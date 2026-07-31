"""
Pull from Drata into the local cache; push locally-edited (dirty) rows back.

Both are explicit, human-triggered actions (buttons in the UI) — never
automatic. Confirmed live against the API: title, description, and owners
(as [{"id": <int>}]) ARE editable via PUT. Only score/residualScore are
truly read-only (server-computed). Categories are treated as read-only here
not because the API forbids it, but because there's no confirmed endpoint
to enumerate valid category ids to build a picker from.
"""

import json
import logging

import requests

from . import config, db
from .drata_auth import load_tokens
from .drata_client import DrataClient

log = logging.getLogger(__name__)


def pull_all() -> dict:
    """Fetch every tenant's registers + risks from Drata, upsert into the cache."""
    tenants = load_tokens(config.TOKENS_PATH, azure=(config.TOKENS_MODE == "azure"))
    results = {"tenants": 0, "registers": 0, "risks": 0, "errors": []}

    for t in tenants:
        base_url = config.REGION_URLS.get(t.get("region", "us"), config.REGION_URLS["us"])
        client = DrataClient(t["token"], base_url=base_url)
        registers, error = client.fetch_all_data()
        if error:
            results["errors"].append(f"{t['name']}: {error}")
            continue

        results["tenants"] += 1
        for register in registers:
            results["registers"] += 1
            for risk in register.risks:
                db.upsert_risk_from_api(t["name"], register.id, register.name, risk)
                results["risks"] += 1

        for user in client.get_users():
            db.upsert_user(t["name"], user)

    return results


def push_dirty(tenant_name: str = None, register_id: int = None) -> dict:
    """PUT every dirty row (optionally scoped to one tenant/register) back to Drata."""
    tenants = load_tokens(config.TOKENS_PATH, azure=(config.TOKENS_MODE == "azure"))
    tenant_tokens = {t["name"]: t for t in tenants}

    rows = db.list_dirty_risks(tenant_name=tenant_name, register_id=register_id)
    pushed_count, errors = 0, []

    for row in rows:
        t = tenant_tokens.get(row["tenant_name"])
        if t is None:
            errors.append(f"{row['risk_code']}: no token configured for tenant {row['tenant_name']!r}")
            continue

        base_url = config.REGION_URLS.get(t.get("region", "us"), config.REGION_URLS["us"])
        url = f"{base_url}/risk-registers/{row['register_id']}/risks/{row['drata_id']}"
        payload = _build_payload(row)

        try:
            resp = requests.put(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {t['token']}", "Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            # Mark pushed immediately — if a later row in this batch fails or the
            # process is interrupted, rows already confirmed by Drata don't get
            # stranded showing as still-dirty (and re-pushing them wouldn't be safe
            # anyway if the value changed in Drata directly in the meantime).
            db.mark_pushed([row["local_id"]])
            pushed_count += 1
        except requests.exceptions.RequestException as exc:
            errors.append(f"{row['risk_code']}: {exc}")

    return {"pushed": pushed_count, "errors": errors}


def _build_payload(row) -> dict:
    """
    Only non-null editable fields are sent — confirmed against the live API
    that Drata's PUT schema rejects null for impact/likelihood/treatmentPlan/
    status/residualImpact/residualLikelihood (422 Unprocessable Entity), so
    there is no way to push a "clear this field back to blank" edit through
    this endpoint. That's a real, inherent limitation: if a user blanks one
    of these fields locally, that specific field's clear cannot be synced to
    Drata (the other fields on the same row still push normally). Surfacing
    that clearly is safer than guessing and breaking every push on the row.

    title/description are always sent (they're never legitimately blank).
    owners is sent as [{"id": ...}] — confirmed that shape is required;
    plain integers are rejected. Omitted (not sent as []) when unset, for
    the same null-rejection reason as the scored fields above.
    """
    payload = {
        "treatmentDetails": row["treatment_details"] or "",
        "title": row["title"] or "",
        "description": row["description"] or "",
    }
    for field, api_field in [
        ("impact", "impact"),
        ("likelihood", "likelihood"),
        ("treatment_plan", "treatmentPlan"),
        ("status", "status"),
        ("residual_impact", "residualImpact"),
        ("residual_likelihood", "residualLikelihood"),
    ]:
        value = row[field]
        if value is not None:
            payload[api_field] = value

    owners = json.loads(row["owners_json"]) if row["owners_json"] else []
    if owners:
        payload["owners"] = [{"id": o["id"]} for o in owners]

    return payload
