import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_business as support


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.fx=support.BusinessTests(); self.fx.setUp(); self.addCleanup(self.fx.doCleanups)

    def run_agent(self,cal='C_EMPTY',mid='M01',model=None,max_steps=24):
        self.fx.ready(cal); self.fx.task(mid)
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        return agent.run_agent(self.fx.store,'T1',model or OfflineMock(),Path(self.fx.tmp.name)/'trace.jsonl',max_steps=max_steps)

    def test_batch_failure_keeps_completed_items(self):
        from schedule_agent.agent import run_agent
        from schedule_agent.model import OfflineMock
        from runtime_helpers import database
        self.fx.ready()
        self.fx.write('task.create',{'task_id':'T1','items':[
            {'material_id':'M01','requested_time':'2026-10-28T10:00:00Z'},
            {'material_id':'M03','requested_time':'2026-10-28T11:00:00Z'},
            {'material_id':'M08','requested_time':'2026-10-28T12:00:00Z'}]},'batch')
        with database(self.fx.store.path) as c:
            c.execute("CREATE TRIGGER fail_item BEFORE INSERT ON drafts WHEN NEW.material_id='M08' BEGIN SELECT RAISE(ABORT,'injected'); END")
        result=run_agent(self.fx.store,'T1',OfflineMock(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['task_status'],'partial')
        self.assertEqual([i['item_status'] for i in result['items']],['completed','needs_information','save_failed'])

    def test_model_can_request_missing_information_after_read_without_rule_check(self):
        captured=[]
        class ReadThenStop:
            mode='test_double'
            def next_action(self,context):
                if not context['observations']:
                    return dict(action='read_material',arguments={'material_id':'M03','view':'primary'},reason='Read')
                captured.append(context['observations'][0].get('reason'))
                return dict(action='finish',arguments={},reason='缺资料且无补充，请提供文案及授权。')
        result=self.run_agent(mid='M03',model=ReadThenStop())
        item=result['items'][0]
        self.assertEqual(item['item_status'],'needs_information')
        self.assertEqual({p['field'] for p in item['pending_items']},{'caption','rights_status'})
        self.assertEqual(item['findings'],[])  # Do not invent rule findings never checked.
        self.assertEqual(item['save_status'],'not_attempted')
        self.assertEqual(captured,['Read'])

    def test_transient_retry_reuses_exact_request(self):
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        from schedule_agent.contracts import error_response,RequestError
        self.fx.ready(); self.fx.task(); seen=[]; real=agent.handle_request
        class RetryMock(OfflineMock):
            def next_action(self,ctx):
                obs=ctx['observations']
                if obs and obs[-1]['action']=='save_draft' and obs[-1]['result']['status']=='failed':
                    return dict(action='save_draft',arguments=obs[-1]['arguments'],reason='Retry the failed write.')
                return super().next_action(ctx)
        def injected(raw,store,**kwargs):
            seen.append(json.loads(raw))
            if len(seen)==1: return error_response(RequestError('STORAGE_BUSY','injected',retryable=True))
            return real(raw,store,**kwargs)
        with patch.object(agent,'handle_request',side_effect=injected):
            result=agent.run_agent(self.fx.store,'T1',RetryMock(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['task_status'],'completed')
        self.assertEqual(seen[0],seen[1])

    def test_mock_normal_and_conflict_are_observation_dependent(self):
        result=self.run_agent(cal='C_18')
        self.assertEqual(result['task_status'],'incomplete')
        self.assertEqual(result['items'][0]['item_status'],'pending_confirmation')
        self.assertEqual(result['items'][0]['suggested_slot']['slot_id'],'S19')
        records=[json.loads(line) for line in (Path(self.fx.tmp.name)/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[0]['model_mode'],'offline_mock')
        self.assertTrue(any(r.get('event')=='tool_result' and r['action']=='query_slots' and r['result']['data']['requested_status']=='occupied' for r in records))

    def test_supplement_observation_causes_second_read_and_rule_check(self):
        result=self.run_agent(mid='M02'); self.assertEqual(result['task_status'],'completed')
        rows=[json.loads(x) for x in (Path(self.fx.tmp.name)/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
        reads=[r['arguments']['view'] for r in rows if r.get('event')=='tool_call' and r['action']=='read_material']
        self.assertEqual(reads,['primary','complete'])
        self.assertEqual(result['items'][0]['draft']['review_basis']['read_result']['provenance']['rights_status'],['SUP.M02'])

    def test_missing_material_and_step_limit_cannot_claim_success(self):
        result=self.run_agent(mid='M03',max_steps=1)
        self.assertEqual(result['stop_reason'],'step_limit')
        self.assertEqual(result['items'][0]['item_status'],'incomplete')
        self.assertEqual(result['items'][0]['save_status'],'not_attempted')

    def test_model_cannot_confirm_or_save_another_task(self):
        class Rogue:
            mode='test_double'
            def next_action(self,context):
                return dict(action='draft.confirm',arguments={},reason='attempt bypass')
        result=self.run_agent(model=Rogue())
        self.assertEqual(result['items'][0]['item_status'],'incomplete')
        self.assertIn('MODEL_PROTOCOL_ERROR',result['items'][0]['reason_codes'])
        from schedule_agent.business import read_drafts
        self.assertEqual(read_drafts(self.fx.store,'T1')['data']['drafts'],[])

    def test_read_failure_does_not_turn_into_passed_review(self):
        import schedule_agent.agent as agent
        failure=dict(ok=False,data=None,error=dict(code='TOOL_UNAVAILABLE',message='injected',field=None,retryable=True))
        with patch.object(agent,'read_tool',return_value=failure):
            result=self.run_agent()
        self.assertEqual(result['items'][0]['review_status'],'not_checked')
        self.assertEqual(result['items'][0]['save_status'],'not_attempted')
        self.assertEqual(result['task_status'],'incomplete')

    def test_unknown_stops_and_preserves_original_request(self):
        import schedule_agent.agent as agent
        self.fx.ready(); self.fx.task()
        from schedule_agent.model import OfflineMock
        def lost(conn): conn.commit(); raise OSError('injected')
        trace=Path(self.fx.tmp.name)/'trace.jsonl'
        with patch.object(self.fx.store,'commit',side_effect=lost), patch.object(self.fx.store,'recover',side_effect=OSError('injected')):
            result=agent.run_agent(self.fx.store,'T1',OfflineMock(),trace)
        self.assertEqual(result['items'][0]['save_status'],'unknown')
        self.assertNotEqual(result['items'][0]['item_status'],'completed')
        rows=[json.loads(x) for x in trace.read_text(encoding='utf-8').splitlines()]
        original=next(r['request'] for r in rows if r.get('event')=='write_request')
        from schedule_agent.runtime import handle_request
        recovered=handle_request(json.dumps(original),self.fx.store,channel='model')
        self.assertEqual(recovered['status'],'succeeded')

    def test_trace_cannot_overwrite_previous_evidence(self):
        from schedule_agent.agent import run_agent
        from schedule_agent.model import OfflineMock
        self.fx.ready(); self.fx.task(); trace=Path(self.fx.tmp.name)/'trace.jsonl'; trace.write_text('KEEP',encoding='utf-8')
        from schedule_agent.contracts import RequestError
        with self.assertRaises(RequestError): run_agent(self.fx.store,'T1',OfflineMock(),trace)
        self.assertEqual(trace.read_text(encoding='utf-8'),'KEEP')

    def test_final_refresh_includes_confirmation_of_an_earlier_item(self):
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        self.fx.ready('C_18')
        self.fx.write('task.create',{'task_id':'T1','items':[
            {'material_id':'M01','requested_time':'2026-10-28T10:00:00Z'},
            {'material_id':'M08','requested_time':'2026-10-28T12:00:00Z'}]},'batch')
        fixture=self.fx
        class UserConfirmsBetweenItems(OfflineMock):
            def next_action(self,ctx):
                if ctx['item']['material_id']=='M08' and not ctx['observations']:
                    d=agent.read_drafts(fixture.store,'T1')['data']['drafts'][0]
                    confirmed=fixture.write('draft.confirm',fixture.confirmation(d),'human-confirm',channel='user')
                    if confirmed['status']!='succeeded': raise AssertionError(confirmed)
                return super().next_action(ctx)
        result=agent.run_agent(self.fx.store,'T1',UserConfirmsBetweenItems(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['task_status'],'completed')
        self.assertEqual(result['items'][0]['draft']['version'],2)
        self.assertEqual(result['items'][0]['pending_items'],[])

    def test_cross_task_tool_args_are_rejected(self):
        class Rogue:
            mode='test_double'
            def next_action(self,ctx):
                return dict(action='query_slots',arguments=dict(task_id='OTHER',material_id='M01'),reason='attempt bypass')
        result=self.run_agent(model=Rogue())
        self.assertEqual(result['items'][0]['reason_codes'],['MODEL_PROTOCOL_ERROR'])

    def test_final_refresh_uses_latest_pending_suggestion(self):
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        self.fx.ready('C_18')
        self.fx.write('task.create',{'task_id':'T1','items':[
            {'material_id':'M01','requested_time':'2026-10-28T10:00:00Z'},
            {'material_id':'M08','requested_time':'2026-10-28T12:00:00Z'}]},'batch')
        fixture=self.fx
        class ReviseBetweenItems(OfflineMock):
            def next_action(self,ctx):
                if ctx['item']['material_id']=='M08' and not ctx['observations']:
                    d=agent.read_drafts(fixture.store,'T1')['data']['drafts'][0]
                    fixture.save(slot='S21',version=d['version'],key='concurrent-revision')
                return super().next_action(ctx)
        result=agent.run_agent(self.fx.store,'T1',ReviseBetweenItems(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['items'][0]['draft']['suggested_slot_id'],'S21')
        self.assertEqual(result['items'][0]['suggested_slot']['slot_id'],'S21')

    def test_trace_write_failure_before_save_has_no_business_effect(self):
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        from schedule_agent.contracts import RequestError
        self.fx.ready(); self.fx.task(); original=agent.Journal.record
        def fail_before(journal,event,**kwargs):
            if event=='write_request': raise OSError('injected trace fault before save')
            return original(journal,event,**kwargs)
        with patch.object(agent.Journal,'record',new=fail_before):
            result=agent.run_agent(self.fx.store,'T1',OfflineMock(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['task_status'],'incomplete')
        self.assertEqual(result['stop_reason'],'trace_error')
        self.assertEqual(agent.read_drafts(self.fx.store,'T1')['data']['drafts'],[])

    def test_trace_failure_after_commit_does_not_report_save_failure(self):
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        self.fx.ready(); self.fx.task(); original=agent.Journal.record
        def fail_after(journal,event,**kwargs):
            if event=='tool_result' and kwargs['action']=='save_draft': raise OSError('injected trace fault after save')
            return original(journal,event,**kwargs)
        with patch.object(agent.Journal,'record',new=fail_after):
            result=agent.run_agent(self.fx.store,'T1',OfflineMock(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['items'][0]['save_status'],'saved')
        self.assertEqual(result['items'][0]['item_status'],'completed')
        self.assertEqual(result['stop_reason'],'trace_error')

    def test_trace_failure_mid_batch_preserves_successful_item_in_output(self):
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        self.fx.ready()
        self.fx.write('task.create',{'task_id':'T1','items':[
            {'material_id':'M01','requested_time':'2026-10-28T10:00:00Z'},
            {'material_id':'M08','requested_time':'2026-10-28T12:00:00Z'}]},'batch')
        original=agent.Journal.record
        def fail_second(journal,event,**kwargs):
            if event=='write_request' and kwargs['request']['payload']['material_id']=='M08': raise OSError('disk full')
            return original(journal,event,**kwargs)
        with patch.object(agent.Journal,'record',new=fail_second):
            result=agent.run_agent(self.fx.store,'T1',OfflineMock(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['task_status'],'partial')
        self.assertEqual(result['items'][0]['item_status'],'completed')
        self.assertEqual(result['items'][1]['save_status'],'not_attempted')
        self.assertEqual(result['items'][1]['item_status'],'incomplete')

    def test_failed_final_query_does_not_claim_cached_confirmation(self):
        import schedule_agent.agent as agent
        from schedule_agent.model import OfflineMock
        from schedule_agent.contracts import RequestError,error_response
        self.fx.ready(); self.fx.task(); self.fx.save(); original=agent.read_drafts; calls=[]
        def query(store,task):
            calls.append(1)
            if len(calls)>1: return error_response(RequestError('STORAGE_ERROR','injected'))
            return original(store,task)
        with patch.object(agent,'read_drafts',side_effect=query):
            result=agent.run_agent(self.fx.store,'T1',OfflineMock(),Path(self.fx.tmp.name)/'trace.jsonl')
        self.assertEqual(result['task_status'],'incomplete')
        self.assertIn('STORAGE_ERROR',result['items'][0]['reason_codes'])


if __name__=='__main__': unittest.main()
