import copy
import json
from pathlib import Path
import unittest

from live_validation import validate_save_failure_evidence


class LiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'examples/bailian-failure-final/S10-actual.json'
        self.evidence=json.loads(path.read_text(encoding='utf-8'))

    def test_recovery_of_actual_alternative_remains_pending(self):
        self.assertTrue(validate_save_failure_evidence(self.evidence)['passed'])

    def test_recovery_rejects_unauthorized_occupancy_and_changed_replay(self):
        for kind in ('occupancy','replay'):
            changed=copy.deepcopy(self.evidence)
            if kind=='occupancy': changed['final_drafts']['data']['drafts'][0]['occupies_slot']=True
            else: changed['operations'][-1]['response']['status']='failed'
            self.assertFalse(validate_save_failure_evidence(changed)['passed'])
