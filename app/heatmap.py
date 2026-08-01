"""
Shared heatmap-grid math — used by register_detail's live view and by the
export/board-report renderers. Keeping this in one place means every
consumer computes tiers/cell-buckets identically.
"""

from .models import score_to_tier

_TIER_ORDER = {"Very High": 0, "High": 1, "Moderate": 2, "Low": 3, "Unscored": 4}


def grid_size(risks: list, default: int = 4, cap: int = 7) -> int:
    max_val = default
    for r in risks:
        for field in ("impact", "likelihood", "residual_impact", "residual_likelihood"):
            v = r[field]
            if v is not None:
                max_val = max(max_val, v)
    return min(max_val, cap)


def seq_map(risks: list) -> dict:
    ordered = sorted(
        risks,
        key=lambda r: (_TIER_ORDER.get(score_to_tier(r["score"]), 99), -(r["score"] or 0), r["title"] or ""),
    )
    return {r["local_id"]: i for i, r in enumerate(ordered, start=1)}


def build_heatmap(risks: list, size: int, impact_field: str, likelihood_field: str, seq: dict):
    cells = {(i, l): [] for i in range(1, size + 1) for l in range(1, size + 1)}
    off_grid = 0
    for r in risks:
        imp, lik = r[impact_field], r[likelihood_field]
        if imp is None or lik is None:
            continue
        if imp > size or lik > size or imp < 1 or lik < 1:
            off_grid += 1
            continue
        cells[(imp, lik)].append(seq[r["local_id"]])

    grid = []
    for imp in range(size, 0, -1):
        row = []
        for lik in range(1, size + 1):
            ids = cells[(imp, lik)]
            tier = score_to_tier(imp * lik)
            row.append({"impact": imp, "likelihood": lik, "ids": ids, "tier_class": tier.replace(" ", "")})
        grid.append(row)
    return grid, off_grid
