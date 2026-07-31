"""
Per-register advisory lock. Whoever opens a register for editing holds it
until they leave (heartbeat stops) or LOCK_TTL_SECONDS passes with no
heartbeat — then it's free again. Simple, no distributed-lock machinery
needed at this scale (single SQLite file, single app instance).
"""

from datetime import datetime, timedelta, timezone

from . import config, db


def _key(tenant_name: str, register_id: int) -> str:
    return f"{tenant_name}:{register_id}"


def _expired(heartbeat_at: str) -> bool:
    ts = datetime.fromisoformat(heartbeat_at)
    return datetime.now(timezone.utc) - ts > timedelta(seconds=config.LOCK_TTL_SECONDS)


def get_status(tenant_name: str, register_id: int):
    """Returns None if free, else {"locked_by": ..., "locked_at": ...}."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM locks WHERE register_key=?", (_key(tenant_name, register_id),)
    ).fetchone()
    if row is None:
        return None
    if _expired(row["heartbeat_at"]):
        release(tenant_name, register_id, row["locked_by"])
        return None
    return {"locked_by": row["locked_by"], "locked_at": row["locked_at"]}


def acquire_or_heartbeat(tenant_name: str, register_id: int, user: str) -> bool:
    """
    Returns True if the caller now holds (or already held) the lock.
    Returns False if someone else holds it.

    The insert-or-heartbeat is one atomic statement (the WHERE guard on
    DO UPDATE means a conflicting row owned by someone else is left
    untouched, not overwritten), and ownership is re-read in the same
    transaction — so the boolean returned always matches who actually
    holds the row, even under concurrent requests from different users.
    """
    key = _key(tenant_name, register_id)
    for _ in range(2):  # one retry, in case the first pass reclaims an expired lock
        ts = db.now()
        with db.tx() as conn:
            conn.execute(
                """
                INSERT INTO locks (register_key, tenant_name, register_id, locked_by, locked_at, heartbeat_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(register_key) DO UPDATE SET heartbeat_at=excluded.heartbeat_at
                WHERE locks.locked_by = excluded.locked_by
                """,
                (key, tenant_name, register_id, user, ts, ts),
            )
            row = conn.execute(
                "SELECT locked_by, heartbeat_at FROM locks WHERE register_key=?", (key,)
            ).fetchone()

        if row["locked_by"] == user:
            return True
        if _expired(row["heartbeat_at"]):
            release(tenant_name, register_id, row["locked_by"])
            continue
        return False
    return False


def release(tenant_name: str, register_id: int, user: str) -> None:
    """Only releases if the caller is the current holder — atomic, no prior read."""
    with db.tx() as conn:
        conn.execute(
            "DELETE FROM locks WHERE register_key=? AND locked_by=?",
            (_key(tenant_name, register_id), user),
        )
