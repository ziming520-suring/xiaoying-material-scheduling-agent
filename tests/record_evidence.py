"""Reproduce foundation evidence in isolated temporary databases (not ReAct traces)."""
from contextlib import contextmanager
import io
import json
import logging
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime_helpers import (raw, snapshot, database, add_due_objects,
                             settle_test_objects, fail_after_partial_settlement)
from schedule_agent.runtime import handle_request, read_clock
from schedule_agent.storage import Store

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "foundation"


def save(name, value):
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cli_step(db, request=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
    command = [sys.executable, "-m", "schedule_agent"]
    if request is None:
        command += ["clock", "--db", str(db)]
    else:
        path = db.parent / "input.json"
        path.write_text(request, encoding="utf-8")
        command += ["run", "--db", str(db), "--input", str(path)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=15)
    return {"input": json.loads(request) if request else {"command": "clock"},
            "output": json.loads(result.stdout), "exit_code": result.returncode,
            "trace": [json.loads(line) for line in result.stderr.splitlines() if line.strip()]}


def api_step(request, store):
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    logger = logging.getLogger("schedule_agent")
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        result = handle_request(request, store)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    return {"input": json.loads(request), "output": result,
            "trace": [json.loads(line) for line in log.getvalue().splitlines()]}


def generate():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = io.StringIO()
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=report, verbosity=2).run(suite)
    (OUTPUT / "test-results.txt").write_text(report.getvalue(), encoding="utf-8")
    save("test-summary.json", {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
         "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
         "skipped": len(result.skipped), "duration_seconds": round(time.perf_counter() - started, 3),
         "successful": result.wasSuccessful(), "scope": "foundation only, no model decisions"})
    if not result.wasSuccessful():
        print(report.getvalue())
        return 1

    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "normal.sqlite3"
        steps = [cli_step(db, raw("system.initialize", "init", "2026-10-28T09:00:00Z")),
                 cli_step(db, raw()), cli_step(db, raw()), cli_step(db)]
        assert all(step["exit_code"] == 0 for step in steps)
        assert steps[1]["output"] == steps[2]["output"]
        assert steps[3]["output"]["data"]["clock_version"] == 2
        save("01-normal-replay.json", {"mode": "real CLI / local SQLite", "steps": steps})

        db = Path(directory) / "invalid.sqlite3"
        steps = [cli_step(db, '{"schema_version":1,"request_id":"missing"}'), cli_step(db, raw())]
        assert [s["exit_code"] for s in steps] == [2, 2]
        assert not db.exists()
        save("02-invalid-uninitialized.json", {"mode": "real CLI / local SQLite", "steps": steps,
                                                "database_created": db.exists()})

        db = Path(directory) / "failure.sqlite3"
        store = Store(db)
        steps = [api_step(raw("system.initialize", "init", "2026-10-28T09:00:00Z"), store)]
        with database(db) as conn:
            conn.execute("CREATE TRIGGER reject_replay BEFORE INSERT ON replay_results BEGIN SELECT RAISE(ABORT, 'injected'); END")
        before = snapshot(db)
        steps.append(api_step(raw(), store))
        unchanged = snapshot(db) == before
        assert unchanged and steps[-1]["output"]["error"]["code"] == "STORAGE_ERROR"
        with database(db) as conn:
            conn.execute("DROP TRIGGER reject_replay")
        steps.extend([api_step(raw(), store), api_step(raw(), store)])
        assert steps[-1]["output"] == steps[-2]["output"]
        save("03-storage-failure-retry.json", {"mode": "real runtime / SQLite trigger fault injection",
             "steps": steps, "state_unchanged_on_failure": unchanged})

        class UnavailableRecoveryStore(Store):
            def commit(self, connection):
                super().commit(connection)
                raise OSError("injected reply loss")
            def recover(self, request):
                raise OSError("injected recovery outage")

        db = Path(directory) / "unknown.sqlite3"
        store = Store(db)
        steps = [api_step(raw("system.initialize", "init", "2026-10-28T09:00:00Z"), store),
                 api_step(raw(), UnavailableRecoveryStore(db)), api_step(raw(), store)]
        assert steps[1]["output"]["status"] == "unknown"
        assert steps[2]["output"]["data"]["clock_version"] == 2
        save("04-unknown-recovery.json", {"mode": "real runtime / injected commit acknowledgement and recovery faults",
                                          "steps": steps})

        db = Path(directory) / "settlement.sqlite3"
        store = Store(db)
        steps = [api_step(raw("system.initialize", "init", "2026-10-28T09:00:00Z"), store)]
        add_due_objects(db)
        with patch("schedule_agent.runtime.DUE_HANDLERS", (settle_test_objects,)):
            steps.append(api_step(raw(key="before", time="2026-10-28T09:59:59Z"), store))
            steps.append(api_step(raw(), store))
        before = snapshot(db)
        with patch("schedule_agent.runtime.DUE_HANDLERS", (fail_after_partial_settlement,)):
            steps.append(api_step(raw(key="later", time="2026-10-28T11:00:00Z"), store))
        unchanged = snapshot(db) == before
        assert unchanged and steps[-1]["output"]["error"]["code"] == "SETTLEMENT_FAILED"
        with patch("schedule_agent.runtime.DUE_HANDLERS", (settle_test_objects,)):
            steps.append(api_step(raw(key="later", time="2026-10-28T11:00:00Z"), store))
        assert steps[-1]["output"]["data"]["settled_count"] == 1
        save("05-test-only-due-processing.json", {"mode": "TEST FIXTURES ONLY; no production expiry rule",
             "fixture": [{"id": "A", "due_at": "2026-10-28T10:00:00Z"},
                         {"id": "B", "due_at": "2026-10-28T11:00:00Z"}],
             "steps": steps, "state_unchanged_on_failure": unchanged})
    print(f"Verified {result.testsRun} tests and wrote 5 actual foundation traces to examples/foundation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(generate())
