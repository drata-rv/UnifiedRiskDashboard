"""
SQLite persistence — one file, stdlib sqlite3, no ORM.

Four tables:
  risks      — cached copy of every risk pulled from Drata, plus a `dirty`
               flag for local edits not yet pushed back.
  locks      — one row per register currently being edited (advisory,
               TTL-based — see locks.py).
  audit_log  — append-only field-level change history, used for rollback.
  users      — per-tenant pool of valid risk owners, cached from /users
               (there's no way to look up valid owners on demand, so the
               whole tenant directory is cached at pull time).
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS risks (
    local_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_name         TEXT NOT NULL,
    register_id         INTEGER NOT NULL,
    register_name       TEXT NOT NULL,
    drata_id            INTEGER NOT NULL,
    risk_code           TEXT,
    title               TEXT,
    description         TEXT,
    impact              INTEGER,
    likelihood          INTEGER,
    score               INTEGER,
    residual_impact     INTEGER,
    residual_likelihood INTEGER,
    residual_score      INTEGER,
    treatment_plan      TEXT,
    treatment_details   TEXT,
    status              TEXT,
    owners_json         TEXT,
    categories_json      TEXT,
    dirty               INTEGER NOT NULL DEFAULT 0,
    last_pulled_at      TEXT,
    last_pushed_at      TEXT,
    UNIQUE(tenant_name, register_id, drata_id)
);

CREATE TABLE IF NOT EXISTS locks (
    register_key   TEXT PRIMARY KEY,
    tenant_name    TEXT NOT NULL,
    register_id    INTEGER NOT NULL,
    locked_by      TEXT NOT NULL,
    locked_at      TEXT NOT NULL,
    heartbeat_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    local_risk_id  INTEGER NOT NULL,
    tenant_name    TEXT NOT NULL,
    risk_code      TEXT,
    field          TEXT NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    changed_by     TEXT NOT NULL,
    changed_at     TEXT NOT NULL,
    rolled_back    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    tenant_name  TEXT NOT NULL,
    user_id      INTEGER NOT NULL,
    name         TEXT NOT NULL,
    email        TEXT,
    PRIMARY KEY (tenant_name, user_id)
);

CREATE TABLE IF NOT EXISTS reassessments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_name   TEXT NOT NULL,
    register_id   INTEGER NOT NULL,
    marked_by     TEXT NOT NULL,
    marked_at     TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    """One connection per thread — FastAPI's threadpool runs sync routes on worker threads."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx():
    """Commit on success, rollback on exception."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Risks
# ---------------------------------------------------------------------------

RISK_COLUMNS = [
    "tenant_name", "register_id", "register_name", "drata_id", "risk_code",
    "title", "description", "impact", "likelihood", "score",
    "residual_impact", "residual_likelihood", "residual_score",
    "treatment_plan", "treatment_details", "status",
    "owners_json", "categories_json",
]


def upsert_risk_from_api(tenant_name: str, register_id: int, register_name: str, risk) -> None:
    """
    Insert or refresh a risk pulled fresh from the Drata API.
    Never overwrites a row with unpushed local edits (dirty=1) — those win
    until the user pushes or explicitly discards them.

    The dirty check and the write are one atomic statement (the WHERE guard
    on DO UPDATE), so a concurrent edit landing between a separate check and
    write can't get silently clobbered by an in-flight pull.
    """
    with tx() as conn:
        existing = conn.execute(
            "SELECT local_id FROM risks WHERE tenant_name=? AND register_id=? AND drata_id=?",
            (tenant_name, register_id, risk.id),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO risks (
                tenant_name, register_id, register_name, drata_id, risk_code,
                title, description, impact, likelihood, score,
                residual_impact, residual_likelihood, residual_score,
                treatment_plan, treatment_details, status,
                owners_json, categories_json, dirty, last_pulled_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
            ON CONFLICT(tenant_name, register_id, drata_id) DO UPDATE SET
                register_name=excluded.register_name, risk_code=excluded.risk_code,
                title=excluded.title, description=excluded.description,
                impact=excluded.impact, likelihood=excluded.likelihood, score=excluded.score,
                residual_impact=excluded.residual_impact,
                residual_likelihood=excluded.residual_likelihood,
                residual_score=excluded.residual_score,
                treatment_plan=excluded.treatment_plan, treatment_details=excluded.treatment_details,
                status=excluded.status, owners_json=excluded.owners_json,
                categories_json=excluded.categories_json, dirty=0, last_pulled_at=excluded.last_pulled_at
            WHERE risks.dirty = 0
            """,
            (
                tenant_name, register_id, register_name, risk.id, risk.risk_id,
                risk.title, risk.description, risk.impact, risk.likelihood, risk.score,
                risk.residual_impact, risk.residual_likelihood, risk.residual_score,
                risk.treatment_plan, risk.treatment_details, risk.status,
                json.dumps(risk.owners), json.dumps(risk.categories), now(),
            ),
        )

        if existing is None:
            new_row = conn.execute(
                "SELECT local_id FROM risks WHERE tenant_name=? AND register_id=? AND drata_id=?",
                (tenant_name, register_id, risk.id),
            ).fetchone()
            _record_baseline(conn, new_row["local_id"], tenant_name, risk.risk_id, risk)


