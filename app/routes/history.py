from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import audit
from ..templating import templates
from ..user_auth import get_current_user

router = APIRouter()


@router.get("/history")
def history(request: Request, tenant: str = None, rollback_failed: int = 0):
    entries = audit.list_history(tenant_name=tenant)
    return templates.TemplateResponse(request, "history.html", {
        "entries": entries,
        "tenant": tenant,
        "rollback_failed": bool(rollback_failed),
    })


@router.post("/history/{entry_id}/rollback")
def rollback(request: Request, entry_id: int):
    ok = audit.rollback_entry(entry_id, get_current_user(request))
    suffix = "" if ok else "?rollback_failed=1"
    return RedirectResponse(f"/history{suffix}", status_code=303)
