"""Read-only aggregation of existing model runs; never calls a provider."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from schedule_agent.bailian import BudgetLedger
from live_validation import validate_save_failure_evidence

def main():
    output=ROOT/'examples/bailian-report'; output.mkdir(exist_ok=True)
    attempts=[]
    for folder in sorted((ROOT/'examples').glob('bailian-*')):
        for path in sorted(folder.glob('S*-actual.json')):
            data=json.loads(path.read_text(encoding='utf-8'))
            sequences=[]; model_errors=[]
            for name in data['trace_files']:
                records=[json.loads(line) for line in (folder/name).read_text(encoding='utf-8').splitlines()]
                sequences.append([r['action'] for r in records if r['event']=='model_action'])
                model_errors.extend(r for r in records if r['event']=='model_error')
            attempts.append(dict(scenario_id=data['scenario_id'],passed=data['passed'],model_errors=model_errors,file=str(path.relative_to(ROOT)).replace('\\','/'),action_sequences=sequences))
    selected={sid:('bailian-final' if sid in ('S01','S02','S06') else 'bailian-v3') for sid in ('S01','S02','S03','S04','S06','S09','S10','S11')}
    selected['S10']='bailian-failure-final'
    coverage=[]
    for sid,folder in selected.items():
        evidence=next(r for r in attempts if r['scenario_id']==sid and f'/{folder}/' in r['file'])
        coverage.append(evidence)
    failure_path=ROOT/'examples/bailian-failure-final/S10-actual.json'
    followup=validate_save_failure_evidence(json.loads(failure_path.read_text(encoding='utf-8')))
    followup['source_sha256']=hashlib.sha256(failure_path.read_bytes()).hexdigest()
    (output/'S10-follow-up-validation.json').write_text(json.dumps(followup,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    log=(ROOT/'docs/bailian-tests.txt').read_text(encoding='utf-8-sig')
    if 'Ran 97 tests' not in log or not log.rstrip().endswith('OK'): raise AssertionError('Expected fresh 97-test green suite.')
    shutil.copyfile(ROOT/'docs/bailian-tests.txt',output/'test-results.txt')
    result=dict(model='qwen-plus',provider='Alibaba Cloud Bailian Beijing',
                note='Adaptive development attempts, not an unbiased benchmark success rate. Selected runs span documented iterations; inspect all failures.',
                all_attempts=attempts,selected_coverage=coverage,selected_all_passed=all(r['passed'] for r in coverage),
                save_failure_followup_validation=followup,
                budget=BudgetLedger().summary(),tests={'count':97,'failures':0,'errors':0,'log':'test-results.txt'},
                final_source_sha256={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'src').rglob('*.py'))})
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(attempts=len(attempts),failures=sum(not a['passed'] for a in attempts),selected_all_passed=result['selected_all_passed'],budget=result['budget']),ensure_ascii=False))

if __name__=='__main__': main()