# A risk's audit trail only starts at its first UI edit, which means history
# reconstruction couldn't reach further back than that — not to "as first
# pulled". Recording one baseline row per non-empty editable field (only on
# a genuine insert, never on a refresh) gives reconstruction a real anchor,
# using the exact same audit_log replay mechanism as a normal edit — no
# special-casing needed for "how a risk looked when it appeared".
_BASELINE_SOURCE = "Drata Sync (initial pull)"


def _record_baseline(conn, local_id: int, tenant_name: str, risk_code: str, risk) -> None:
    ts = now()
    fields = {
        "title": risk.title,
        "description": risk.description,
        "impact": risk.impact,
        "likelihood": risk.likelihood,
        "treatment_plan": risk.treatment_plan,
        "treatment_details": risk.treatment_details,
        "status": risk.status,
        "residual_impact": risk.residual_impact,
        "residual_likelihood": risk.residual_likelihood,
        "owners_json": json.dumps(risk.owners) if risk.owners else None,
    }
    for field, value in fields.items():
        if value is None:
            continue
        conn.execute(
            """
            INSERT INTO audit_log (local_risk_id, tenant_name, risk_code, field, old_value, new_value, changed_by, changed_at)
            VALUES (?,?,?,?,NULL,?,?,?)
            """,
            (local_id, tenant_name, risk_code, field, str(value), _BASELINE_SOURCE, ts),
        )


def list_tenants() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT tenant_name FROM risks ORDER BY tenant_name").fetchall()
    return [r["tenant_name"] for r in rows]


def list_registers(tenant_name: str) -> list:
    conn = get_connection()
    return conn.execute(
        "SELECT DISTINCT register_id, register_name FROM risks WHERE tenant_name=? ORDER BY register_name",
        (tenant_name,),
    ).fetchall()


def list_risks(tenant_name: str = None, register_id: int = None) -> list:
    conn = get_connection()
    query = "SELECT * FROM risks WHERE 1=1"
    params = []
    if tenant_name is not None:
        query += " AND tenant_name=?"
        params.append(tenant_name)
    if register_id is not None:
        query += " AND register_id=?"
        params.append(register_id)
    query += " ORDER BY score DESC NULLS LAST, title"
    return conn.execute(query, params).fetchall()


def get_risk(local_id: int):
    conn = get_connection()
    return conn.execute("SELECT * FROM risks WHERE local_id=?", (local_id,)).fetchone()


def list_dirty_risks(tenant_name: str = None, register_id: int = None) -> list:
    conn = get_connection()
    query = "SELECT * FROM risks WHERE dirty=1"
    params = []
    if tenant_name is not None:
        query += " AND tenant_name=?"
        params.append(tenant_name)
    if register_id is not None:
        query += " AND register_id=?"
        params.append(register_id)
    return conn.execute(query, params).fetchall()


