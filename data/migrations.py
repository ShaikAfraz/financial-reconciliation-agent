"""
Forward-only SQLite Migration Engine.
Tracks applied migrations in `schema_migrations`.
Enforces foreign keys, WAL mode, and busy timeout on every connection.
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Tuple, Callable, Optional

def configure_pragmas(conn: sqlite3.Connection) -> None:
    """Configures critical SQLite pragmas for safety, concurrency, and integrity."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA busy_timeout = 5000;")

def _table_has_column(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name});")
    cols = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in cursor.fetchall()}
    return column_name in cols

def _migration_001_base_schema(conn: sqlite3.Connection) -> None:
    """Initial core reconciliation and agent tables."""
    cursor = conn.cursor()
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS budget_rules (
        category TEXT PRIMARY KEY,
        monthly_limit REAL NOT NULL,
        category_type TEXT NOT NULL,
        flexibility_weight REAL NOT NULL,
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        raw_descriptor TEXT NOT NULL,
        clean_merchant TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        account_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'posted',
        is_anomaly INTEGER NOT NULL DEFAULT 0,
        anomaly_type TEXT,
        anomaly_details TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        run_id TEXT NOT NULL,
        step_index INTEGER NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT,
        output_json TEXT,
        duration_ms REAL,
        provider TEXT,
        model TEXT,
        tokens INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS bank_records (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        description TEXT NOT NULL,
        reference TEXT
    );

    CREATE TABLE IF NOT EXISTS ledger_records (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        description TEXT NOT NULL,
        reference TEXT
    );

    CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        method TEXT NOT NULL,
        confidence REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS match_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL,
        side TEXT NOT NULL,
        record_id TEXT NOT NULL,
        UNIQUE (match_id, side, record_id)
    );

    CREATE TABLE IF NOT EXISTS exceptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id TEXT NOT NULL,
        side TEXT NOT NULL,
        type TEXT NOT NULL,
        status TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        reviewer_note TEXT,
        ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_txns_date_cat ON transactions(date, category);
    CREATE INDEX IF NOT EXISTS idx_bank_records_date ON bank_records(date);
    CREATE INDEX IF NOT EXISTS idx_ledger_records_date ON ledger_records(date);
    CREATE INDEX IF NOT EXISTS idx_bank_records_amt ON bank_records(amount_cents);
    CREATE INDEX IF NOT EXISTS idx_ledger_records_amt ON ledger_records(amount_cents);
    CREATE INDEX IF NOT EXISTS idx_match_members_match ON match_members(match_id);
    CREATE INDEX IF NOT EXISTS idx_exceptions_record ON exceptions(record_id, side);
    CREATE INDEX IF NOT EXISTS idx_audit_logs_run ON audit_logs(run_id);
    """)

    # Check and migrate audit_logs columns if needed
    for col, col_type in [("provider", "TEXT"), ("model", "TEXT"), ("tokens", "INTEGER DEFAULT 0")]:
        if not _table_has_column(cursor, "audit_logs", col):
            cursor.execute(f"ALTER TABLE audit_logs ADD COLUMN {col} {col_type}")

def _migration_002_auth_and_persistence(conn: sqlite3.Connection) -> None:
    """Users, sessions, runs, import tables, login throttling, and record ownership."""
    cursor = conn.cursor()
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        email TEXT,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        hash_params TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'analyst',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        csrf_token TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'completed',
        settings_json TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id);

    CREATE TABLE IF NOT EXISTS import_batches (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        run_id TEXT REFERENCES runs(id),
        side TEXT NOT NULL DEFAULT 'bank',
        record_type TEXT DEFAULT 'bank',
        filename TEXT NOT NULL,
        row_count INTEGER NOT NULL DEFAULT 0,
        total_records INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_import_batches_user ON import_batches(user_id);

    CREATE TABLE IF NOT EXISTS import_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
        row_index INTEGER NOT NULL,
        raw_data_json TEXT NOT NULL,
        record_id TEXT,
        status TEXT DEFAULT 'pending',
        error_message TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_import_rows_batch ON import_rows(batch_id);

    CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        ip_address TEXT,
        attempt_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        success INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup ON login_attempts(username, ip_address, attempt_time);
    """)

    # Add user_id and batch_id to records/matches/exceptions/reviews/audit_logs if missing
    table_alterations = [
        ("bank_records", "user_id", "TEXT REFERENCES users(id)"),
        ("bank_records", "batch_id", "TEXT REFERENCES import_batches(id)"),
        ("ledger_records", "user_id", "TEXT REFERENCES users(id)"),
        ("ledger_records", "batch_id", "TEXT REFERENCES import_batches(id)"),
        ("matches", "user_id", "TEXT REFERENCES users(id)"),
        ("matches", "run_id", "TEXT REFERENCES runs(id)"),
        ("match_members", "user_id", "TEXT REFERENCES users(id)"),
        ("exceptions", "user_id", "TEXT REFERENCES users(id)"),
        ("exceptions", "run_id", "TEXT REFERENCES runs(id)"),
        ("reviews", "user_id", "TEXT REFERENCES users(id)"),
        ("audit_logs", "user_id", "TEXT REFERENCES users(id)"),
        ("users", "email", "TEXT")
    ]

    for table, col, col_def in table_alterations:
        if not _table_has_column(cursor, table, col):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")

    # Add indexes for performance and scoping
    cursor.executescript("""
    CREATE INDEX IF NOT EXISTS idx_bank_records_user ON bank_records(user_id);
    CREATE INDEX IF NOT EXISTS idx_ledger_records_user ON ledger_records(user_id);
    CREATE INDEX IF NOT EXISTS idx_matches_user ON matches(user_id);
    CREATE INDEX IF NOT EXISTS idx_exceptions_user ON exceptions(user_id);
    CREATE INDEX IF NOT EXISTS idx_reviews_user ON reviews(user_id);
    CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id);
    """)

