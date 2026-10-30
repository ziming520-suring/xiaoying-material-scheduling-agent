import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import phase4_evaluate

from phase4_evaluate import run_case
from schedule_agent.model import OfflineMock
from schedule_agent.contracts import RequestError


class Phase4Tests(unittest.TestCase):
    def test_final_case_source_change_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); out=root/'evidence'; changed=False
            def fingerprint(): return {'source':'changed' if changed else 'original'}
            def case(*args):
                nonlocal changed
                changed=True
                return dict(round=1,scenario_id='S01',passed=True,initial_statuses=['completed'],model_errors=[])
            with patch.object(phase4_evaluate,'ROOT',root), patch.object(phase4_evaluate,'ALL',['S01']), \
                 patch.object(phase4_evaluate,'fingerprints',side_effect=fingerprint), \
                 patch.object(phase4_evaluate,'run_case',side_effect=case), \
                 patch('sys.argv',['evaluate','--model','mock','--output',str(out),'--rounds','1']):
                self.assertEqual(phase4_evaluate.main(),2)
            summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
            self.assertFalse(summary['source_unchanged'])
            self.assertEqual(summary['stop_reason'],'source_changed')

    def test_fingerprints_include_fixture_content_and_runtime_helper(self):
        hashes=phase4_evaluate.fingerprints()
        for path in (phase4_evaluate.ROOT/'data').rglob('*.json'):
            self.assertIn(path.relative_to(phase4_evaluate.ROOT).as_posix(),hashes)
        self.assertIn('tests/runtime_helpers.py',hashes)

    def test_fault_description_survives_early_model_failure(self):
        class Failed:
            mode='failure_test_double'
            def next_action(self,context): raise RequestError('MODEL_PROTOCOL_ERROR','test only')
        for sid in ('S17','S18'):
            with self.subTest(scenario=sid),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                result=run_case(sid,1,Failed(),root/'evidence',root/'db')
                self.assertFalse(result['passed'])
                evidence=json.loads((root/f'evidence/round-1/{sid}-actual.json').read_text(encoding='utf-8'))
                self.assertTrue(evidence['fault_injection'])

    def test_failed_model_attempt_keeps_actual_output_and_trace(self):
        class Failed:
            mode='failure_test_double'
            def next_action(self,context): raise RequestError('MODEL_PROTOCOL_ERROR','test only')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            result=run_case('S01',1,Failed(),root/'evidence',root/'db')
            self.assertFalse(result['passed'])
            evidence=json.loads((root/'evidence/round-1/S01-actual.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['agent_runs'][0]['items'][0]['save_status'],'not_attempted')
            self.assertTrue((root/'evidence/round-1/S01-run-1.jsonl').exists())
            self.assertIn('MODEL_PROTOCOL_ERROR',result['model_errors'])

    def test_offline_scenario_is_marked_offline_and_successful(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            result=run_case('S06',1,OfflineMock(),root/'evidence',root/'db')
            self.assertTrue(result['passed'])
            self.assertFalse(result['real_model_executed'])