EDITABLE_FIELDS = {
    "impact", "likelihood", "treatment_plan", "treatment_details",
    "status", "residual_impact", "residual_likelihood",
    "title", "description", "owners_json",
}


# impact/likelihood (or their residual counterparts) drive score/residual_score —
# recompute locally so the heatmap and table agree immediately, without waiting
# for the next pull. Matches Drata's own score = impact * likelihood formula.
_SCORE_RECOMPUTE = {
    "impact":              ("impact", "likelihood", "score"),
    "likelihood":          ("impact", "likelihood", "score"),
    "residual_impact":     ("residual_impact", "residual_likelihood", "residual_score"),
    "residual_likelihood": ("residual_impact", "residual_likelihood", "residual_score"),
}


def _update_risk_field_on(conn, local_id: int, field: str, new_value):
    """Same as update_risk_field, but runs on a caller-supplied connection/transaction."""
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"{field!r} is not an editable field")
    conn.execute(f"UPDATE risks SET {field}=?, dirty=1 WHERE local_id=?", (new_value, local_id))
    if field in _SCORE_RECOMPUTE:
        imp_col, lik_col, score_col = _SCORE_RECOMPUTE[field]
        row = conn.execute(
            f"SELECT {imp_col}, {lik_col} FROM risks WHERE local_id=?", (local_id,)
        ).fetchone()
        imp, lik = row[imp_col], row[lik_col]
        score = imp * lik if (imp is not None and lik is not None) else None
        conn.execute(f"UPDATE risks SET {score_col}=? WHERE local_id=?", (score, local_id))


def update_risk_field(local_id: int, field: str, new_value):
    """Write one field, mark the row dirty. Caller records the audit entry."""
    with tx() as conn:
        _update_risk_field_on(conn, local_id, field, new_value)


def mark_pushed(local_ids: list):
    if not local_ids:
        return
    with tx() as conn:
        conn.executemany(
            "UPDATE risks SET dirty=0, last_pushed_at=? WHERE local_id=?",
            [(now(), lid) for lid in local_ids],
        )


# ---------------------------------------------------------------------------
# Users — the pool of valid risk owners for a tenant, cached at pull time
# ---------------------------------------------------------------------------

def upsert_user(tenant_name: str, user: dict) -> None:
    with tx() as conn:
        conn.execute(
            """
            INSERT INTO users (tenant_name, user_id, name, email) VALUES (?,?,?,?)
            ON CONFLICT(tenant_name, user_id) DO UPDATE SET name=excluded.name, email=excluded.email
            """,
            (tenant_name, user["id"], user["name"], user["email"]),
        )


def list_users(tenant_name: str) -> list:
    conn = get_connection()
    return conn.execute(
        "SELECT user_id, name, email FROM users WHERE tenant_name=? ORDER BY name",
        (tenant_name,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Reassessments — manual "we reviewed this register" checkpoints. A register
# has no table of its own (register identity is denormalized on `risks`
# rows), so this is intentionally its own small table rather than piggy-
# backing on audit_log, which is per-risk (local_risk_id NOT NULL).
# ---------------------------------------------------------------------------

def record_reassessment(tenant_name: str, register_id: int, marked_by: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO reassessments (tenant_name, register_id, marked_by, marked_at) VALUES (?,?,?,?)",
            (tenant_name, register_id, marked_by, now()),
        )


def latest_reassessment(tenant_name: str, register_id: int):
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM reassessments WHERE tenant_name=? AND register_id=? ORDER BY id DESC LIMIT 1",
        (tenant_name, register_id),
    ).fetchone()


def list_reassessments(tenant_name: str, register_id: int) -> list:
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM reassessments WHERE tenant_name=? AND register_id=? ORDER BY id DESC",
        (tenant_name, register_id),
    ).fetchall()
