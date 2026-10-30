"""Explicit version-two extension; version-one tables remain unchanged."""
SCHEMA = (
    'CREATE TABLE tasks (task_id TEXT PRIMARY KEY)',
    'CREATE TABLE task_items (task_id TEXT NOT NULL, material_id TEXT NOT NULL, position INTEGER NOT NULL, requested_time TEXT NOT NULL, PRIMARY KEY(task_id, material_id), UNIQUE(task_id, position))',
    'CREATE TABLE materials (material_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)',
    'CREATE TABLE supplements (source_id TEXT PRIMARY KEY, material_id TEXT NOT NULL, record_json TEXT NOT NULL)',
    'CREATE TABLE rules (rule_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)',
    'CREATE TABLE slots (slot_id TEXT PRIMARY KEY, starts_at TEXT NOT NULL UNIQUE)',
    'CREATE TABLE calendar_state (singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1), dataset_id TEXT NOT NULL, calendar_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1), occupancy_version INTEGER NOT NULL CHECK(occupancy_version>=1))',
    'CREATE TABLE existing_schedules (schedule_id TEXT PRIMARY KEY, slot_id TEXT NOT NULL UNIQUE)',
    "CREATE TABLE drafts (draft_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, material_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1), status TEXT NOT NULL CHECK(status IN ('pending_confirmation','confirmed')), suggested_slot_id TEXT NOT NULL, record_json TEXT NOT NULL, UNIQUE(task_id, material_id))",
    'CREATE TABLE confirmations (confirmation_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL)',
)
INDEX = "CREATE UNIQUE INDEX confirmed_slot ON drafts(suggested_slot_id) WHERE status='confirmed'"


def check_v2(connection, definitions):
    from .contracts import RequestError
    def equivalent(a,b): return isinstance(a,str) and ''.join(a.split()).lower()==''.join(b.split()).lower()
    for statement in SCHEMA:
        if not equivalent(definitions.get(statement.split()[2]),statement):
            raise RequestError('STORAGE_ERROR','Business database schema is incompatible.',retryable=True)
    row=connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='confirmed_slot'").fetchone()
    if not row or not equivalent(row[0],INDEX):
        raise RequestError('STORAGE_ERROR','Business uniqueness index is missing or incompatible.',retryable=True)
