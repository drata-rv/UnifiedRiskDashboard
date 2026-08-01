"""
Point-in-time reconstruction of a register's risk state, built entirely off
the existing audit_log — no separate snapshot table. To find how a risk
looked as of some past timestamp, start from its current row and undo every
audit_log entry newer than that timestamp, in reverse order. Rollback
entries need no special handling: audit.rollback_entry logs the rollback
itself as a normal forward edit, so replaying every row (regardless of its
`rolled_back` flag) already reconstructs the correct history.
"""

from datetime import datetime, timezone

from . import db

_INT_FIELDS = {"impact", "likelihood", "residual_impact", "residual_likelihood"}


def date_to_utc_bound(date_str: str) -> str:
    """'YYYY-MM-DD' -> that day's start, in the same ISO/UTC format audit_log timestamps use."""
    return f"{date_str}T00:00:00+00:00"


def now_bound() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce(field: str, raw):
    if raw is None:
        return None
    return int(raw) if field in _INT_FIELDS else raw


def _score(impact, likelihood):
    return impact * likelihood if (impact is not None and likelihood is not None) else None


def reconstruct_register_state(tenant_name: str, register_id: int, as_of: str) -> list:
    """Return every current risk in the register, rewound to how it looked at `as_of` (ISO timestamp)."""
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)
    conn = db.get_connection()
    result = []
    for r in risks:
        state = dict(r)
        entries = conn.execute(
            """
            SELECT field, old_value FROM audit_log
            WHERE local_risk_id = ? AND changed_at > ?
            ORDER BY id DESC
            """,
            (r["local_id"], as_of),
        ).fetchall()
        for e in entries:
            field = e["field"]
            if field not in db.EDITABLE_FIELDS:
                continue
            state[field] = _coerce(field, e["old_value"])
        state["score"] = _score(state["impact"], state["likelihood"])
        state["residual_score"] = _score(state["residual_impact"], state["residual_likelihood"])
        result.append(state)
    return result
