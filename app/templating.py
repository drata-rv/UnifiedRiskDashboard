import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from . import models

STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["parse_json"] = lambda s: json.loads(s) if s else []
templates.env.filters["level_label"] = models.level_label
templates.env.filters["treatment_label"] = lambda v: models.TREATMENT_LABELS.get(v, v or "—")
templates.env.filters["status_label"] = lambda v: models.STATUS_LABELS.get(v, v or "—")
templates.env.filters["tier"] = models.score_to_tier
templates.env.globals["LEVEL_LABELS"] = models.LEVEL_LABELS
templates.env.globals["TREATMENT_LABELS"] = models.TREATMENT_LABELS
templates.env.globals["STATUS_LABELS"] = models.STATUS_LABELS
templates.env.globals["TIER_COLORS"] = models.TIER_COLORS


def _asset_version() -> str:
    """
    style.css's mtime, stat'd fresh on every call (not cached at import time)
    so any edit — even without a server restart — changes the query string
    and forces browsers to fetch the new file instead of serving a cached
    copy of the old one.
    """
    try:
        return str(int((STATIC_DIR / "style.css").stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["asset_version"] = _asset_version
