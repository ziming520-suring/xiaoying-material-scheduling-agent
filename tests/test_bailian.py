import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from schedule_agent.bailian import BailianModel, BudgetLedger
from schedule_agent.contracts import RequestError


class BailianTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'budget.sqlite3'
        self.context = dict(task_id='T1', item={'material_id':'M01'}, observations=[], current_draft=None, remaining_steps=8)

    def model(self, transport):
        return BailianModel(api_key='test-secret', budget=BudgetLedger(self.path), transport=transport)

    def response(self, name='read_material', args=None):
        return {'id':'response-1', 'usage':{'prompt_tokens':100,'completion_tokens':40}, 'choices':[{
            'finish_reason':'tool_calls', 'message':{'tool_calls':[{'function':{'name':name,
            'arguments':json.dumps(args or {'material_id':'M01','view':'primary','reason':'读取基础资料'})}}]}}]}

    def test_real_protocol_and_usage_without_secret_or_reasoning(self):
        seen=[]
        def transport(request, timeout):
            seen.append(json.loads(request.data))
            return self.response()
        model=self.model(transport)
        action=model.next_action(self.context)
        self.assertEqual(action['action'],'read_material')
        self.assertEqual(action['arguments'],{'material_id':'M01','view':'primary'})
        self.assertFalse(seen[0]['enable_thinking'])
        self.assertEqual(seen[0]['max_tokens'],2048)
        self.assertEqual({t['function']['name'] for t in seen[0]['tools']},
                         {'read_material','check_rules','query_slots','save_draft','finish'})
        self.assertEqual(model.budget.summary()['accounted_yuan'],'0.000220')
        self.assertNotIn('test-secret',json.dumps(model.budget.summary()))

    def test_budget_survives_reopening_and_failed_call_is_not_refunded(self):
        def fail(*args,**kwargs): raise TimeoutError('test-secret')
        with self.assertRaises(RequestError) as caught: self.model(fail).next_action(self.context)
        self.assertEqual(caught.exception.code,'MODEL_UNAVAILABLE')
        ledger=BudgetLedger(self.path)
        for _ in range(99): ledger.reserve()
        with self.assertRaises(RequestError) as caught: ledger.reserve()
        self.assertEqual(caught.exception.code,'MODEL_BUDGET_EXCEEDED')
        self.assertEqual(ledger.summary()['reserved_yuan'],'10.000000')

    def test_oversized_context_makes_no_request_and_no_reservation(self):
        model=self.model(lambda *a,**k: self.fail('must not call'))
        with self.assertRaises(RequestError): model.next_action(dict(self.context, oversized='中'*60000))
        self.assertEqual(model.budget.summary()['calls'],0)

    def test_http_error_is_sanitized_and_not_retried(self):
        count=[]
        def fail(*a,**k):
            count.append(1)
            raise HTTPError('https://example.invalid',401,'test-secret',{},io.BytesIO(b'test-secret'))
        with self.assertRaises(RequestError) as caught: self.model(fail).next_action(self.context)
        self.assertEqual(caught.exception.code,'MODEL_AUTH_ERROR')
        self.assertNotIn('test-secret',str(caught.exception))
        self.assertEqual(len(count),1)

    def test_malformed_multiple_and_truncated_calls_rejected(self):
        for kind in ('multiple','truncated','unknown','bad_arguments'):
            with self.subTest(kind=kind):
                result=self.response()
                if kind=='multiple': result['choices'][0]['message']['tool_calls'] *= 2
                if kind=='truncated': result['choices'][0]['finish_reason']='length'
                if kind=='unknown': result['choices'][0]['message']['tool_calls'][0]['function']['name']='confirm'
                if kind=='bad_arguments': result['choices'][0]['message']['tool_calls'][0]['function']['arguments']='oops'
                with self.assertRaises(RequestError): self.model(lambda *a,**k:result).next_action(self.context)

    def test_protocol_diagnostic_is_structural_and_does_not_echo_provider_text(self):
        result=self.response()
        result['choices'][0]['finish_reason']='length'
        result['choices'][0]['message']['content']='test-secret'
        model=self.model(lambda *a,**k:result)
        with self.assertRaises(RequestError): model.next_action(self.context)
        self.assertEqual(model.last_diagnostic,'invalid_finish_reason')

    def test_untrusted_endpoint_and_empty_key_rejected(self):
        for options in ({'api_key':''},{'api_key':'x','base_url':'http://evil.invalid/v1'},
                        {'api_key':'x','base_url':'https://evil.invalid/compatible-mode/v1'}):
            with self.assertRaises(RequestError): BailianModel(budget=BudgetLedger(self.path),**options)

    def test_model_failure_has_explicit_agent_reason(self):
        import test_business as support
        from schedule_agent.agent import run_agent
        fx=support.BusinessTests(); fx.setUp(); self.addCleanup(fx.doCleanups)
        fx.ready(); fx.task()
        class Failed:
            mode='bailian'
            def next_action(self,ctx): raise RequestError('MODEL_BUDGET_EXCEEDED','Budget exhausted.')
        result=run_agent(fx.store,'T1',Failed(),Path(self.tmp.name)/'trace.jsonl')
        self.assertIn('MODEL_BUDGET_EXCEEDED',result['items'][0]['reason_codes'])
        self.assertEqual(result['items'][0]['save_status'],'not_attempted')

    def test_concurrent_reservations_do_not_exceed_remaining_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        ledger=BudgetLedger(self.path)
        for _ in range(99): ledger.reserve()
        def attempt(_):
            try: return BudgetLedger(self.path).reserve()
            except RequestError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(attempt,range(4)))
        self.assertEqual(sum(type(r) is int for r in results),1)
        self.assertEqual(results.count('MODEL_BUDGET_EXCEEDED'),3)
        self.assertEqual(ledger.summary()['calls'],100)

    def test_observations_are_native_tool_messages_not_a_new_task(self):
        captured=[]
        result={'ok':True,'data':{'review_status':'needs_information','missing_fields':['caption']},'error':None}
        context=dict(self.context,observations=[{'action':'check_rules','arguments':{'material_id':'M01','material_ref':'mat.'+'a'*64},'reason':'检查已读取资料','result':result}])
        def transport(request,timeout):
            captured.append(json.loads(request.data))
            return self.response('finish',{'reason':'缺资料，停止并请求补充'})
        self.model(transport).next_action(context)
        messages=captured[0]['messages']
        tool_messages=[m for m in messages if m['role']=='tool']
        self.assertEqual(len(tool_messages),1)
        self.assertEqual(json.loads(tool_messages[0]['content']),result)
        prior=next(m for m in messages if m['role']=='assistant')
        self.assertEqual(json.loads(prior['tool_calls'][0]['function']['arguments'])['reason'],'检查已读取资料')
        self.assertEqual(prior['tool_calls'][0]['id'],tool_messages[0]['tool_call_id'])
        self.assertNotIn('observations',json.loads(messages[1]['content']))

    def test_cli_missing_key_returns_json_without_creating_business_database(self):
        import os
        import subprocess
        import sys
        root=Path(__file__).resolve().parents[1]
        db=Path(self.tmp.name)/'absent.sqlite3'
        env=dict(os.environ,DASHSCOPE_API_KEY='',PYTHONPATH=str(root/'src'))
        proc=subprocess.run([sys.executable,'-X','utf8','-m','schedule_agent','agent','--db',str(db),
                            '--task-id','T1','--model','bailian','--trace',str(Path(self.tmp.name)/'t.jsonl')],
                            env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(proc.returncode,2)
        self.assertEqual(json.loads(proc.stdout)['error']['code'],'MODEL_CONFIG_ERROR')
        self.assertFalse(db.exists())

