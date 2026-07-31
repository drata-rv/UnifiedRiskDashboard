import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from . import models

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
