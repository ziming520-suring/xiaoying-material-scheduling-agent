"""Fixed-version evaluation, opt-in live model. Failures remain in the denominator."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from schedule_agent.bailian import BailianModel
from schedule_agent.model import OfflineMock
from record_business_evidence import execute_scenario

CORE=['S01','S02','S03','S04','S06','S09','S10','S11']
ALL=[f'S{i:02}' for i in range(1,19)]
FIRST_STATUS={'S01':['completed'],'S02':['completed'],'S03':['needs_information'],'S04':['blocked'],
              'S05':['pending_confirmation'],'S06':['pending_confirmation'],'S07':['pending_confirmation'],
              'S08':['incomplete'],'S09':['completed'],'S10':['save_failed'],'S11':['pending_confirmation'],
              'S12':['completed','needs_information','save_failed'],'S13':['blocked'],'S14':['blocked'],
              'S15':['pending_confirmation'],'S16':['pending_confirmation'],'S17':['unknown'],'S18':['incomplete']}


def fingerprints():
    paths=list((ROOT/'src').rglob('*.py'))+[ROOT/'tests/record_business_evidence.py',Path(__file__)]
    return {str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def run_case(sid,round_id,model,output,dbroot):
    out=output/f'round-{round_id}'; out.mkdir(parents=True,exist_ok=True)
    dbdir=dbroot/f'round-{round_id}'; dbdir.mkdir(parents=True,exist_ok=True)
    if (out/f'{sid}-actual.json').exists() or (dbdir/f'{sid}.sqlite3').exists():
        raise ValueError('Use fresh evidence and database directories.')
    started=time.monotonic(); error=None
    try: execute_scenario(sid,dbdir,model=model,output=out,max_steps=24)
    except Exception as exc: error=type(exc).__name__  # Do not expose exception/provider bodies.
    actual=out/f'{sid}-actual.json'
    data=json.loads(actual.read_text(encoding='utf-8')) if actual.exists() else {}
    traces=[]; model_errors=[]
    for path in sorted(out.glob(f'{sid}-run-*.jsonl')):
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        model_errors.extend(r['code'] for r in rows if r['event']=='model_error')
        traces.append(dict(file=path.name,actions=[r['action'] for r in rows if r['event']=='model_action']))
    runs=data.get('agent_runs',[])
    initial=[i['item_status'] for i in runs[0]['items']] if runs else []
    status_match=initial==FIRST_STATUS[sid]
    if runs:
        for run in runs:
            for item in run['items']:
                model_errors.extend(code for code in item['reason_codes'] if code.startswith('MODEL_'))
    checks=dict(contract_assertions=error is None,initial_status_matches=status_match,no_model_errors=not model_errors)
    verdict=dict(scenario_id=sid,round=round_id,passed=all(checks.values()),checks=checks,
                 real_model_executed=model.mode=='bailian_live',model_mode=model.mode,
                 error_type=error,initial_statuses=initial,model_errors=sorted(set(model_errors)),
                 elapsed_seconds=round(time.monotonic()-started,3),actual_file=f'round-{round_id}/{sid}-actual.json',traces=traces)
    data['phase4_verdict']=verdict
    if error: data['execution_status']='evaluation_failed'
    actual.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return verdict


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',choices=['mock','bailian'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rounds',type=int,default=3,choices=range(1,6))
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    model=BailianModel() if args.model=='bailian' else OfflineMock()
    dbroot=ROOT/'var'/('phase4-'+args.output.name)
    source=fingerprints(); started=datetime.now(timezone.utc).isoformat(); results=[]
    budget_before=model.budget.summary() if args.model=='bailian' else None
    schedule=[(r,sid) for r in range(1,args.rounds+1) for sid in (ALL if r==1 or args.model=='mock' else CORE)]
    def persist(stop=None):
        summary=dict(started_at=started,updated_at=datetime.now(timezone.utc).isoformat(),model_mode=model.mode,
                     model='qwen-plus' if args.model=='bailian' else 'OfflineMock',
                     requested_cases=len(schedule),executed_cases=len(results),passed=sum(r['passed'] for r in results),
                     failed=sum(not r['passed'] for r in results),stop_reason=stop,results=results,
                     source_sha256=source,source_unchanged=source==fingerprints(),budget_before=budget_before,
                     budget_after=model.budget.summary() if args.model=='bailian' else None,
                     note='Synthetic local business tools; confirmation/calendar/recovery actions belong to the test harness, not the model. No real publishing.')
        (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    persist()
    for r,sid in schedule:
        if source!=fingerprints(): persist('source_changed'); return 2
        result=run_case(sid,r,model,args.output,dbroot); results.append(result); persist()
        print(json.dumps({k:result[k] for k in ('round','scenario_id','passed','initial_statuses','model_errors')},ensure_ascii=False),flush=True)
        if set(result['model_errors'])&{'MODEL_BUDGET_EXCEEDED','MODEL_BUDGET_ERROR','MODEL_AUTH_ERROR','MODEL_CONFIG_ERROR'}:
            persist('configuration_or_budget'); return 2
    persist('completed')
    return 0 if all(r['passed'] for r in results) else 1


if __name__=='__main__': raise SystemExit(main())
