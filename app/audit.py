"""
Field-level audit log — every edit is recorded, any single field change
can be rolled back. Rollback is itself logged (append-only), not a delete.
"""

from . import db, locks


def _record_change_on(conn, local_risk_id: int, tenant_name: str, risk_code: str,
                       field: str, old_value, new_value, changed_by: str) -> None:
    """Same as record_change, but runs on a caller-supplied connection/transaction."""
    if old_value == new_value:
        return
    conn.execute(
        """
        INSERT INTO audit_log (local_risk_id, tenant_name, risk_code, field,
                                old_value, new_value, changed_by, changed_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (local_risk_id, tenant_name, risk_code, field,
         str(old_value) if old_value is not None else None,
         str(new_value) if new_value is not None else None,
         changed_by, db.now()),
    )


def record_change(local_risk_id: int, tenant_name: str, risk_code: str,
                   field: str, old_value, new_value, changed_by: str) -> None:
    with db.tx() as conn:
        _record_change_on(conn, local_risk_id, tenant_name, risk_code, field, old_value, new_value, changed_by)


def list_history(tenant_name: str = None, limit: int = 200) -> list:
    conn = db.get_connection()
    query = "SELECT * FROM audit_log"
    params = []
    if tenant_name is not None:
        query += " WHERE tenant_name=?"
        params.append(tenant_name)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


def get_entry(entry_id: int):
    conn = db.get_connection()
    return conn.execute("SELECT * FROM audit_log WHERE id=?", (entry_id,)).fetchone()


def rollback_entry(entry_id: int, performed_by: str) -> bool:
    """
    Reverts one field to its pre-change value and marks the row dirty again
    (so it can be re-pushed). Records a new audit entry documenting the revert,
    all as one atomic transaction. Returns False if:
      - the entry doesn't exist or was already rolled back
      - a later, un-rolled-back edit already changed this field further
        (rolling back would silently clobber that newer change)
      - the register is currently locked to someone else
    """
    entry = get_entry(entry_id)
    if entry is None or entry["rolled_back"]:
        return False

    field = entry["field"]
    old_value = entry["old_value"]
    risk = db.get_risk(entry["local_risk_id"])
    if risk is None:
        return False

    current_value = risk[field]
    current_value_str = str(current_value) if current_value is not None else None
    if current_value_str != entry["new_value"]:
        return False  # superseded by a newer edit — refuse rather than silently overwrite

    lock = locks.get_status(entry["tenant_name"], risk["register_id"])
    if lock and lock["locked_by"] != performed_by:
        return False  # register is being actively edited by someone else

    with db.tx() as conn:
        db._update_risk_field_on(conn, entry["local_risk_id"], field, old_value)
        _record_change_on(
            conn, entry["local_risk_id"], entry["tenant_name"], entry["risk_code"],
            field, current_value, old_value, performed_by,
        )
        conn.execute("UPDATE audit_log SET rolled_back=1 WHERE id=?", (entry_id,))
    return True
