"""
Data models for the FirstService Risk Dashboard.

These classes represent the normalized data pulled from the Drata API.
The label maps here are the single source of truth for Drata enum → display name
translation. Update them if Drata changes its enum values.
"""

from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Drata API enum → human-readable label mappings
# ---------------------------------------------------------------------------

# Impact / Likelihood numeric levels (Drata returns integers 1–4 for 4-level
# configurations; 1–5 for 5-level. We support both.
LEVEL_LABELS = {
    1: "Low",
    2: "Moderate",
    3: "High",
    4: "Very High",
    5: "Critical",
    6: "Severe",
    7: "Catastrophic",
}
LABEL_TO_LEVEL = {v: k for k, v in LEVEL_LABELS.items()}

# Drata treatmentPlan enum values.
# NOTE: Drata uses MITIGATE (not REDUCE) and TRANSFER (not SHARE).
# The customer's Excel draft uses REDUCE/SHARE — map here for display.
TREATMENT_LABELS = {
    "MITIGATE":  "Mitigate (Reduce)",
    "TRANSFER":  "Transfer (Share)",
    "AVOID":     "Avoid",
    "ACCEPT":    "Accept",
    "UNTREATED": "Untreated",
}
LABEL_TO_TREATMENT = {v: k for k, v in TREATMENT_LABELS.items()}

# Drata status enum values for a Risk.
STATUS_LABELS = {
    "ACTIVE":   "Active",
    "ARCHIVED": "Archived",
    "CLOSED":   "Closed",
}
LABEL_TO_STATUS = {v: k for k, v in STATUS_LABELS.items()}

# Score thresholds for severity tier grouping (inherent score = impact × likelihood).
# Derived from the customer's Excel draft (California Closets ERM, 2026):
#   Very High: 12–16 (e.g. VH×H or VH×VH)
#   High:       6–11 (e.g. H×H=9, VH×M=8, H×M=6)
#   Moderate:   3–5  (e.g. VH×L=4, M×M=4)
#   Low:        1–2  (e.g. L×L=1, L×M=2)
SCORE_TIERS = [
    (12, "Very High"),
    (6,  "High"),
    (3,  "Moderate"),
    (0,  "Low"),
]

# Heatmap cell background colors per tier (hex, no leading #).
TIER_COLORS = {
    "Very High": "FF0000",   # Red
    "High":      "FF8C00",   # Orange
    "Moderate":  "FFFF00",   # Yellow
    "Low":       "70AD47",   # Green
    "Unscored":  "D9D9D9",   # Light grey
}

