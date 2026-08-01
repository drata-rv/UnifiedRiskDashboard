from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import db, export_service, history_service

router = APIRouter()


def _register_name(risks: list) -> str:
    return risks[0]["register_name"] if risks else "Register"


def _validate_view(view):
    if view not in (None, "inherent", "residual"):
        raise HTTPException(400, "view must be 'inherent' or 'residual'")


def _attachment(data: bytes, media_type: str, filename: str) -> Response:
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
    })


@router.get("/tenants/{tenant_name}/registers/{register_id}/export/heatmap.png")
def export_heatmap_png(tenant_name: str, register_id: int, view: str = None):
    _validate_view(view)
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)
    register_name = _register_name(risks)
    data = export_service.render_heatmap(risks, register_name, fmt="png", view=view)
    filename = export_service.stamped_filename(tenant_name, register_name, "heatmap", "png")
    return _attachment(data, "image/png", filename)


@router.get("/tenants/{tenant_name}/registers/{register_id}/export/heatmap.pdf")
def export_heatmap_pdf(tenant_name: str, register_id: int, view: str = None):
    _validate_view(view)
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)
    register_name = _register_name(risks)
    data = export_service.render_heatmap(risks, register_name, fmt="pdf", view=view)
    filename = export_service.stamped_filename(tenant_name, register_name, "heatmap", "pdf")
    return _attachment(data, "application/pdf", filename)


@router.get("/tenants/{tenant_name}/registers/{register_id}/export/risks.csv")
def export_risks_csv(tenant_name: str, register_id: int):
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)
    register_name = _register_name(risks)
    rows = export_service.build_register_rows(tenant_name, register_id)
    data = export_service.rows_to_csv(rows)
    filename = export_service.stamped_filename(tenant_name, register_name, "risks", "csv")
    return _attachment(data, "text/csv", filename)


@router.get("/tenants/{tenant_name}/registers/{register_id}/export/risks.xlsx")
def export_risks_xlsx(tenant_name: str, register_id: int):
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)
    register_name = _register_name(risks)
    rows = export_service.build_register_rows(tenant_name, register_id)
    data = export_service.rows_to_xlsx(rows, register_name)
    filename = export_service.stamped_filename(tenant_name, register_name, "risks", "xlsx")
    return _attachment(data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename)


@router.get("/tenants/{tenant_name}/registers/{register_id}/export/board-report.pdf")
def export_board_report(tenant_name: str, register_id: int, since: str = None):
    if not since:
        since = history_service.default_since_date(tenant_name, register_id)
    risks = db.list_risks(tenant_name=tenant_name, register_id=register_id)
    register_name = _register_name(risks)
    data = export_service.build_board_report(tenant_name, register_id, since)
    filename = export_service.stamped_filename(tenant_name, register_name, "board_report", "pdf")
    return _attachment(data, "application/pdf", filename)
