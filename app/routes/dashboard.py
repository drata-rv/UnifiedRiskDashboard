from fastapi import APIRouter, Request

from .. import db, models
from ..templating import templates
from ..user_auth import get_current_user

router = APIRouter()

_TIER_COLS = ["Very High", "High", "Moderate", "Low", "Unscored"]
_VIEWS = ("inherent", "residual")


@router.get("/")
def dashboard(request: Request, view: str = None, sort: str = None, tenant: str = None):
    # An explicit ?view= sets the cookie (persists the choice); otherwise
    # fall back to whatever was last chosen, defaulting to inherent. There's
    # no session store in this app (Easy Auth owns login, not app state), so
    # a plain display-preference cookie is the minimal way to "persist".
    requested_view = view if view in _VIEWS else None
    active_view = requested_view or request.cookies.get("risk_view") or "inherent"
    if active_view not in _VIEWS:
        active_view = "inherent"
    counts_key = "inherent_counts" if active_view == "inherent" else "residual_counts"

    # Tenant filter is a plain query param, not persisted — it's a view filter,
    # not a display preference, so it resets to "All Tenants" every visit.
    all_tenants = db.list_tenants()
    selected_tenant = tenant if tenant in all_tenants else None

    rows = []
    for tenant_name in all_tenants:
        if selected_tenant and tenant_name != selected_tenant:
            continue
        for reg in db.list_registers(tenant_name):
            risks = db.list_risks(tenant_name=tenant_name, register_id=reg["register_id"])
            inherent_counts = {tier: 0 for tier in _TIER_COLS}
            residual_counts = {tier: 0 for tier in _TIER_COLS}
            dirty_count = 0
            for r in risks:
                inherent_counts[models.score_to_tier(r["score"])] += 1
                residual_counts[models.score_to_tier(r["residual_score"])] += 1
                if r["dirty"]:
                    dirty_count += 1
            rows.append({
                "tenant_name": tenant_name,
                "register_id": reg["register_id"],
                "register_name": reg["register_name"],
                "total": len(risks),
                "inherent_counts": inherent_counts,
                "residual_counts": residual_counts,
                "dirty_count": dirty_count,
            })

    if sort == "Total":
        rows.sort(key=lambda r: -r["total"])
    elif sort in _TIER_COLS:
        rows.sort(key=lambda r: -r[counts_key][sort])

    response = templates.TemplateResponse(request, "dashboard.html", {
        "rows": rows,
        "tier_cols": _TIER_COLS,
        "active_view": active_view,
        "counts_key": counts_key,
        "sort": sort,
        "all_tenants": all_tenants,
        "selected_tenant": selected_tenant,
        "user": get_current_user(request),
    })
    if requested_view is not None:
        response.set_cookie("risk_view", requested_view, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response
