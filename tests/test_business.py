"""Business tests use real SQLite; no model calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import sqlite3
from unittest.mock import patch

from runtime_helpers import raw, snapshot, database
from schedule_agent.runtime import handle_request
from schedule_agent.storage import Store

DATA = Path(__file__).resolve().parents[1] / 'data'


def request(op, payload, key='request-1'):
    return json.dumps(dict(schema_version=1, request_id=key, operation=op, payload=payload))


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'business.sqlite3')
        self.initial = handle_request(raw('system.initialize', 'init', '2026-10-28T09:00:00Z'), self.store)

    def write(self, op, payload, key='request-1', **kwargs):
        return handle_request(request(op, payload, key), self.store, **kwargs)

    def ready(self, cal='C_EMPTY'):
        upgrade = self.write('schema.upgrade', {'target_storage_version': 2}, 'upgrade')
        self.assertEqual(upgrade['status'], 'succeeded', upgrade)
        loaded = self.write('fixture.load', {'dataset_id':'synthetic-campus-v1',
            'manifest_sha256':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(),
            'calendar_id':cal}, 'load')
        self.assertEqual(loaded['status'], 'succeeded', loaded)

    def tool(self, name, **args):
        from schedule_agent.business import read_tool
        return read_tool(self.store, name, args)

    def task(self, mid='M01', task='T1', time='2026-10-28T18:00:00+08:00'):
        result = self.write('task.create', {'task_id':task,'items':[{'material_id':mid,'requested_time':time}]}, task)
        self.assertEqual(result['status'], 'succeeded', result)
        return result

    def proposal(self,mid='M01',task='T1',slot='S18',version=None):
        m=self.tool('read_material',material_id=mid,view='complete')['data']
        c=self.tool('check_rules',material_id=mid,material_ref=m['material_ref'])['data']
        return dict(task_id=task,material_id=mid,suggested_slot_id=slot,material_ref=m['material_ref'],check_ref=c['check_ref'],expected_draft_version=version)

    def save(self,mid='M01',task='T1',slot='S18',version=None,key='save'):
        return self.write('draft.save',self.proposal(mid,task,slot,version),key)

    def confirmation(self,d):
        return dict(draft_id=d['draft_id'],expected_draft_version=d['version'],accepted_slot_id=d['suggested_slot_id'],accepted_risk_ids=d['required_risk_ids'])

    def test_normal_save_replay_business_uniqueness_and_snapshot(self):
        self.ready(); self.task(); p=self.proposal()
        first=self.write('draft.save',p,'save'); self.assertEqual(first['status'],'succeeded',first)
        draft=first['data']['draft']; self.assertEqual(draft['status'],'confirmed'); self.assertTrue(draft['occupies_slot'])
        self.assertEqual(draft['authorization_basis'],'original_request')
        self.assertEqual(self.write('draft.save',p,'save'),first)
        self.assertEqual(self.write('draft.save',p,'new-key')['data']['draft'],draft)
        with database(self.store.path) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM drafts').fetchone()[0],1)
        self.assertEqual(self.tool('query_slots',task_id='T1',material_id='M01')['data']['requested_status'],'occupied')

    def test_risk_and_conflict_pending_then_exact_confirmation(self):
        self.ready('C_18'); self.task('M05')
        saved=self.save('M05',slot='S19'); self.assertEqual(saved['status'],'succeeded',saved)
        d=saved['data']['draft']; self.assertFalse(d['occupies_slot']); self.assertEqual(d['required_risk_ids'],['R004'])
        p=self.confirmation(d); before=snapshot(self.store.path)
        self.assertEqual(self.write('draft.confirm',p,'confirm')['error']['code'],'CONFIRMATION_CHANNEL_REQUIRED')
        self.assertEqual(snapshot(self.store.path),before)
        bad=dict(p,accepted_risk_ids=[])
        self.assertEqual(self.write('draft.confirm',bad,'bad',channel='user')['error']['code'],'CONFIRMATION_MISMATCH')
        self.assertEqual(snapshot(self.store.path),before)
        good=self.write('draft.confirm',p,'confirm',channel='user'); self.assertEqual(good['status'],'succeeded',good)
        self.assertEqual(good['data']['draft']['version'],d['version']+1)
        self.assertEqual(good['data']['draft']['review_status'],'needs_confirmation')
        self.assertEqual(self.write('draft.confirm',p,'confirm',channel='user'),good)
        self.assertEqual(self.write('draft.confirm',p,'another',channel='user')['error']['code'],'DRAFT_FINALIZED')

    def test_confirmation_race_and_new_key_pending_recheck(self):
        self.ready('C_18'); self.task(); p=self.proposal(slot='S19')
        saved=self.write('draft.save',p,'save'); self.assertEqual(saved['status'],'succeeded',saved)
        d=saved['data']['draft']
        self.write('calendar.replace',{'expected_revision':1,'existing':[{'schedule_id':'EX18','slot_id':'S18'},{'schedule_id':'RACE','slot_id':'S19'}]},'race',channel='test')
        before=snapshot(self.store.path)
        self.assertEqual(self.write('draft.confirm',self.confirmation(d),'confirm',channel='user')['error']['code'],'SLOT_CONFLICT')
        self.assertEqual(self.write('draft.save',p,'new')['error']['code'],'SLOT_CONFLICT')
        self.assertEqual(self.write('draft.save',p,'save'),saved)
        self.assertEqual(before,snapshot(self.store.path))

    def test_update_pending_invalidates_old_version_and_restores_original(self):
        self.ready('C_18'); self.task(); a=self.save(slot='S19')
        self.assertEqual(a['status'],'succeeded',a); d=a['data']['draft']
        b=self.save(slot='S20',version=d['version'],key='revise')['data']['draft']
        self.assertEqual(b['draft_id'],d['draft_id']); self.assertEqual(b['version'],d['version']+1)
        self.assertEqual(self.write('draft.confirm',self.confirmation(d),'old',channel='user')['error']['code'],'DRAFT_VERSION_CONFLICT')
        self.write('calendar.replace',{'expected_revision':1,'existing':[{'schedule_id':'EX19','slot_id':'S19'}]},'replace',channel='test')
        c=self.save(slot='S18',version=b['version'],key='original')['data']['draft']
        self.assertEqual(c['status'],'confirmed'); self.assertEqual(c['version'],b['version']+1)
        self.assertEqual(self.save(slot='S20',version=c['version'],key='forbidden')['error']['code'],'DRAFT_FINALIZED')

    def test_blocked_missing_and_forged_refs_never_save(self):
        self.ready()
        for mid,code in [('M03','MATERIAL_INCOMPLETE'),('M04','REVIEW_BLOCKED'),('M06','REVIEW_BLOCKED')]:
            self.task(mid,task=mid); before=snapshot(self.store.path)
            self.assertEqual(self.save(mid,task=mid,key=mid)['error']['code'],code)
            self.assertEqual(before,snapshot(self.store.path))
        self.task(); p=self.proposal(); p['check_ref']='check.'+'0'*64
        before=snapshot(self.store.path)
        self.assertEqual(self.write('draft.save',p,'forged')['error']['code'],'STALE_RULE_CHECK')
        self.assertEqual(before,snapshot(self.store.path))

    def test_failed_save_rolls_back_draft_occupancy_and_replay(self):
        self.ready(); self.task(); p=self.proposal()
        with database(self.store.path) as c:
            c.execute("CREATE TRIGGER fail_save BEFORE INSERT ON replay_results WHEN NEW.operation='draft.save' BEGIN SELECT RAISE(ABORT,'injected'); END")
        before=snapshot(self.store.path)
        self.assertEqual(self.write('draft.save',p,'save')['error']['code'],'STORAGE_ERROR')
        self.assertEqual(before,snapshot(self.store.path))
        with database(self.store.path) as c: c.execute('DROP TRIGGER fail_save')
        first=self.write('draft.save',p,'save'); self.assertEqual(first['status'],'succeeded',first)
        self.assertEqual(self.write('draft.save',p,'save'),first)

    def test_unknown_after_commit_recovers_with_original_key(self):
        self.ready(); self.task(); p=self.proposal()
        def lost(conn): conn.commit(); raise OSError('injected response loss')
        with patch.object(self.store,'commit',side_effect=lost), patch.object(self.store,'recover',side_effect=OSError('injected read loss')):
            result=self.write('draft.save',p,'save')
        self.assertEqual(result['status'],'unknown',result)
        recovered=self.write('draft.save',p,'save'); self.assertEqual(recovered['status'],'succeeded',recovered)
        with database(self.store.path) as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM drafts').fetchone()[0],1)

    def test_migration_rollback_is_atomic(self):
        with database(self.store.path) as c:
            c.execute("CREATE TRIGGER fail_upgrade BEFORE UPDATE ON runtime_meta BEGIN SELECT RAISE(ABORT,'injected'); END")
        before=snapshot(self.store.path)
        self.assertEqual(self.write('schema.upgrade',{'target_storage_version':2},'up')['error']['code'],'STORAGE_ERROR')
        self.assertEqual(snapshot(self.store.path),before)

    def test_time_advance_keeps_pending_but_expired_confirmation_rejected(self):
        self.ready('C_18'); self.task(); saved=self.save(slot='S19'); self.assertEqual(saved['status'],'succeeded',saved)
        d=saved['data']['draft']
        handle_request(raw(time='2026-10-28T11:00:00Z'),self.store)
        before=snapshot(self.store.path)
        self.assertEqual(self.write('draft.confirm',self.confirmation(d),'confirm',channel='user')['error']['code'],'INVALID_PUBLISH_TIME')
        self.assertEqual(before,snapshot(self.store.path))

    def test_historical_snapshot_survives_current_material_change(self):
        self.ready(); self.task(); saved=self.save(); self.assertEqual(saved['status'],'succeeded',saved)
        d=saved['data']['draft']
        with database(self.store.path) as c:
            m=json.loads(c.execute("SELECT record_json FROM materials WHERE material_id='M01'").fetchone()[0]); m['version']+=1; m['caption']='新资料'
            c.execute("UPDATE materials SET record_json=? WHERE material_id='M01'",(json.dumps(m,ensure_ascii=False),))
        from schedule_agent.business import read_drafts
        result=read_drafts(self.store,'T1'); self.assertEqual(result['data']['drafts'][0],d)
        self.assertNotEqual(d['review_basis']['primary']['caption'],m['caption'])

    def test_two_processes_cannot_claim_same_slot(self):
        self.ready(); self.task(); self.task('M08','T2')
        writes=[request('draft.save',self.proposal(),'save1'),request('draft.save',self.proposal('M08','T2'),'save2')]
        worker=Path(__file__).with_name('process_worker.py')
        processes=[subprocess.Popen([sys.executable,'-X','utf8',str(worker),str(self.store.path),r],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8') for r in writes]
        try:
            for proc in processes: self.assertEqual(proc.stdout.readline().strip(),'ready')
            for proc in processes: proc.stdin.write('\n'); proc.stdin.flush()
            results=[json.loads(proc.stdout.readline()) for proc in processes]
            self.assertEqual(sorted(r['status'] for r in results),['failed','succeeded'])
            self.assertEqual(next(r for r in results if r['status']=='failed')['error']['code'],'SLOT_CONFLICT')
        finally:
            for proc in processes:
                proc.communicate(timeout=10)

    def test_upgrade_retains_clock_and_original_replay(self):
        self.ready()
        self.assertEqual(handle_request(raw('system.initialize','init','2026-10-28T09:00:00Z'), self.store), self.initial)
        with database(self.store.path) as c:
            self.assertEqual(c.execute('SELECT storage_schema_version FROM runtime_meta').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT current_time_utc FROM logical_clock').fetchone()[0],'2026-10-28T09:00:00Z')

    def test_confirmation_replay_binds_nested_request_identity(self):
        self.ready('C_18'); self.task(); d=self.save(slot='S19')['data']['draft']; p=self.confirmation(d)
        self.assertEqual(self.write('draft.confirm',p,'confirm',channel='user')['status'],'succeeded')
        with database(self.store.path) as c:
            row=c.execute("SELECT response_json FROM replay_results WHERE operation='draft.confirm'").fetchone()
            response=json.loads(row[0]); response['data']['confirmation']['request_id']='different-operation-key'
            c.execute("UPDATE replay_results SET response_json=? WHERE operation='draft.confirm'",(json.dumps(response),))
        before=snapshot(self.store.path)
        self.assertEqual(self.write('draft.confirm',p,'confirm',channel='user')['error']['code'],'STORAGE_ERROR')
        self.assertEqual(before,snapshot(self.store.path))

    def test_current_confirmed_draft_requires_confirmation_audit(self):
        self.ready('C_18'); self.task(); d=self.save(slot='S19')['data']['draft']
        self.write('draft.confirm',self.confirmation(d),'confirm',channel='user')
        with database(self.store.path) as c: c.execute('DELETE FROM confirmations')
        from schedule_agent.business import read_drafts
        self.assertEqual(read_drafts(self.store,'T1')['error']['code'],'STORAGE_ERROR')

    def test_current_confirmed_draft_rejects_cross_table_double_occupancy(self):
        self.ready(); self.task(); self.save()
        with database(self.store.path) as c: c.execute("INSERT INTO existing_schedules (schedule_id,slot_id) VALUES ('BROKEN','S18')")
        from schedule_agent.business import read_drafts
        self.assertEqual(read_drafts(self.store,'T1')['error']['code'],'STORAGE_ERROR')

    def test_deep_invalid_business_json_is_error_not_crash(self):
        raw_json='{"schema_version":1,"request_id":"deep","operation":"task.create","payload":{"task_id":"T1","items":'+('['*600)+'0'+(']'*600)+'}}'
        before=snapshot(self.store.path)
        self.assertEqual(handle_request(raw_json,self.store)['error']['code'],'INVALID_REQUEST')
        self.assertEqual(before,snapshot(self.store.path))

    def test_channel_rejection_preserves_validated_request_identity(self):
        self.ready('C_18'); self.task(); d=self.save(slot='S19')['data']['draft']
        denied=self.write('draft.confirm',self.confirmation(d),'human-only')
        self.assertEqual(denied['error']['code'],'CONFIRMATION_CHANNEL_REQUIRED')
        self.assertEqual(denied['request_id'],'human-only')
        self.assertEqual(denied['operation'],'draft.confirm')

    def test_invalid_business_payload_leaves_database_unchanged(self):
        before=snapshot(self.store.path)
        result=self.write('task.create', {'task_id':'T1','items':[{'requested_time':'18:00'}]})
        self.assertEqual(result['error']['code'],'INVALID_REQUEST')
        self.assertEqual(before,snapshot(self.store.path))

    def test_load_replay_and_reject_second_load(self):
        self.ready()
        payload={'dataset_id':'synthetic-campus-v1','manifest_sha256':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(),'calendar_id':'C_EMPTY'}
        before=snapshot(self.store.path)
        self.assertEqual(self.write('fixture.load',payload,'load')['status'],'succeeded')
        self.assertEqual(self.write('fixture.load',payload,'other')['error']['code'],'FIXTURE_ALREADY_LOADED')
        self.assertEqual(before,snapshot(self.store.path))

    def test_supplement_recheck_and_unresolvable_missing(self):
        self.ready()
        primary=self.tool('read_material',material_id='M02',view='primary')['data']
        complete=self.tool('read_material',material_id='M02',view='complete')['data']
        self.assertEqual(primary['effective']['rights_status'],'unknown')
        self.assertEqual(complete['effective']['rights_status'],'authorized')
        self.assertEqual(complete['provenance']['rights_status'],['SUP.M02'])
        self.assertEqual(self.tool('check_rules',material_id='M02',material_ref=primary['material_ref'])['data']['review_status'],'needs_information')
        self.assertEqual(self.tool('check_rules',material_id='M02',material_ref=complete['material_ref'])['data']['review_status'],'passed')
        missing=self.tool('read_material',material_id='M03',view='complete')['data']
        self.assertEqual(missing['missing_fields'],['caption','rights_status'])

    def test_risk_precedence_and_conflicts_in_both_views(self):
        self.ready()
        for mid,expected in [('M04',{'R002','R003'}),('M07',{'R001','R003','R004'}),('M06',{'R005'})]:
            for view in ['primary','complete']:
                material=self.tool('read_material',material_id=mid,view=view)['data']
                checked=self.tool('check_rules',material_id=mid,material_ref=material['material_ref'])['data']
                self.assertEqual(checked['review_status'],'blocked')
                self.assertEqual({f['rule_id'] for f in checked['findings']},expected)

    def test_slot_query_and_hard_time_task_identity(self):
        self.ready('C_18'); self.task()
        slots=self.tool('query_slots',task_id='T1',material_id='M01')['data']
        self.assertEqual(slots['requested_status'],'occupied')
        self.assertEqual([s['slot_id'] for s in slots['alternatives']],['S19','S20','S21'])
        self.assertEqual(self.write('task.create',{'task_id':'T1','items':[{'material_id':'M01','requested_time':'2026-10-28T11:00:00Z'}]},'changed')['error']['code'],'TASK_CONFLICT')


if __name__=='__main__': unittest.main()
