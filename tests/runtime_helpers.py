"""Fixtures and fault injection only; never imported by production code."""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def raw(operation="clock.advance", key="advance-1", time="2026-10-28T10:00:00Z"):
    field = "initial_time" if operation == "system.initialize" else "target_time"
    return json.dumps({"schema_version": 1, "request_id": key, "operation": operation,
                       "payload": {field: time}})


@contextmanager
def database(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def snapshot(path):
    with database(path) as conn:
        return "\n".join(conn.iterdump())


def add_due_objects(path):
    with database(path) as conn:
        conn.execute("CREATE TABLE test_due (id TEXT PRIMARY KEY, due_at TEXT, settled INTEGER NOT NULL)")
        conn.executemany("INSERT INTO test_due VALUES (?, ?, 0)", [
            ("A", "2026-10-28T10:00:00Z"), ("B", "2026-10-28T11:00:00Z")])


def settle_test_objects(tx, previous, target):
    return tx.execute("UPDATE test_due SET settled=1 WHERE settled=0 AND due_at<=?", (target,)).rowcount


def fail_after_partial_settlement(tx, previous, target):
    settle_test_objects(tx, previous, target)
    raise RuntimeError("injected after partial update")
