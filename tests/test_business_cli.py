import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_business as support
from test_business import request


class BusinessCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture=support.BusinessTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.ready('C_18'); self.fixture.task()
        self.store=self.fixture.store

    def cli(self,*args):
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
        proc=subprocess.run([sys.executable,'-X','utf8','-m','schedule_agent',*args],env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        return proc.returncode,json.loads(proc.stdout)

    def test_independent_confirm_cli_and_current_drafts(self):
        d=self.fixture.save(slot='S19')['data']['draft']
        path=Path(self.fixture.tmp.name)/'confirm.json'
        path.write_text(request('draft.confirm',self.fixture.confirmation(d),'confirm'),encoding='utf-8')
        code,denied=self.cli('run','--db',str(self.store.path),'--input',str(path))
        self.assertEqual(code,2); self.assertEqual(denied['error']['code'],'CONFIRMATION_CHANNEL_REQUIRED')
        code,confirmed=self.cli('confirm','--db',str(self.store.path),'--input',str(path))
        self.assertEqual(code,0,confirmed); self.assertEqual(confirmed['data']['draft']['status'],'confirmed')
        code,current=self.cli('drafts','--db',str(self.store.path),'--task-id','T1')
        self.assertEqual(code,0,current); self.assertEqual(current['data']['drafts'],[confirmed['data']['draft']])

    def test_confirm_entry_rejects_other_writes(self):
        path=Path(self.fixture.tmp.name)/'wrong.json'
        path.write_text(request('clock.advance',{'target_time':'2026-10-28T12:00:00Z'},'advance'),encoding='utf-8')
        code,result=self.cli('confirm','--db',str(self.store.path),'--input',str(path))
        self.assertEqual(code,2); self.assertEqual(result['error']['code'],'INVALID_REQUEST')

    def test_explicit_mock_agent_cli(self):
        trace=Path(self.fixture.tmp.name)/'offline.jsonl'
        code,result=self.cli('agent','--db',str(self.store.path),'--task-id','T1','--model','mock','--trace',str(trace))
        self.assertEqual(code,2,result)
        self.assertEqual(result['items'][0]['item_status'],'pending_confirmation')
        self.assertEqual(json.loads(trace.read_text(encoding='utf-8').splitlines()[0])['model_mode'],'offline_mock')


if __name__=='__main__': unittest.main()
