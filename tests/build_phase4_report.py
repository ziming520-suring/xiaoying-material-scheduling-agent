"""Build a local evidence index from completed runs; no model or network calls."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from phase4_evaluate import CORE, fingerprints


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read_json(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    base=ROOT/'examples/phase4'
    live=read_json(base/'live/summary.json')
    offline=read_json(base/'offline-final/summary.json')
    assert live['stop_reason']==offline['stop_reason']=='completed'
    assert live['source_unchanged'] and offline['source_unchanged']
    current=fingerprints()
    for name,value in live['source_sha256'].items():
        if name.startswith('src/'):
            assert current[name]==value, 'Runtime changed after real evaluation.'
        else:
            assert digest(base/'evaluated-harness'/Path(name).name)==value, 'Missing exact historical harness.'
    assert current==offline['source_sha256'], 'Final offline evidence is stale.'
    evidence_hashes={}
    for directory,summary in (('live',live),('offline-final',offline)):
        assert len(summary['results'])==summary['executed_cases']==summary['requested_cases']
        for result in summary['results']:
            actual_path=base/directory/result['actual_file']
            actual=read_json(actual_path)
            assert actual['phase4_verdict']==result
            assert actual['real_model_executed']==(directory=='live')
            for name in actual['trace_files']:
                path=actual_path.parent/name
                rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                assert rows and rows[0]['event']=='run_start'
                evidence_hashes[path.relative_to(ROOT).as_posix()]=digest(path)
            evidence_hashes[actual_path.relative_to(ROOT).as_posix()]=digest(actual_path)
    raw=(ROOT/'docs/phase4-tests-final.txt').read_bytes()
    log=raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8-sig')
    match=re.search(r'Ran (\d+) tests in ([\d.]+)s',log)
    assert match and re.search(r'^OK\s*$',log,re.M), 'Tests not successful.'
    (base/'test-results.txt').write_text(log,encoding='utf-8')
    core={sid:dict(executed=sum(r['scenario_id']==sid for r in live['results']),
                   passed=sum(r['scenario_id']==sid and r['passed'] for r in live['results'])) for sid in CORE}
    before,after=live['budget_before'],live['budget_after']
    usage={key:after[key]-before[key] for key in ('calls','prompt_tokens','completion_tokens')}
    usage['estimated_yuan']=str(Decimal(after['estimated_yuan'])-Decimal(before['estimated_yuan']))
    usage['settled_conservative_yuan']=str(Decimal(after['settled_conservative_yuan'])-Decimal(before['settled_conservative_yuan']))
    report=dict(generated_at=datetime.now(timezone.utc).isoformat(),
        unittest=dict(tests=int(match[1]),seconds=float(match[2]),failures=0,errors=0,file='test-results.txt'),
        live={k:live[k] for k in ('model','started_at','updated_at','requested_cases','executed_cases','passed','failed','source_unchanged')},
        offline={k:offline[k] for k in ('requested_cases','executed_cases','passed','failed','source_unchanged')},
        core_repetitions=core,failures=[r for r in live['results'] if not r['passed']],
        live_stage_usage=usage,cumulative_budget=after,
        dynamic_paths=[dict(scenario_id=r['scenario_id'],traces=r['traces']) for r in live['results'] if r['round']==1 and r['scenario_id'] in ('S01','S02','S06','S11')],
        runtime_matches_live=True,current_source_and_data_sha256=current,evidence_sha256=evidence_hashes,
        caveats=['Live evidence froze runtime and two harness files, not data or runtime_helpers. Final offline run freezes all listed data and helper files.',
                 'The two archived harness files are the exact live-evaluated version; final harness adds post-case source checks, broader hashes and earlier fault annotation. Runtime/prompt unchanged.',
                 'Provider model alias may change. Synthetic finite sample is not a production success-rate estimate.',
                 'Costs are estimates, not verified billing. No real publishing. User confirmations and recovery are harness operations.'])
    (base/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('unittest','live','offline','live_stage_usage','runtime_matches_live')},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
