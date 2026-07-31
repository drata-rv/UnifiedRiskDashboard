from fastapi import APIRouter, Request

from .. import sync_service
from ..templating import templates

router = APIRouter()


@router.post("/sync/pull")
def pull(request: Request):
    result = sync_service.pull_all()
    return templates.TemplateResponse(request, "sync_result.html", {
        "result": result,
        "back_url": "/",
    })


@router.post("/sync/push")
def push_all(request: Request):
    result = sync_service.push_dirty()
    return templates.TemplateResponse(request, "sync_result.html", {
        "result": result,
        "back_url": "/",
    })