# Softer fill colors for the tier header rows in the risk details table.
TIER_ROW_COLORS = {
    "Very High": "FFCCCC",
    "High":      "FFE0B3",
    "Moderate":  "FFFACD",
    "Low":       "D6EFD8",
    "Unscored":  "F2F2F2",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def score_to_tier(score: Optional[int]) -> str:
    """Return the severity tier label for a given inherent or residual score."""
    if score is None:
        return "Unscored"
    for threshold, label in SCORE_TIERS:
        if score >= threshold:
            return label
    return "Low"


def level_label(level: Optional[int]) -> str:
    """Return the display label for a numeric impact/likelihood level."""
    if level is None:
        return "—"
    return LEVEL_LABELS.get(level, str(level))


def parse_level(cell_value) -> Optional[int]:
    """Parse an Excel cell value (display label or raw number) back to a Drata integer level."""
    if cell_value is None:
        return None
    s = str(cell_value).strip()
    if s in ("—", "", "None"):
        return None
    if s in LABEL_TO_LEVEL:
        return LABEL_TO_LEVEL[s]
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def risk_row_hash(
    treatment_plan, treatment_details, status,
    impact, likelihood, residual_impact, residual_likelihood,
) -> str:
    """
    Stable 12-char hash of the 7 editable fields.
    Stored in the Excel at generation time; recomputed at sync time to detect changes.
    Both sides must use raw API values (enums + integers), not display labels.
    """
    import hashlib
    parts = "|".join(str(x if x is not None else "") for x in [
        treatment_plan, treatment_details, status,
        impact, likelihood, residual_impact, residual_likelihood,
    ])
    return hashlib.sha1(parts.encode()).hexdigest()[:12]


def heatmap_cell_color(impact: Optional[int], likelihood: Optional[int]) -> str:
    """
    Return the hex fill color for a heatmap grid cell based on its coordinates.
    Uses inherent score logic: color is determined by impact × likelihood.
    """
    if impact is None or likelihood is None:
        return TIER_COLORS["Unscored"]
    return TIER_COLORS[score_to_tier(impact * likelihood)]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Risk:
    """A single risk record as returned by the Drata API."""

    id: int
    risk_id: str              # Drata's alphanumeric ID, e.g. "R-001"
    title: str
    description: str
    impact: Optional[int]     # 1–5, nullable until scored
    likelihood: Optional[int] # 1–5, nullable until scored
    score: Optional[int]      # impact × likelihood, nullable
    residual_impact: Optional[int]
    residual_likelihood: Optional[int]
    residual_score: Optional[int]
    treatment_plan: Optional[str]    # MITIGATE / TRANSFER / AVOID / ACCEPT / UNTREATED
    treatment_details: Optional[str] # Free-text description of activities
    status: Optional[str]            # ACTIVE / ARCHIVED / CLOSED
    categories: List[str] = field(default_factory=list)
    owners: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience properties (display-ready strings)
    # ------------------------------------------------------------------

    @property
    def impact_label(self) -> str:
        return level_label(self.impact)

    @property
    def likelihood_label(self) -> str:
        return level_label(self.likelihood)

    @property
    def residual_impact_label(self) -> str:
        return level_label(self.residual_impact)

    @property
    def residual_likelihood_label(self) -> str:
        return level_label(self.residual_likelihood)

    @property
    def treatment_label(self) -> str:
        return TREATMENT_LABELS.get(self.treatment_plan, self.treatment_plan or "—")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status or "—")

    @property
    def severity_tier(self) -> str:
        """Tier grouping label based on inherent score."""
        return score_to_tier(self.score)

    @property
    def residual_tier(self) -> str:
        """Tier grouping label based on residual score."""
        return score_to_tier(self.residual_score)


@dataclass
class RiskRegister:
    """A Risk Register within a Drata tenant."""

    id: int
    name: str
    description: Optional[str]
    risks: List[Risk] = field(default_factory=list)


@dataclass
class TenantData:
    """
    All risk data collected for a single Drata tenant.

    'error' is set when the API fetch failed for this tenant; the dashboard
    will show an error row for it rather than crashing the entire run.
    """

    name: str
    registers: List[RiskRegister] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def all_risks(self) -> List[Risk]:
        """Flat list of every risk across all registers in this tenant."""
        return [risk for reg in self.registers for risk in reg.risks]

    @property
    def risk_counts(self) -> dict:
        """Summary counts by severity tier, useful for the dashboard overview."""
        risks = self.all_risks
        return {
            "Very High": sum(1 for r in risks if r.severity_tier == "Very High"),
            "High":      sum(1 for r in risks if r.severity_tier == "High"),
            "Moderate":  sum(1 for r in risks if r.severity_tier == "Moderate"),
            "Low":       sum(1 for r in risks if r.severity_tier == "Low"),
            "Unscored":  sum(1 for r in risks if r.severity_tier == "Unscored"),
            "Total":     len(risks),
        }

    @property
    def highest_tier(self) -> str:
        """The worst severity tier present in this tenant, for tab color coding."""
        for _, label in SCORE_TIERS:   # iterate labels, not threshold numbers
            if any(r.severity_tier == label for r in self.all_risks):
                return label
        return "Unscored"
