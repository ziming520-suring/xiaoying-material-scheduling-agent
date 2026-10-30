"""Opt-in paid evaluation; real Bailian selections, synthetic local business tools.

Run explicitly with --scenarios S01 S06 etc. Never part of unittest discovery.
Each invocation uses a fresh output directory. The shared budget is NOT reset.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from schedule_agent.agent import run_agent
from schedule_agent.bailian import BailianModel
from schedule_agent.business import read_drafts
from schedule_agent.contracts import serialize
from schedule_agent.runtime import handle_request
from schedule_agent.storage import Store
from live_validation import validate_save_failure_evidence


def scenario(sid, output, model):
    definition=json.loads((ROOT/'data/scenarios'/f'{sid}.json').read_text(encoding='utf-8'))
    path=ROOT/'var'/output.name/f'{sid}.sqlite3'
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise ValueError('Use a new output directory; database already exists.')
    store=Store(path); operations=[]; runs=[]; trace_files=[]; checks=[]
    def write(op,payload,key,channel='run'):
        request=dict(schema_version=1,request_id=key,operation=op,payload=payload)
        result=handle_request(serialize(request),store,channel=channel)
        operations.append(dict(request=request,response=result,channel=channel))
        return result
    def success(r):
        if r['status']!='succeeded': raise AssertionError(r)
    success(write('system.initialize',{'initial_time':'2026-10-28T09:00:00Z'},'init'))
    success(write('schema.upgrade',{'target_storage_version':2},'upgrade'))
    success(write('fixture.load',dict(dataset_id='synthetic-campus-v1',calendar_id=definition['calendar_id'],
                  manifest_sha256=hashlib.sha256((ROOT/'data/manifest.json').read_bytes()).hexdigest()),'load'))
    success(write('task.create',definition['task'],'task'))
    fault=None
    if sid=='S10':
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TRIGGER live_test_fault BEFORE INSERT ON replay_results WHEN NEW.operation='draft.save' BEGIN SELECT RAISE(ABORT,'injected test failure'); END")
        fault='Test-only SQLite trigger aborts save transaction before replay commit; not a provider failure.'
    def run():
        trace=output/f'{sid}-run-{len(runs)+1}.jsonl'
        result=run_agent(store,definition['task']['task_id'],model,trace,max_steps=10)
        runs.append(result); trace_files.append(trace.name)
        return result
    first=run(); item=first['items'][0]
    def check(label,condition): checks.append(dict(check=label,passed=bool(condition)))
    expected_status={'S01':'completed','S02':'completed','S03':'needs_information','S04':'blocked',
                     'S06':'pending_confirmation','S09':'completed','S10':'save_failed','S11':'pending_confirmation'}[sid]
    check('Initial item status matches expected',item['item_status']==expected_status)
    if sid=='S06':
        check('Alternative requires confirmation and does not occupy',bool(item['draft']) and
              not item['draft']['occupies_slot'] and item['suggested_slot']['slot_id'] in ['S19','S20','S21'] and
              item['requested_time']=='2026-10-28T10:00:00Z')
    if sid in ('S09','S10'):
        events=[json.loads(line) for line in (output/trace_files[0]).read_text(encoding='utf-8').splitlines()]
        requests=[r['request'] for r in events if r['event']=='write_request']
        check('Model actually attempted save',bool(requests))
        if requests:
            if sid=='S10':
                check('Failed save left no draft',read_drafts(store,definition['task']['task_id'])['data']['drafts']==[])
                with sqlite3.connect(path) as conn: conn.execute('DROP TRIGGER live_test_fault')
            request=requests[-1]
            a=write(request['operation'],request['payload'],request['request_id'])
            b=write(request['operation'],request['payload'],request['request_id'])
            check('Explicit harness original-key recovery/replay succeeds once',a==b and a['status']=='succeeded')
    if sid=='S11' and item['draft']:
        old=item['draft']
        success(write('calendar.replace',{'expected_revision':1,'existing':[{'schedule_id':'NEW.S19','slot_id':'S19'}]},'replace','test'))
        second=run()['items'][0]
        check('New observation updates same draft to original now-free S18',second['item_status']=='completed' and
              second['draft']['draft_id']==old['draft_id'] and second['draft']['version']>old['version'] and
              second['suggested_slot']['slot_id']=='S18')
    drafts=read_drafts(store,definition['task']['task_id'])
    if sid in ('S01','S02','S09'):
        check('One confirmed draft only',drafts['status']=='succeeded' and len(drafts['data']['drafts'])==1 and
              drafts['data']['drafts'][0]['status']=='confirmed')
    if sid in ('S03','S04'): check('No draft or occupancy',drafts['data']['drafts']==[])
    result=dict(scenario_id=sid,title=definition['title'],model_mode=model.mode,model='qwen-plus',
                input=definition['task'],calendar_id=definition['calendar_id'],fault_injection=fault,
                operations=operations,agent_runs=runs,trace_files=trace_files,final_drafts=drafts,
                checks=checks,passed=all(c['passed'] for c in checks),
                notice='Real model tool choices; synthetic local tools. Recovery/calendar writes are explicit harness actions, not model choices.')
    if sid=='S10':
        checks.extend(validate_save_failure_evidence(result)['checks'])
        result['passed']=all(c['passed'] for c in checks)
    (output/f'{sid}-actual.json').write_text(serialize(result)+'\n',encoding='utf-8')
    return dict(scenario_id=sid,passed=result['passed'],item_status=item['item_status'],trace_files=trace_files)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--scenarios',nargs='+',required=True,choices=['S01','S02','S03','S04','S06','S09','S10','S11'])
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    output=Path(args.output); output.mkdir(parents=True,exist_ok=False)
    model=BailianModel(); results=[]
    for sid in args.scenarios:
        row=scenario(sid,output,model); results.append(row)
        summary=dict(created_at=datetime.now(timezone.utc).isoformat(),model_mode=model.mode,model='qwen-plus',
                     results=results,budget=model.budget.summary())
        (output/'summary.json').write_text(serialize(summary)+'\n',encoding='utf-8')
        print(serialize(row),flush=True)
        if not row['passed']: break  # Investigate before spending on more scenarios.
    print(serialize(model.budget.summary()),flush=True)
    return 0 if all(r['passed'] for r in results) and len(results)==len(args.scenarios) else 1


if __name__=='__main__': raise SystemExit(main())
