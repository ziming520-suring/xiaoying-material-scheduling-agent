"""Click-to-run launcher uses real local tools and no paid model by default."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DemoTests(unittest.TestCase):
    def test_default_launcher_works_from_other_directory_and_preserves_previous_run(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            results=[]
            for _ in range(2):
                run=subprocess.run([sys.executable,'-X','utf8',str(root/'run_demo.py'),'--output-root',temp],
                                   cwd=temp,capture_output=True,text=True,encoding='utf-8',timeout=15)
                self.assertEqual(run.returncode,2,run.stderr)
                result=json.loads(run.stdout)
                self.assertEqual(result['model_mode'],'offline_mock')
                item=result['result']['items'][0]
                self.assertEqual(item['item_status'],'pending_confirmation')
                self.assertEqual(item['suggested_slot']['slot_id'],'S19')
                self.assertFalse(item['draft']['occupies_slot'])
                self.assertTrue(Path(result['trace_path']).is_file())
                self.assertEqual(json.loads(Path(result['output_path']).read_text(encoding='utf-8')),result)
                results.append(result)
            self.assertNotEqual(results[0]['database_path'],results[1]['database_path'])
            self.assertTrue(Path(results[0]['database_path']).is_file())
