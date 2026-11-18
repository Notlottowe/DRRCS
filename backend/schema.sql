-- Applied once by the project owner. The client cannot execute SQL.
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    name TEXT NOT NULL,
    agency TEXT NOT NULL,
    roles TEXT NOT NULL,
    status TEXT NOT NULL,
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username),
    expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
CREATE TABLE IF NOT EXISTS attempts (
    identity TEXT PRIMARY KEY, count INTEGER NOT NULL, until_time REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(kind,id)
);
CREATE TABLE IF NOT EXISTS audit (
    event_id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
    target TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_queue (
    event_id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
    target TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, created_at TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY,value TEXT NOT NULL);
-- D1 batches are transactions. A failed version check aborts the whole batch.
CREATE TABLE IF NOT EXISTS write_checks (
    id TEXT PRIMARY KEY, ok INTEGER NOT NULL CHECK(ok=1)
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS history_no_update BEFORE UPDATE ON history
BEGIN SELECT RAISE(ABORT,'History is append-only'); END;
CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON history
BEGIN SELECT RAISE(ABORT,'History is append-only'); END;
CREATE TRIGGER IF NOT EXISTS final_report_no_update BEFORE UPDATE ON records
WHEN OLD.kind='reports' AND json_extract(OLD.data,'$.status')='Finalized'
BEGIN SELECT RAISE(ABORT,'Finalized report is immutable'); END;
CREATE TRIGGER IF NOT EXISTS final_report_no_delete BEFORE DELETE ON records
WHEN OLD.kind='reports' AND json_extract(OLD.data,'$.status')='Finalized'
BEGIN SELECT RAISE(ABORT,'Finalized report is immutable'); END;
