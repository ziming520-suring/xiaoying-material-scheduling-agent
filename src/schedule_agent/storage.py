"""Local SQLite lifecycle and replay persistence; no implicit initialization."""
from pathlib import Path
import sqlite3

from .contracts import CanonicalRequest, RequestError, Response, normalize_time, serialize, validate_stored_success

SCHEMA = (
    "CREATE TABLE runtime_meta (singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1), storage_schema_version INTEGER NOT NULL)",
    "CREATE TABLE logical_clock (singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1), current_time_utc TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1))",
    "CREATE TABLE replay_results (operation TEXT NOT NULL, request_id TEXT NOT NULL, request_digest TEXT NOT NULL, canonical_request_json TEXT NOT NULL, response_json TEXT NOT NULL, PRIMARY KEY(operation, request_id))",
)


class ReadOnlyTransaction:
    """Precondition view: offers state reads, no write or commit methods."""
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def clock(self) -> tuple[str, int]:
        row = self._connection.execute("SELECT current_time_utc, version FROM logical_clock WHERE singleton_id=1").fetchone()
        if row is None:
            raise RequestError("STORAGE_ERROR", "Logical clock is missing.", retryable=True)
        return row

    def query(self, sql: str, params: tuple = ()) -> list:
        if not sql.lstrip().upper().startswith('SELECT '):
            raise ValueError('Read-only transaction only accepts SELECT queries.')
        return self._connection.execute(sql, params).fetchall()


class Transaction(ReadOnlyTransaction):
    """Handlers share this transaction and cannot commit through this interface."""
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._connection.execute(sql, params)


def check_schema(connection: sqlite3.Connection) -> bool:
    """Return False only for an empty DB; never repair a foreign/partial schema."""
    definitions = dict(connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    tables = set(definitions)
    if not tables:
        return False
    if not {"runtime_meta", "logical_clock", "replay_results"}.issubset(tables):
        raise RequestError("STORAGE_ERROR", "Database is unrelated or incompletely initialized.", retryable=True)
    # Version 1 has one supported layout, including key/check constraints.
    # Refuse edited/recreated layouts rather than guessing a migration.
    for expected in SCHEMA:
        table = expected.split()[2]
        actual = definitions[table]
        if not isinstance(actual, str) or "".join(actual.split()).lower() != "".join(expected.split()).lower():
            raise RequestError("STORAGE_ERROR", "Database table layout or constraints are incompatible.", retryable=True)
    meta = connection.execute("SELECT singleton_id, storage_schema_version FROM runtime_meta").fetchall()
    if meta not in ([(1, 1)], [(1, 2)]):
        raise RequestError("STORAGE_ERROR", "Storage schema version is incompatible.", retryable=True)
    if meta == [(1, 2)]:
        from .business_schema import check_v2
        check_v2(connection, definitions)
    clocks = connection.execute("SELECT singleton_id, current_time_utc, version FROM logical_clock").fetchall()
    if len(clocks) != 1 or clocks[0][0] != 1 or type(clocks[0][2]) is not int or clocks[0][2] < 1:
        raise RequestError("STORAGE_ERROR", "Logical clock is invalid or missing.", retryable=True)
    try:
        if normalize_time(clocks[0][1], "stored_time") != clocks[0][1]:
            raise ValueError("not canonical")
    except (RequestError, ValueError):
        raise RequestError("STORAGE_ERROR", "Stored time is not canonical UTC.", retryable=True) from None
    connection.execute("SELECT operation, request_id, request_digest, canonical_request_json, response_json FROM replay_results LIMIT 0")
    return True


def lookup_replay(tx: Transaction, request: CanonicalRequest) -> Response | None:
    row = tx.execute("SELECT request_digest, canonical_request_json, response_json FROM replay_results WHERE operation=? AND request_id=?",
                     (request.request.operation, request.request.request_id)).fetchone()
    if row is None:
        return None
    if row[0] != request.digest or row[1] != request.canonical_json:
        raise RequestError("IDEMPOTENCY_CONFLICT", "This request key already belongs to different parameters.", "request_id")
    return validate_stored_success(row[2], request.request)


def save_replay(tx: Transaction, request: CanonicalRequest, response: Response) -> None:
    tx.execute("INSERT INTO replay_results (operation, request_id, request_digest, canonical_request_json, response_json) VALUES (?, ?, ?, ?, ?)",
               (request.request.operation, request.request.request_id, request.digest,
                request.canonical_json, serialize(response)))


class Store:
    def __init__(self, path: str | Path, timeout: float = 5.0):
        self.path = Path(path)
        self.timeout = timeout

    def connect(self, *, create: bool = False, readonly: bool = False) -> sqlite3.Connection:
        if not create and not self.path.exists():
            raise RequestError("NOT_INITIALIZED", "Initialize the database explicitly before use.")
        mode = "ro" if readonly else ("rwc" if create else "rw")
        # URI-encode paths so spaces, '#' and non-ASCII filenames remain literal.
        uri = self.path.resolve().as_uri() + "?mode=" + mode
        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout, isolation_level=None)
        try:
            if not readonly:
                conn.execute("PRAGMA synchronous=FULL")
        except Exception:
            conn.close()
            raise
        return conn

    def commit(self, connection: sqlite3.Connection) -> None:
        connection.commit()

    def recover(self, request: CanonicalRequest) -> Response | None:
        """Read a committed outcome from a fresh connection after a commit error."""
        conn = self.connect(readonly=True)
        try:
            conn.execute("BEGIN")
            if not check_schema(conn):
                return None
            return lookup_replay(Transaction(conn), request)
        finally:
            conn.close()
