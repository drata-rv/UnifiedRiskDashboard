import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import audit, db, locks, sync_service
from ..models import score_to_tier
from ..templating import templates
from ..user_auth import get_current_user

router = APIRouter()

_TIER_ORDER = {"Very High": 0, "High": 1, "Moderate": 2, "Low": 3, "Unscored": 4}
_INT_FIELDS = {"impact", "likelihood", "residual_impact", "residual_likelihood"}


def _grid_size(risks: list, default: int = 4, cap: int = 7) -> int:
    max_val = default
    for r in risks:
        for field in ("impact", "likelihood", "residual_impact", "residual_likelihood"):
            v = r[field]
            if v is not None:
                max_val = max(max_val, v)
    return min(max_val, cap)


def _seq_map(risks: list) -> dict:
    ordered = sorted(
        risks,
        key=lambda r: (_TIER_ORDER.get(score_to_tier(r["score"]), 99), -(r["score"] or 0), r["title"] or ""),
    )
    return {r["local_id"]: i for i, r in enumerate(ordered, start=1)}


def _build_heatmap(risks: list, grid_size: int, impact_field: str, likelihood_field: str, seq: dict):
    cells = {(i, l): [] for i in range(1, grid_size + 1) for l in range(1, grid_size + 1)}
    off_grid = 0
    for r in risks:
        imp, lik = r[impact_field], r[likelihood_field]
        if imp is None or lik is None:
            continue
        if imp > grid_size or lik > grid_size or imp < 1 or lik < 1:
            off_grid += 1
            continue
        cells[(imp, lik)].append(seq[r["local_id"]])

    grid = []
    for imp in range(grid_size, 0, -1):
        row = []
        for lik in range(1, grid_size + 1):
            ids = cells[(imp, lik)]
            tier = score_to_tier(imp * lik)
            row.append({"impact": imp, "likelihood": lik, "ids": ids, "tier_class": tier.replace(" ", "")})
        grid.append(row)
    return grid, off_grid


@router.get("/tenants/{tenant_name}/registers/{register_id}")
def register_detail(request: Request, tenant_name: str, register_id: int, lock_lost: int = 0):
    user = get_current_user(request)
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)

    can_edit = locks.acquire_or_heartbeat(tenant_name, register_id, user)
    lock_status = locks.get_status(tenant_name, register_id)

    grid_size = _grid_size(risks)
    seq = _seq_map(risks)
    inherent_grid, inherent_off = _build_heatmap(risks, grid_size, "impact", "likelihood", seq)
    residual_grid, residual_off = _build_heatmap(risks, grid_size, "residual_impact", "residual_likelihood", seq)

    tiers = {"Very High": [], "High": [], "Moderate": [], "Low": [], "Unscored": []}
    for r in risks:
        tiers[score_to_tier(r["score"])].append(r)
    for bucket in tiers.values():
        bucket.sort(key=lambda r: -(r["score"] or 0))

    register_name = risks[0]["register_name"] if risks else "Register"

    return templates.TemplateResponse(request, "register_detail.html", {
        "tenant_name": tenant_name,
        "register_id": register_id,
        "register_name": register_name,
        "user": user,
        "can_edit": can_edit,
        "lock_status": lock_status,
        "grid_size": grid_size,
        "inherent_grid": inherent_grid,
        "inherent_off": inherent_off,
        "residual_grid": residual_grid,
        "residual_off": residual_off,
        "tiers": tiers,
        "seq": seq,
        "dirty_count": sum(1 for r in risks if r["dirty"]),
        "lock_lost": bool(lock_lost),
        "users": db.list_users(tenant_name),
    })


@router.post("/tenants/{tenant_name}/registers/{register_id}/risks/{local_id}/edit")
def edit_risk(request: Request, tenant_name: str, register_id: int, local_id: int,
              field: str = Form(...), value: str = Form("")):
    user = get_current_user(request)

    if field not in db.EDITABLE_FIELDS:
        raise HTTPException(400, f"{field!r} is not an editable field")

    row = db.get_risk(local_id)
    if row is None or row["tenant_name"] != tenant_name or row["register_id"] != register_id:
        raise HTTPException(404, "risk not found in this register")

    if not locks.acquire_or_heartbeat(tenant_name, register_id, user):
        return RedirectResponse(f"/tenants/{tenant_name}/registers/{register_id}?lock_lost=1", status_code=303)

    if field == "owners_json":
        # value is the selected user's numeric id (from the owner dropdown),
        # not the JSON itself — look it up against this tenant's cached user
        # directory and build the [{"id", "name"}] shape the row expects.
        old_value = row["owners_json"]
        if value == "":
            new_value = json.dumps([])
        else:
            try:
                user_id = int(value)
            except ValueError:
                raise HTTPException(400, f"{value!r} is not a valid owner id")
            match = next((u for u in db.list_users(tenant_name) if u["user_id"] == user_id), None)
            if match is None:
                raise HTTPException(400, "selected owner not found for this tenant")
            new_value = json.dumps([{"id": match["user_id"], "name": match["name"]}])
    else:
        old_value = row[field]
        new_value = value if value != "" else None
        if field in _INT_FIELDS and new_value is not None:
            try:
                new_value = int(new_value)
            except ValueError:
                raise HTTPException(400, f"{value!r} is not a valid value for {field}")

    db.update_risk_field(local_id, field, new_value)
    audit.record_change(local_id, row["tenant_name"], row["risk_code"], field, old_value, new_value, user)

    return RedirectResponse(f"/tenants/{tenant_name}/registers/{register_id}", status_code=303)


@router.post("/tenants/{tenant_name}/registers/{register_id}/release")
def release_lock(request: Request, tenant_name: str, register_id: int):
    locks.release(tenant_name, register_id, get_current_user(request))
    return RedirectResponse("/", status_code=303)


@router.post("/tenants/{tenant_name}/registers/{register_id}/push")
def push_register(request: Request, tenant_name: str, register_id: int):
    result = sync_service.push_dirty(tenant_name=tenant_name, register_id=register_id)
    return templates.TemplateResponse(request, "sync_result.html", {
        "result": result,
        "back_url": f"/tenants/{tenant_name}/registers/{register_id}",
    })
