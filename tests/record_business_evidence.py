"""Execute all 18 local scenarios. Model decisions are explicitly offline Mock."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from schedule_agent.agent import run_agent
from schedule_agent.business import read_drafts, read_tool
from schedule_agent.contracts import serialize
from schedule_agent.model import OfflineMock
from schedule_agent.runtime import handle_request, read_clock
from schedule_agent.storage import Store
from runtime_helpers import database, snapshot

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
OUT=ROOT/'examples'/'phase3'


def execute_scenario(sid,temp,*,model=None,output=None,max_steps=24):
    out=output or OUT
    out.mkdir(parents=True,exist_ok=True)
    model=model or OfflineMock()
    live=model.mode=='bailian_live'
    scenario=json.loads((DATA/'scenarios'/f'{sid}.json').read_text(encoding='utf-8'))
    store=Store(temp/f'{sid}.sqlite3'); operations=[]; traces=[]; runs=[]
    fault=None; verified=[]
    def checkpoint():
        partial=dict(scenario_id=sid,execution_status='in_progress',real_model_executed=live,
                     model_mode=model.mode,input=scenario['task'],calendar_id=scenario['calendar_id'],
                     fault_injection=fault,agent_runs=runs,explicit_operations=operations,
                     trace_files=[p.name for p in traces])
        (out/f'{sid}-actual.json').write_text(json.dumps(partial,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def write(op,p,key,channel='run'):
        req=dict(schema_version=1,request_id=key,operation=op,payload=p)
        result=handle_request(serialize(req),store,channel=channel)
        operations.append(dict(request=req,channel=channel,response=result))
        checkpoint()
        return result
    def success(response):
        if response['status']!='succeeded': raise AssertionError(response)
    success(write('system.initialize',{'initial_time':'2026-10-28T09:00:00Z'},'init'))
    success(write('schema.upgrade',{'target_storage_version':2},'upgrade'))
    success(write('fixture.load',{'dataset_id':'synthetic-campus-v1','manifest_sha256':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(),'calendar_id':scenario['calendar_id']},'load'))
    success(write('task.create',scenario['task'],'task'))
    def run():
        path=out/f'{sid}-run-{len(runs)+1}.jsonl'
        traces.append(path); checkpoint()
        result=run_agent(store,scenario['task']['task_id'],model,path,max_steps=max_steps)
        runs.append(result); checkpoint(); return result
    def drafts(): return read_drafts(store,scenario['task']['task_id'])['data']['drafts']
    def confirm(d,key,risks=None):
        return write('draft.confirm',dict(draft_id=d['draft_id'],expected_draft_version=d['version'],accepted_slot_id=d['suggested_slot_id'],accepted_risk_ids=d['required_risk_ids'] if risks is None else risks),key,'user')
    def saved_request():
        events=[json.loads(line) for line in traces[0].read_text(encoding='utf-8').splitlines()]
        return next(e['request'] for e in events if e['event']=='write_request')
    if sid in ('S10','S12'):
        condition="NEW.operation='draft.save'" if sid=='S10' else "NEW.operation='draft.save' AND json_extract(NEW.canonical_request_json,'$.payload.material_id')='M08'"
        with database(store.path) as c:
            c.execute("CREATE TRIGGER fault BEFORE INSERT ON replay_results WHEN "+condition+" BEGIN SELECT RAISE(ABORT,'test only'); END")
        fault='SQLite trigger aborts replay INSERT after draft write, inside same transaction.'
    if sid=='S17':
        fault='After real SQLite commit, inject response loss and failed recovery read.'
        checkpoint()
        def lost(conn): conn.commit(); raise OSError('test-only post-commit loss')
        with patch.object(store,'commit',side_effect=lost),patch.object(store,'recover',side_effect=OSError('test-only recovery failure')):
            run()
        assert runs[0]['items'][0]['save_status']=='unknown'
    elif sid=='S18':
        fault='Read tool adapter returns TOOL_UNAVAILABLE for every read attempt.'
        checkpoint()
        failure=dict(ok=False,data=None,error=dict(code='TOOL_UNAVAILABLE',message='test-only read failure',field=None,retryable=True))
        with patch('schedule_agent.agent.read_tool',return_value=failure): run()
    else: run()
    if sid=='S05':
        d=drafts()[0]; assert not d['occupies_slot']; success(confirm(d,'confirm'))
        verified.append('Warning draft occupied only after explicit exact user confirmation.')
    if sid=='S07':
        d=drafts()[0]
        success(write('calendar.replace',{'expected_revision':1,'existing':[{'schedule_id':'EX.S18','slot_id':'S18'},{'schedule_id':'RACE','slot_id':d['suggested_slot_id']}]},'race','test'))
        before=snapshot(store.path); rejected=confirm(d,'confirm')
        assert rejected['error']['code']=='SLOT_CONFLICT' and before==snapshot(store.path)
        verified.append('Confirmation collision rejected without changing state.')
    if sid in ('S09','S10','S17'):
        req=saved_request()
        if sid=='S10':
            assert drafts()==[] and runs[0]['items'][0]['save_status']=='failed'
            with database(store.path) as c: c.execute('DROP TRIGGER fault')
        recovered=write(req['operation'],req['payload'],req['request_id']); success(recovered)
        repeated=write(req['operation'],req['payload'],req['request_id']); assert recovered==repeated
        if sid=='S09':
            newer=write(req['operation'],req['payload'],'another-key'); success(newer)
            assert newer['data']['draft']==recovered['data']['draft']
        verified.append('Original-key recovery/replay returns one draft; repeated response equal.')
    if sid=='S11':
        old=drafts()[0]
        success(write('calendar.replace',{'expected_revision':1,'existing':[{'schedule_id':'EX.S19','slot_id':'S19'}]},'replace','test'))
        run(); new=drafts()[0]
        assert new['draft_id']==old['draft_id'] and new['version']>old['version'] and new['suggested_slot_id']=='S18'
        verified.append('Changed occupancy caused a new query and update of the same draft to original S18.')
    if sid=='S15':
        d=drafts()[0]
        success(write('clock.advance',{'target_time':'2026-10-28T13:00:00Z'},'advance'))
        before=snapshot(store.path); rejected=confirm(d,'confirm')
        assert rejected['error']['code']=='INVALID_PUBLISH_TIME' and before==snapshot(store.path)
        verified.append('Clock advance kept pending draft; non-future confirmation rejected.')
    if sid=='S16':
        old=drafts()[0]; before=snapshot(store.path)
        assert confirm(old,'missing-risk',[])['error']['code']=='CONFIRMATION_MISMATCH'
        assert before==snapshot(store.path)
        # Controlled tool contract test following the observed model suggestion.
        query=read_tool(store,'query_slots',{'task_id':old['task_id'],'material_id':old['material_id']})['data']
        alternative=next(s for s in query['alternatives'] if s['slot_id']!=old['suggested_slot_id'])
        payload={k:old[k] for k in ('task_id','material_id','material_ref','check_ref')}
        payload.update(suggested_slot_id=alternative['slot_id'],expected_draft_version=old['version'])
        success(write('draft.save',payload,'revise'))
        new=drafts()[0]; assert new['version']==old['version']+1
        before=snapshot(store.path)
        assert confirm(old,'stale')['error']['code']=='DRAFT_VERSION_CONFLICT' and before==snapshot(store.path)
        verified.append('Missing risk consent and stale version rejected; controlled revision increments once.')
    final=drafts()
    for expected in scenario['expected']['items']:
        actual=[d for d in final if d['material_id']==expected['material_id']]
        assert len(actual)==expected['draft_count'],(sid,actual,expected)
        if actual:
            assert actual[0]['status']==expected['draft_status']
            assert actual[0]['occupies_slot']==expected['occupies_slot']
            assert actual[0]['suggested_slot_id'] in expected['allowed_suggested_slot_ids']
    verified.append('Final per-material draft count, status, allowed slot and occupancy match scenario projection.')
    if sid=='S12': assert [i['item_status'] for i in runs[0]['items']]==['completed','needs_information','save_failed']
    if sid=='S18': assert runs[0]['items'][0]['review_status']=='not_checked'
    evidence=dict(scenario_id=sid,execution_status='completed',model_mode=model.mode,
        evidence_kind='actual_local_tools_with_live_model' if live else 'actual_local_tools_with_offline_mock',real_model_executed=live,
        input=scenario['task'],calendar_id=scenario['calendar_id'],fault_injection=fault,agent_runs=runs,
        explicit_operations=operations,final_drafts=final,final_clock=read_clock(store),verified_assertions=verified,
        trace_files=[p.name for p in traces],note='Operations after agent_runs are separate real user/test calls; do not mistake an earlier run result for the final state.')
    (out/f'{sid}-actual.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dict(scenario_id=sid,local_check='passed',real_model_executed=live,actual_file=f'{sid}-actual.json',trace_files=evidence['trace_files'])


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        results=[execute_scenario(f'S{i:02}',Path(temp)) for i in range(1,19)]
    summary=dict(evidence_kind='actual_local_tools_with_offline_mock',scenarios=results,real_model_executed=False,
                 limitations=['Not real model decision evidence.','Only final draft projection plus listed assertions are checked here; detailed behavior also has unittest coverage.'])
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(local_scenarios=len(results),passed=len(results),real_model_executed=False)))


if __name__=='__main__': main()
