import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import audit, db, locks, sync_service
from ..heatmap import build_heatmap, grid_size as compute_grid_size, seq_map
from ..models import score_to_tier
from ..templating import templates
from ..user_auth import get_current_user

router = APIRouter()

_INT_FIELDS = {"impact", "likelihood", "residual_impact", "residual_likelihood"}


@router.get("/tenants/{tenant_name}/registers/{register_id}")
def register_detail(request: Request, tenant_name: str, register_id: int, lock_lost: int = 0):
    user = get_current_user(request)
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)

    can_edit = locks.acquire_or_heartbeat(tenant_name, register_id, user)
    lock_status = locks.get_status(tenant_name, register_id)

    size = compute_grid_size(risks)
    seq = seq_map(risks)
    inherent_grid, inherent_off = build_heatmap(risks, size, "impact", "likelihood", seq)
    residual_grid, residual_off = build_heatmap(risks, size, "residual_impact", "residual_likelihood", seq)

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
        "grid_size": size,
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
