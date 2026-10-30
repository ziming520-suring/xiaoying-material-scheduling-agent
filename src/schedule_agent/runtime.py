"""Unified write gate, replay, logical time and transactional business dispatch."""
import logging
import sqlite3
from typing import Callable

from .contracts import (OPERATIONS, RequestError, Response, ValidatedRequest, canonicalize,
                        error_response, parse_and_validate, serialize, success_response, request_payload)
from .storage import (SCHEMA, ReadOnlyTransaction, Store, Transaction, check_schema,
                      lookup_replay, save_replay)

logger = logging.getLogger("schedule_agent")
# Trusted, local transactional handlers only. Production intentionally registers none.
DUE_HANDLERS: tuple[Callable[[Transaction, str, str], int], ...] = ()


def _event(name: str, request: ValidatedRequest | None = None, **details) -> None:
    # Diagnostics must never turn a committed success into a reported failure.
    try:
        logger.info(serialize({"event": name, "request_id": request.request_id if request else None,
                               "operation": request.operation if request else None, **details}))
    except Exception:
        pass


def _error_for(exc: Exception) -> RequestError:
    if isinstance(exc, RequestError):
        return exc
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", 0) or 0
        if code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return RequestError("STORAGE_BUSY", "Storage is busy; retry the same request.", retryable=True)
        return RequestError("STORAGE_ERROR", "Storage operation failed without a committed change.", retryable=True)
    if isinstance(exc, OSError):
        return RequestError("STORAGE_ERROR", "Storage is unavailable; resolve the fault and retry.", retryable=True)
    return RequestError("INTERNAL_ERROR", "Operation failed; no new change was committed.")


def validate_preconditions(tx: ReadOnlyTransaction, request: ValidatedRequest) -> None:
    if request.operation == "system.initialize":
        raise RequestError("ALREADY_INITIALIZED", "The clock is already initialized.")
    current, _ = tx.clock()
    if request.time_utc < current:
        raise RequestError("TIME_REVERSED", "Logical time cannot move backwards.", "payload.target_time")


def initialize_clock(tx: Transaction, initial_time_utc: str) -> dict:
    for statement in SCHEMA:
        tx.execute(statement)
    tx.execute("INSERT INTO runtime_meta (singleton_id, storage_schema_version) VALUES (1, 1)")
    tx.execute("INSERT INTO logical_clock (singleton_id, current_time_utc, version) VALUES (1, ?, 1)", (initial_time_utc,))
    return {"current_time": initial_time_utc, "clock_version": 1}


def settle_due(tx: Transaction, previous_time_utc: str, target_time_utc: str) -> int:
    total = 0
    try:
        for handler in DUE_HANDLERS:
            count = handler(tx, previous_time_utc, target_time_utc)
            if type(count) is not int or count < 0:
                raise ValueError("handler must return a nonnegative count")
            total += count
    except Exception:
        raise RequestError("SETTLEMENT_FAILED", "Due processing failed; the transaction will be rolled back.", retryable=True) from None
    return total


def advance_clock(tx: Transaction, target_time_utc: str) -> dict:
    previous, version = tx.clock()
    count = 0
    if target_time_utc > previous:
        count = settle_due(tx, previous, target_time_utc)
        version += 1
        tx.execute("UPDATE logical_clock SET current_time_utc=?, version=? WHERE singleton_id=1", (target_time_utc, version))
    return {"previous_time": previous, "current_time": target_time_utc,
            "clock_version": version, "settled_count": count}


def handle_request(raw_json: str, store: Store, *, channel: str = 'run') -> Response:
    try:
        request = parse_and_validate(raw_json)
    except RequestError as exc:
        _event("validation_failed", code=exc.code)
        return error_response(exc)
    return execute_write(request, store, channel=channel)


def execute_write(request: ValidatedRequest, store: Store, *, channel: str = 'run') -> Response:
    # Do not trust a manually constructed dataclass to bypass the public write gate.
    validated_request = None
    try:
        request = parse_and_validate(serialize({"schema_version": 1,
            "request_id": request.request_id, "operation": request.operation,
            "payload": request_payload(request)}))
        validated_request = request
        if request.operation == 'draft.confirm' and channel != 'user':
            raise RequestError('CONFIRMATION_CHANNEL_REQUIRED','Use the independent user confirmation entry.')
        if request.operation == 'calendar.replace' and channel != 'test':
            raise RequestError('UNKNOWN_OPERATION','Calendar replacement is only available to trusted test fixtures.')
        if channel == 'model' and request.operation != 'draft.save':
            raise RequestError('UNKNOWN_OPERATION','This write operation is not a model tool.')
    except RequestError as exc:
        return error_response(exc, validated_request)
    canonical = canonicalize(request)
    _event("validated", request)
    conn = None
    commit_started = False
    try:
        conn = store.connect(create=request.operation == "system.initialize")
        conn.execute("BEGIN IMMEDIATE")
        initialized = check_schema(conn)
        tx = Transaction(conn)
        if initialized:
            replay = lookup_replay(tx, canonical)
            if replay is not None:
                conn.rollback()
                _event("replay_hit", request)
                return replay
            if OPERATIONS[request.operation] is not None:
                validate_preconditions(ReadOnlyTransaction(conn), request)
        elif request.operation != "system.initialize":
            raise RequestError("NOT_INITIALIZED", "Initialize the database explicitly before use.")
        _event("preconditions_passed", request)
        if request.operation == "system.initialize":
            data = initialize_clock(tx, request.time_utc)
        elif request.operation == 'clock.advance':
            data = advance_clock(tx, request.time_utc)
        else:
            from .business import prepare, apply
            prepared = prepare(ReadOnlyTransaction(conn), request)
            data = apply(tx, prepared)
        response = success_response(request.request_id, request.operation, data)
        save_replay(tx, canonical, response)
        commit_started = True
        store.commit(conn)
        _event("committed", request, result=data)
        return response
    except Exception as exc:
        rollback_ok = True
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                rollback_ok = False
            finally:
                try:
                    conn.close()
                except Exception:
                    rollback_ok = False
                conn = None
        if commit_started or not rollback_ok:
            try:
                recovered = store.recover(canonical)
                if recovered is not None:
                    _event("commit_recovered", request)
                    return recovered
                # A consistent fresh read with no record proves this request did not commit.
            except Exception:
                _event("outcome_unknown", request)
                return error_response(RequestError("OUTCOME_UNKNOWN",
                    "Commit outcome cannot be verified; retry with the same key and parameters.", retryable=True), request)
        error = _error_for(exc)
        _event("rejected_or_rolled_back", request, code=error.code)
        return error_response(error, request)
    finally:
        if conn is not None:
            conn.close()


def read_clock(store: Store) -> Response:
    conn = None
    try:
        conn = store.connect(readonly=True)
        conn.execute("BEGIN")
        if not check_schema(conn):
            raise RequestError("NOT_INITIALIZED", "Initialize the database explicitly before use.")
        current, version = ReadOnlyTransaction(conn).clock()
        return success_response(None, "clock.read", {"current_time": current, "clock_version": version})
    except Exception as exc:
        return error_response(_error_for(exc), operation="clock.read")
    finally:
        if conn is not None:
            conn.close()
