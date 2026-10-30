import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from runtime_helpers import database, raw, snapshot

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "含 空格#数据库.sqlite3"
        self.input = Path(self.temp.name) / "request.json"

    def call(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        result = subprocess.run([sys.executable, "-m", "schedule_agent", *args],
                                env=env, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=15)
        self.assertTrue(result.stdout.strip().startswith("{"), result.stderr or result.stdout)
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        self.assertNotIn("Traceback", result.stderr)
        return result, json.loads(result.stdout)

    def write(self, content):
        self.input.write_text(content, encoding="utf-8")
        return self.call("run", "--db", str(self.db), "--input", str(self.input))

    def initialize(self):
        result, body = self.write(raw("system.initialize", "init", "2026-10-28T09:00:00Z"))
        self.assertEqual(result.returncode, 0, body)
        return result, body

    def test_cli_init_advance_replay_and_read(self):
        self.initialize()
        first, body = self.write(raw())
        self.assertEqual(first.returncode, 0)
        replay, replay_body = self.write(raw())
        self.assertEqual(replay.returncode, 0)
        self.assertEqual(first.stdout, replay.stdout)
        self.assertEqual(body, replay_body)
        self.assertIn('"event":"replay_hit"', replay.stderr)
        queried, clock = self.call("clock", "--db", str(self.db))
        self.assertEqual(queried.returncode, 0)
        self.assertEqual(clock["data"], {"current_time": "2026-10-28T10:00:00Z", "clock_version": 2})

    def test_cli_invalid_json_returns_exit_two_and_no_database(self):
        result, body = self.write('{"schema_version":false}')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertFalse(self.db.exists())

    def test_cli_invalid_utf8_returns_error(self):
        self.input.write_bytes(b"\xff\xfe")
        result, body = self.call("run", "--db", str(self.db), "--input", str(self.input))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertFalse(self.db.exists())

    def test_cli_missing_file_and_parameters_are_json_errors(self):
        for args in [("run", "--db", str(self.db), "--input", str(self.input)),
                     ("run", "--db", str(self.db)), (), ("publish",),
                     ("clock", "--db", str(self.db), "--extra", "x")]:
            with self.subTest(args=args):
                result, body = self.call(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
                self.assertFalse(self.db.exists())

    def test_cli_storage_failure_exit_three(self):
        self.db.write_bytes(b"corrupt")
        result, body = self.write(raw("system.initialize", "init"))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["error"]["code"], "STORAGE_ERROR")
        self.assertEqual(self.db.read_bytes(), b"corrupt")

    def test_cli_query_does_not_initialize_or_change_database(self):
        result, body = self.call("clock", "--db", str(self.db))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body["error"]["code"], "NOT_INITIALIZED")
        self.assertFalse(self.db.exists())
        self.initialize()
        before = snapshot(self.db)
        self.call("clock", "--db", str(self.db))
        self.assertEqual(snapshot(self.db), before)

    def test_cli_conflict_and_reversal_are_exit_two(self):
        self.initialize()
        self.write(raw())
        for request, code in [(raw(time="2026-10-28T11:00:00Z"), "IDEMPOTENCY_CONFLICT"),
                              (raw(key="earlier", time="2026-10-28T08:00:00Z"), "TIME_REVERSED")]:
            result, body = self.write(request)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(body["error"]["code"], code)

    def test_review_corrupt_nan_replay_remains_a_json_error(self):
        self.initialize()
        _, body = self.write(raw())
        body["data"]["clock_version"] = float("nan")
        with database(self.db) as conn:
            conn.execute("UPDATE replay_results SET response_json=? WHERE operation='clock.advance'", (json.dumps(body),))
        result, body = self.write(raw())
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["error"]["code"], "STORAGE_ERROR")


if __name__ == "__main__":
    unittest.main()
