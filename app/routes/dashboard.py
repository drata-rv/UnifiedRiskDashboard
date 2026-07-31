from fastapi import APIRouter, Request

from .. import db, models
from ..templating import templates
from ..user_auth import get_current_user

router = APIRouter()

_TIER_COLS = ["Very High", "High", "Moderate", "Low", "Unscored"]


@router.get("/")
def dashboard(request: Request):
    rows = []
    for tenant_name in db.list_tenants():
        for reg in db.list_registers(tenant_name):
            risks = db.list_risks(tenant_name=tenant_name, register_id=reg["register_id"])
            counts = {tier: 0 for tier in _TIER_COLS}
            dirty_count = 0
            for r in risks:
                counts[models.score_to_tier(r["score"])] += 1
                if r["dirty"]:
                    dirty_count += 1
            rows.append({
                "tenant_name": tenant_name,
                "register_id": reg["register_id"],
                "register_name": reg["register_name"],
                "total": len(risks),
                "counts": counts,
                "dirty_count": dirty_count,
            })

    return templates.TemplateResponse(request, "dashboard.html", {
        "rows": rows,
        "tier_cols": _TIER_COLS,
        "user": get_current_user(request),
    })
