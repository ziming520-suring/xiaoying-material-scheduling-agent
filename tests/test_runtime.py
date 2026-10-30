import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime_helpers import database, raw, snapshot, add_due_objects, settle_test_objects, fail_after_partial_settlement

try:
    from schedule_agent.storage import Store
    from schedule_agent.runtime import handle_request, read_clock
except ModuleNotFoundError:
    Store = None


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Store, "transaction runtime is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "runtime.sqlite3"
        self.store = Store(self.db)

    def initialize(self):
        result = handle_request(raw("system.initialize", "init", "2026-10-28T09:00:00Z"), self.store)
        self.assertEqual(result["status"], "succeeded", result)
        return result

    def assert_code(self, result, code):
        self.assertEqual(result["status"], "unknown" if code == "OUTCOME_UNKNOWN" else "failed", result)
        self.assertEqual(result["error"]["code"], code, result)

    def test_B01_invalid_request_cannot_create_database(self):
        self.assert_code(handle_request('{"schema_version":true}', self.store), "INVALID_REQUEST")
        self.assertFalse(self.db.exists())

    def test_B01_invalid_request_does_not_modify_existing_database(self):
        self.initialize()
        before = snapshot(self.db)
        self.assert_code(handle_request(raw(time="18:00"), self.store), "INVALID_REQUEST")
        self.assertEqual(snapshot(self.db), before)

    def test_B02_initialization_replays_but_new_key_cannot_reset_clock(self):
        first = self.initialize()
        before = snapshot(self.db)
        again = handle_request(raw("system.initialize", "init", "2026-10-28T09:00:00Z"), Store(self.db))
        self.assertEqual(again, first)
        self.assertEqual(snapshot(self.db), before)
        self.assert_code(handle_request(raw("system.initialize", "new", "2026-10-28T08:00:00Z"), self.store), "ALREADY_INITIALIZED")
        self.assertEqual(snapshot(self.db), before)

    def test_B03_same_key_changed_parameters_conflicts(self):
        self.initialize()
        handle_request(raw(), self.store)
        before = snapshot(self.db)
        self.assert_code(handle_request(raw(time="2026-10-28T11:00:00Z"), self.store), "IDEMPOTENCY_CONFLICT")
        self.assertEqual(snapshot(self.db), before)

    def test_B04_timezone_equivalent_replay_returns_original(self):
        self.initialize()
        first = handle_request(raw(), self.store)
        before = snapshot(self.db)
        self.assertEqual(handle_request(raw(time="2026-10-28T18:00:00+08:00"), self.store), first)
        self.assertEqual(snapshot(self.db), before)

    def test_B05_read_and_advance_do_not_initialize(self):
        self.assert_code(read_clock(self.store), "NOT_INITIALIZED")
        self.assert_code(handle_request(raw(), self.store), "NOT_INITIALIZED")
        self.assertFalse(self.db.exists())
        self.db.touch()
        self.assert_code(read_clock(self.store), "NOT_INITIALIZED")
        self.assert_code(handle_request(raw(), self.store), "NOT_INITIALIZED")
        self.assertEqual(snapshot(self.db), "BEGIN TRANSACTION;\nCOMMIT;")

    def test_B06_equal_time_does_not_invoke_settlement_or_increment_version(self):
        self.initialize()
        with patch("schedule_agent.runtime.DUE_HANDLERS", (fail_after_partial_settlement,)):
            result = handle_request(raw(time="2026-10-28T09:00:00Z"), self.store)
        self.assertEqual(result["data"], {"previous_time": "2026-10-28T09:00:00Z",
                         "current_time": "2026-10-28T09:00:00Z", "clock_version": 1, "settled_count": 0})
        with database(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM replay_results").fetchone()[0], 2)

    def test_B07_old_success_replays_without_reversing_later_clock(self):
        self.initialize()
        old = handle_request(raw(), self.store)
        handle_request(raw(key="later", time="2026-10-28T11:00:00Z"), self.store)
        before = snapshot(self.db)
        self.assertEqual(handle_request(raw(), self.store), old)
        self.assertEqual(read_clock(self.store)["data"]["current_time"], "2026-10-28T11:00:00Z")
        self.assertEqual(snapshot(self.db), before)

    def test_B08_time_reversal_is_rejected_without_state_change(self):
        self.initialize()
        before = snapshot(self.db)
        self.assert_code(handle_request(raw(time="2026-10-28T08:59:59Z"), self.store), "TIME_REVERSED")
        self.assertEqual(snapshot(self.db), before)

    def test_B09_failure_writing_replay_rolls_back_clock_and_allows_retry(self):
        self.initialize()
        with database(self.db) as conn:
            conn.execute("CREATE TRIGGER reject_replay BEFORE INSERT ON replay_results BEGIN SELECT RAISE(ABORT, 'injected'); END")
        before = snapshot(self.db)
        self.assert_code(handle_request(raw(), self.store), "STORAGE_ERROR")
        self.assertEqual(snapshot(self.db), before)
        with database(self.db) as conn:
            conn.execute("DROP TRIGGER reject_replay")
        first = handle_request(raw(), self.store)
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(first["data"]["clock_version"], 2)
        self.assertEqual(handle_request(raw(), self.store), first)

    def workers(self, requests):
        processes = [subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).with_name("process_worker.py")),
                                      str(self.db), value], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8") for value in requests]
        try:
            for proc in processes:
                self.assertEqual(proc.stdout.readline().strip(), "ready")
            # The parent lock forces the clients to contend on an actual shared DB.
            with database(self.db) as lock:
                lock.execute("BEGIN IMMEDIATE")
                for proc in processes:
                    proc.stdin.write("go\n")
                    proc.stdin.flush()
                lock.rollback()
            results = []
            for proc in processes:
                out, err = proc.communicate(timeout=15)
                self.assertEqual(proc.returncode, 0, err)
                results.append(json.loads(out))
            return results
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate()

    def test_B10_two_processes_same_key_execute_once(self):
        self.initialize()
        first, second = self.workers([raw(), raw()])
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(first, second)
        with database(self.db) as conn:
            self.assertEqual(conn.execute("SELECT version FROM logical_clock").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM replay_results").fetchone()[0], 2)

    def test_B10_concurrent_changed_parameters_have_one_winner(self):
        self.initialize()
        results = self.workers([raw(), raw(time="2026-10-28T11:00:00Z")])
        self.assertEqual(sorted(item["status"] for item in results), ["failed", "succeeded"])
        self.assert_code(next(item for item in results if item["status"] == "failed"), "IDEMPOTENCY_CONFLICT")
        self.assertEqual(read_clock(self.store)["data"]["clock_version"], 2)

    def test_B10_busy_returns_error_without_partial_write(self):
        self.initialize()
        before = snapshot(self.db)
        with database(self.db) as lock:
            lock.execute("BEGIN IMMEDIATE")
            result = handle_request(raw(), Store(self.db, timeout=0.05))
            self.assert_code(result, "STORAGE_BUSY")
            lock.rollback()
        self.assertEqual(snapshot(self.db), before)

    def test_B11_replay_survives_new_process(self):
        self.initialize()
        first = handle_request(raw(), self.store)
        self.assertEqual(self.workers([raw()])[0], first)
        self.assertEqual(read_clock(Store(self.db))["data"]["clock_version"], 2)

    def test_B12_test_due_boundary_and_no_duplicate_settlement(self):
        self.initialize()
        add_due_objects(self.db)
        with patch("schedule_agent.runtime.DUE_HANDLERS", (settle_test_objects,)):
            before = handle_request(raw(key="before", time="2026-10-28T09:59:59Z"), self.store)
            exact = handle_request(raw(), self.store)
            same = handle_request(raw(key="same"), self.store)
            after = handle_request(raw(key="after", time="2026-10-28T10:01:00Z"), self.store)
        self.assertEqual([x["data"]["settled_count"] for x in [before, exact, same, after]], [0, 1, 0, 0])
        with database(self.db) as conn:
            self.assertEqual(conn.execute("SELECT id, settled FROM test_due ORDER BY id").fetchall(), [("A", 1), ("B", 0)])

    def test_B13_partial_settlement_rolls_back_everything(self):
        self.initialize()
        add_due_objects(self.db)
        before = snapshot(self.db)
        with patch("schedule_agent.runtime.DUE_HANDLERS", (fail_after_partial_settlement,)):
            self.assert_code(handle_request(raw(), self.store), "SETTLEMENT_FAILED")
        self.assertEqual(snapshot(self.db), before)
        with patch("schedule_agent.runtime.DUE_HANDLERS", (settle_test_objects,)):
            self.assertEqual(handle_request(raw(), self.store)["data"]["settled_count"], 1)

    def test_B14_committed_then_response_error_is_recovered(self):
        self.initialize()
        class LostReplyStore(Store):
            def commit(self, connection):
                super().commit(connection)
                raise OSError("injected response loss")
        result = handle_request(raw(), LostReplyStore(self.db))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(handle_request(raw(), self.store), result)
        self.assertEqual(result["data"]["clock_version"], 2)

    def test_B15_commit_outcome_unknown_then_retry_recovers(self):
        self.initialize()
        class UnavailableRecoveryStore(Store):
            def commit(self, connection):
                super().commit(connection)
                raise OSError("injected response loss")
            def recover(self, request):
                raise OSError("injected storage unavailable")
        result = handle_request(raw(), UnavailableRecoveryStore(self.db))
        self.assert_code(result, "OUTCOME_UNKNOWN")
        self.assertTrue(result["error"]["retryable"])
        recovered = handle_request(raw(), self.store)
        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["data"]["clock_version"], 2)

    def test_B16_production_advance_has_no_due_business_objects(self):
        self.initialize()
        result = handle_request(raw(time="2030-01-01T00:00:00Z"), self.store)
        self.assertEqual(result["data"]["settled_count"], 0)
        with database(self.db) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"runtime_meta", "logical_clock", "replay_results"})

    def test_corrupt_and_unrelated_databases_are_not_overwritten(self):
        self.db.write_bytes(b"not a SQLite database")
        before = self.db.read_bytes()
        self.assert_code(handle_request(raw("system.initialize", "init"), self.store), "STORAGE_ERROR")
        self.assertEqual(self.db.read_bytes(), before)
        self.db.unlink()
        with database(self.db) as conn:
            conn.execute("CREATE TABLE unrelated (name TEXT)")
            conn.execute("INSERT INTO unrelated VALUES ('keep')")
        before = snapshot(self.db)
        self.assert_code(handle_request(raw("system.initialize", "init"), self.store), "STORAGE_ERROR")
        self.assertEqual(snapshot(self.db), before)

    def test_incompatible_or_incomplete_schema_is_not_repaired(self):
        self.initialize()
        with database(self.db) as conn:
            conn.execute("UPDATE runtime_meta SET storage_schema_version=2")
        before = snapshot(self.db)
        self.assert_code(handle_request(raw(), self.store), "STORAGE_ERROR")
        self.assertEqual(snapshot(self.db), before)
        with database(self.db) as conn:
            conn.execute("UPDATE runtime_meta SET storage_schema_version=1")
            conn.execute("DELETE FROM logical_clock")
        self.assert_code(read_clock(self.store), "STORAGE_ERROR")

    def test_missing_parent_and_readonly_query_never_create_directories(self):
        path = Path(self.temp.name) / "missing" / "runtime.sqlite3"
        result = handle_request(raw("system.initialize", "init"), Store(path))
        self.assert_code(result, "STORAGE_ERROR")
        self.assertFalse(path.parent.exists())

    def test_review_corrupt_replay_responses_are_rejected_without_changes(self):
        self.initialize()
        original = handle_request(raw(), self.store)
        damaged = []
        for data in [{}, {**original["data"], "clock_version": True},
                     {**original["data"], "settled_count": -1},
                     {**original["data"], "current_time": "2030-01-01T00:00:00Z"},
                     {**original["data"], "previous_time": "2030-01-01T00:00:00Z"},
                     {**original["data"], "clock_version": float("nan")}]:
            damaged.append(json.dumps({**original, "data": data}))
        damaged.append(json.dumps(original).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
        damaged.append(json.dumps({**original, "schema_version": True}))
        damaged.append(json.dumps({**original, "extra": 1}))
        for content in damaged:
            with self.subTest(content=content):
                with database(self.db) as conn:
                    conn.execute("UPDATE replay_results SET response_json=? WHERE operation='clock.advance'", (content,))
                before = snapshot(self.db)
                self.assert_code(handle_request(raw(), self.store), "STORAGE_ERROR")
                self.assertEqual(snapshot(self.db), before)

    def test_review_corrupt_initialization_result_is_rejected(self):
        original = self.initialize()
        original["data"]["clock_version"] = 2
        with database(self.db) as conn:
            conn.execute("UPDATE replay_results SET response_json=?", (json.dumps(original),))
        self.assert_code(handle_request(raw("system.initialize", "init", "2026-10-28T09:00:00Z"), self.store), "STORAGE_ERROR")

    def test_review_reordered_replay_schema_rejected_before_mutation(self):
        self.initialize()
        with database(self.db) as conn:
            conn.execute("ALTER TABLE replay_results RENAME TO old_replay")
            conn.execute("CREATE TABLE replay_results (request_id TEXT NOT NULL, operation TEXT NOT NULL, request_digest TEXT NOT NULL, canonical_request_json TEXT NOT NULL, response_json TEXT NOT NULL, PRIMARY KEY(operation, request_id))")
            conn.execute("INSERT INTO replay_results(operation,request_id,request_digest,canonical_request_json,response_json) SELECT operation,request_id,request_digest,canonical_request_json,response_json FROM old_replay")
            conn.execute("DROP TABLE old_replay")
        before = snapshot(self.db)
        self.assert_code(handle_request(raw(), self.store), "STORAGE_ERROR")
        self.assertEqual(snapshot(self.db), before)

    def test_review_missing_replay_key_constraint_rejected_before_mutation(self):
        self.initialize()
        with database(self.db) as conn:
            conn.execute("ALTER TABLE replay_results RENAME TO old_replay")
            conn.execute("CREATE TABLE replay_results (operation TEXT NOT NULL, request_id TEXT NOT NULL, request_digest TEXT NOT NULL, canonical_request_json TEXT NOT NULL, response_json TEXT NOT NULL)")
            conn.execute("INSERT INTO replay_results SELECT * FROM old_replay")
            conn.execute("DROP TABLE old_replay")
        before = snapshot(self.db)
        self.assert_code(handle_request(raw(), self.store), "STORAGE_ERROR")
        self.assertEqual(snapshot(self.db), before)


if __name__ == "__main__":
    unittest.main()