def _migration_003_add_user_email(conn: sqlite3.Connection) -> None:
    """Adds email column to users table if missing and creates index."""
    cursor = conn.cursor()
    if not _table_has_column(cursor, "users", "email"):
        cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

def _migration_004_import_batch_fields(conn: sqlite3.Connection) -> None:
    """Adds record_type, total_records to import_batches, and status, error_message to import_rows."""
    cursor = conn.cursor()
    if not _table_has_column(cursor, "import_batches", "record_type"):
        cursor.execute("ALTER TABLE import_batches ADD COLUMN record_type TEXT DEFAULT 'bank'")
    if not _table_has_column(cursor, "import_batches", "total_records"):
        cursor.execute("ALTER TABLE import_batches ADD COLUMN total_records INTEGER DEFAULT 0")
    if not _table_has_column(cursor, "import_rows", "status"):
        cursor.execute("ALTER TABLE import_rows ADD COLUMN status TEXT DEFAULT 'pending'")
    if not _table_has_column(cursor, "import_rows", "error_message"):
        cursor.execute("ALTER TABLE import_rows ADD COLUMN error_message TEXT")

def _migration_005_csv_workflow(conn: sqlite3.Connection) -> None:
    """
    Expands runs, import_batches, import_rows, bank_records, ledger_records,
    and creates import_row_edits table for the CSV transaction workflow.
    """
    cursor = conn.cursor()

    # runs table extensions
    run_columns = [
        ("owner_user_id", "TEXT REFERENCES users(id)"),
        ("currency", "TEXT DEFAULT 'USD'"),
        ("period_start", "TEXT"),
        ("period_end", "TEXT"),
        ("bank_opening_cents", "INTEGER DEFAULT 0"),
        ("bank_closing_cents", "INTEGER DEFAULT 0"),
        ("ledger_opening_cents", "INTEGER DEFAULT 0"),
        ("ledger_closing_cents", "INTEGER DEFAULT 0"),
    ]
    for col, col_def in run_columns:
        if not _table_has_column(cursor, "runs", col):
            cursor.execute(f"ALTER TABLE runs ADD COLUMN {col} {col_def}")

    if _table_has_column(cursor, "runs", "user_id"):
        cursor.execute("UPDATE runs SET owner_user_id = user_id WHERE owner_user_id IS NULL AND user_id IS NOT NULL")

    # import_batches table extensions
    batch_columns = [
        ("owner_user_id", "TEXT REFERENCES users(id)"),
        ("sha256", "TEXT"),
        ("size_bytes", "INTEGER DEFAULT 0"),
        ("status", "TEXT DEFAULT 'staged'"),
        ("date_format", "TEXT"),
        ("mapping_json", "TEXT"),
        ("confirmed_at", "DATETIME"),
        ("confirmed_by", "TEXT REFERENCES users(id)")
    ]
    for col, col_def in batch_columns:
        if not _table_has_column(cursor, "import_batches", col):
            cursor.execute(f"ALTER TABLE import_batches ADD COLUMN {col} {col_def}")

    if _table_has_column(cursor, "import_batches", "user_id"):
        cursor.execute("UPDATE import_batches SET owner_user_id = user_id WHERE owner_user_id IS NULL AND user_id IS NOT NULL")

    # import_rows table extensions
    row_columns = [
        ("row_number", "INTEGER DEFAULT 0"),
        ("raw_line", "TEXT DEFAULT ''"),
        ("date_iso", "TEXT"),
        ("description", "TEXT"),
        ("raw_description", "TEXT"),
        ("reference", "TEXT"),
        ("amount_cents", "INTEGER"),
        ("currency", "TEXT"),
        ("issues_json", "TEXT DEFAULT '[]'"),
        ("edited", "INTEGER DEFAULT 0"),
        ("duplicate_of_row", "INTEGER")
    ]
    for col, col_def in row_columns:
        if not _table_has_column(cursor, "import_rows", col):
            cursor.execute(f"ALTER TABLE import_rows ADD COLUMN {col} {col_def}")

    # bank_records and ledger_records extensions
    for tbl in ["bank_records", "ledger_records"]:
        if not _table_has_column(cursor, tbl, "run_id"):
            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN run_id TEXT REFERENCES runs(id)")
        if not _table_has_column(cursor, tbl, "source_row_id"):
            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN source_row_id INTEGER REFERENCES import_rows(id)")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{tbl}_run ON {tbl}(run_id)")

    # audit_logs duration_ms
    if not _table_has_column(cursor, "audit_logs", "duration_ms"):
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN duration_ms INTEGER")

    # Create import_row_edits table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS import_row_edits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        row_id INTEGER NOT NULL REFERENCES import_rows(id) ON DELETE CASCADE,
        field TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        user_id TEXT NOT NULL REFERENCES users(id),
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_row_edits_row ON import_row_edits(row_id);")

    # Indices for performance and constraints
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs(owner_user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_batches_run_side ON import_batches(run_id, side);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_batches_sha256 ON import_batches(sha256);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_rows_batch_status ON import_rows(batch_id, status);")

def _migration_006_workflow_storage_compat(conn: sqlite3.Connection) -> None:
    """Adds workflow fields while retaining the original reconciliation table keys."""
    cursor = conn.cursor()
    audit_columns = [
        ("action", "TEXT"), ("details_json", "TEXT"),
    ]
    for name, kind in audit_columns:
        if not _table_has_column(cursor, "audit_logs", name):
            cursor.execute(f"ALTER TABLE audit_logs ADD COLUMN {name} {kind}")
    match_columns = [("match_type", "TEXT"), ("rule_score", "REAL"), ("ai_rationale", "TEXT")]
    for name, kind in match_columns:
        if not _table_has_column(cursor, "matches", name):
            cursor.execute(f"ALTER TABLE matches ADD COLUMN {name} {kind}")
    if not _table_has_column(cursor, "import_batches", "delimiter"):
        cursor.execute("ALTER TABLE import_batches ADD COLUMN delimiter TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_run ON matches(run_id)")

def _migration_007_public_registration(conn: sqlite3.Connection) -> None:
    """Adds isolated persistent throttling for public account registration."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS registration_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip_address TEXT,
        attempt_time DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_registration_attempts_ip_time
        ON registration_attempts(ip_address, attempt_time);
    """)

def _migration_008_ai_run_insights(conn: sqlite3.Connection) -> None:
    """Stores validated, user-scoped advisory AI output for each run."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ai_run_insights (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_ai_run_insights_user ON ai_run_insights(user_id, created_at);
    """)

MIGRATIONS: List[Tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "001_base_schema", _migration_001_base_schema),
    (2, "002_auth_and_persistence", _migration_002_auth_and_persistence),
    (3, "003_add_user_email", _migration_003_add_user_email),
    (4, "004_import_batch_fields", _migration_004_import_batch_fields),
    (5, "005_csv_workflow", _migration_005_csv_workflow),
    (6, "006_workflow_storage_compat", _migration_006_workflow_storage_compat),
    (7, "007_public_registration", _migration_007_public_registration),
    (8, "008_ai_run_insights", _migration_008_ai_run_insights),
]

def run_migrations(conn: sqlite3.Connection) -> None:
    """Executes all unapplied migrations sequentially in a transaction."""
    configure_pragmas(conn)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()

    cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
    applied_versions = {row[0] for row in cursor.fetchall()}

    for version, name, migration_fn in MIGRATIONS:
        if version not in applied_versions:
            migration_fn(conn)
            cursor.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?);",
                (version, name)
            )
            conn.commit()
