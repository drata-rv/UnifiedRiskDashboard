"""
Excel workbook builder for the FirstService Risk Dashboard.

Generates a multi-sheet workbook with:
  - Dashboard sheet  : overview of all tenants with summary metrics
  - [Tenant] sheets  : one per tenant, with dual heatmap + risk details table
  - All Risks sheet  : consolidated, filterable view across all tenants

Layout decisions:
  - Columns A-M are shared by both the heatmap block (rows 5-11) and the risk
    table (row 13+). Column widths are optimized for the table; the heatmap
    cells end up wide rectangles rather than squares, which is fine for readability.
  - Inherent heatmap uses cols B (impact label) + C-F (grid).
  - Residual heatmap uses col H (label) + I-L (grid).
  - Both heatmaps show risk sequence numbers inside cells; the full details are
    in the risk table below, keyed to the same sequence numbers.
"""

from datetime import datetime
from typing import List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import (
    Alignment,
    Border,
    Font,
    PatternFill,
    Side,
)
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from models import (
    TIER_COLORS,
    TIER_ROW_COLORS,
    Risk,
    TenantData,
    heatmap_cell_color,
    level_label,
    score_to_tier,
)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

FONT_NAME = "Calibri"

# Default grid scale for FirstService (4×4 per their CC Excel draft).
# _heatmap_scale() detects the actual scale from live data and uses whichever
# is larger — so a 4-level config stays at 4×4, but a 5-level config gets 5×5.
DEFAULT_GRID_SIZE = 4

# Level display labels.  Keys beyond 5 fall back to "Level N".
LEVEL_SHORT = {
    1: "Low",
    2: "Moderate",
    3: "High",
    4: "Very High",
    5: "Critical",
}


def _grid_size(risks: list) -> int:
    """
    Return the grid dimension to use for this risk set.

    Takes the max of DEFAULT_GRID_SIZE and the largest impact/likelihood value
    actually present in the data, so a standard 4×4 tenant stays 4×4 while a
    tenant that has been configured with a 5+ level scale gets a bigger grid.
    Capped at 7 to keep the heatmap readable.
    """
    max_val = DEFAULT_GRID_SIZE
    for r in risks:
        for val in (r.impact, r.likelihood, r.residual_impact, r.residual_likelihood):
            if val is not None:
                max_val = max(max_val, val)
    return min(max_val, 7)   # cap at 7 × 7


def _level_label_short(level: int) -> str:
    return LEVEL_SHORT.get(level, f"L{level}")

# Heatmap block anchor positions (1-indexed, as openpyxl expects)
HEATMAP_START_ROW    = 5   # First row of the heatmap section on each tenant sheet
HEATMAP_INHERENT_COL = 2   # Col B — impact labels for inherent heatmap
# Residual heatmap column is computed dynamically from grid_size in _build_tenant_sheet
# to ensure the two heatmaps never overlap regardless of grid scale.

# Column index → semantic meaning (for the risk details table)
COL_SEQ          = 1   # A  — sequence #
COL_STATEMENT    = 2   # B  — risk title (also impact labels in heatmap)
COL_IMPACT       = 3   # C  — inherent impact level  (also heatmap grid col 1)
COL_LIKELIHOOD   = 4   # D  — inherent likelihood    (also heatmap grid col 2)
COL_SCORE        = 5   # E  — inherent score         (also heatmap grid col 3)
COL_TREATMENT    = 6   # F  — treatment plan         (also heatmap grid col 4)
COL_DETAILS      = 7   # G  — treatment details (wide; visual gap between heatmaps)
COL_STATUS       = 8   # H  — status                 (also residual label col)
COL_RES_IMPACT   = 9   # I  — residual impact        (also residual grid col 1)
COL_RES_LIKELY   = 10  # J  — residual likelihood    (also residual grid col 2)
COL_RES_SCORE    = 11  # K  — residual score         (also residual grid col 3)
COL_OWNERS       = 12  # L  — owners
COL_CATEGORIES   = 13  # M  — categories

TABLE_LAST_COL = COL_CATEGORIES

# Column widths (chars). Wide cols serve the table; heatmap adapts.
COLUMN_WIDTHS = {
    COL_SEQ:       5,
    COL_STATEMENT: 45,   # Risk Statement (wide) / impact labels in heatmap
    COL_IMPACT:    13,
    COL_LIKELIHOOD:13,
    COL_SCORE:      8,
    COL_TREATMENT: 20,
    COL_DETAILS:   40,   # Treatment details (wide) / visual gap in heatmap
    COL_STATUS:    13,
    COL_RES_IMPACT:13,
    COL_RES_LIKELY:13,
    COL_RES_SCORE:  8,
    COL_OWNERS:    22,
    COL_CATEGORIES:22,
}

# Table column headers (row 13 on tenant sheets)
TABLE_HEADERS = [
    ("#",             COL_SEQ),
    ("Risk Statement",COL_STATEMENT),
    ("Impact",        COL_IMPACT),
    ("Likelihood",    COL_LIKELIHOOD),
    ("Score",         COL_SCORE),
    ("Treatment Plan",COL_TREATMENT),
    ("Treatment Details", COL_DETAILS),
    ("Status",        COL_STATUS),
    ("Residual Impact",   COL_RES_IMPACT),
    ("Residual Likelihood", COL_RES_LIKELY),
    ("Residual Score",    COL_RES_SCORE),
    ("Owners",        COL_OWNERS),
    ("Categories",    COL_CATEGORIES),
]

# Brand colors
COLOR_HEADER_BG   = "1F3864"   # Dark navy — main header rows
COLOR_HEADER_FG   = "FFFFFF"   # White text on dark header
COLOR_SUBHEADER   = "2E75B6"   # Medium blue — section subheaders
COLOR_TABLE_HEAD  = "D6DCE4"   # Light grey-blue — table column headers
COLOR_DASHBOARD_BG= "1F3864"   # Same navy for Dashboard title

# Sheet tab colors matching tier severity
TAB_COLORS = {
    "Very High": "FF0000",
    "High":      "FF8C00",
    "Moderate":  "FFFF00",
    "Low":       "70AD47",
    "Unscored":  "A6A6A6",
}


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

def _font(bold: bool = False, size: int = 11, color: str = "000000") -> Font:
    return Font(name=FONT_NAME, bold=bold, size=size, color=color)


def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def _thin_border() -> Border:
    thin = Side(style="thin", color="BFBFBF")
    return Border(left=thin, right=thin, top=thin, bottom=thin)


def _center() -> Alignment:
    return Alignment(horizontal="center", vertical="center", wrap_text=True)


def _left_wrap() -> Alignment:
    return Alignment(horizontal="left", vertical="top", wrap_text=True)


def _apply_cell(
    ws: Worksheet,
    row: int,
    col: int,
    value=None,
    bold: bool = False,
    font_size: int = 11,
    font_color: str = "000000",
    bg_color: Optional[str] = None,
    align: Optional[Alignment] = None,
    border: bool = False,
    number_format: Optional[str] = None,
):
    """Write a value to a cell and apply formatting in one call."""
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = _font(bold=bold, size=font_size, color=font_color)
    if bg_color:
        cell.fill = _fill(bg_color)
    cell.alignment = align or _center()
    if border:
        cell.border = _thin_border()
    if number_format:
        cell.number_format = number_format
    return cell


def _merge_header(
    ws: Worksheet,
    row: int,
    col_start: int,
    col_end: int,
    text: str,
    bg_color: str = COLOR_HEADER_BG,
    font_size: int = 12,
    font_color: str = COLOR_HEADER_FG,
    bold: bool = True,
):
    """Write a merged header cell spanning col_start to col_end."""
    ws.merge_cells(
        start_row=row, start_column=col_start,
        end_row=row, end_column=col_end,
    )
    _apply_cell(
        ws, row, col_start, value=text,
        bold=bold, font_size=font_size, font_color=font_color,
        bg_color=bg_color, align=_center(),
    )


# ---------------------------------------------------------------------------
# Heatmap drawing helper
# ---------------------------------------------------------------------------

def _draw_heatmap(
    ws: Worksheet,
    risks: List[Risk],
    start_row: int,
    label_col: int,
    grid_col_start: int,
    mode: str,          # "inherent" or "residual"
    title: str,
    grid_size: int = DEFAULT_GRID_SIZE,
):
    """
    Draw a single N×N risk heatmap block onto a worksheet.

    Layout (rows relative to start_row):
      +0: Section title  (merged across label col + N grid cols)
      +1: Likelihood axis headers
      +2 … +(N+1): Grid rows, one per impact level, top = highest
      +(N+2): "← Likelihood →" axis label

    Cell content: sequence numbers for risks landing in that cell.
    When > 5 risks share a cell, show "N risks" to keep cells readable.
    Risks whose impact/likelihood falls outside the grid are silently skipped
    (they still appear in the risk details table below).
    """
    # Build levels lists dynamically from grid_size
    impact_levels     = list(range(grid_size, 0, -1))   # e.g. [4,3,2,1]
    likelihood_levels = list(range(1, grid_size + 1))   # e.g. [1,2,3,4]
    grid_col_end = grid_col_start + grid_size - 1

    # Map (impact, likelihood) → list of risk sequence numbers in this cell
    cell_risks: dict = {}
    skipped = 0
    for i, risk in enumerate(risks, start=1):
        imp = risk.impact      if mode == "inherent" else risk.residual_impact
        lik = risk.likelihood  if mode == "inherent" else risk.residual_likelihood
        if imp is None or lik is None:
            continue
        if imp < 1 or imp > grid_size or lik < 1 or lik > grid_size:
            skipped += 1
            continue
        cell_risks.setdefault((imp, lik), []).append(i)

    # Row 0: Section title
    ws.merge_cells(
        start_row=start_row, start_column=label_col,
        end_row=start_row, end_column=grid_col_end,
    )
    title_text = title
    if skipped:
        title_text += f"  ({skipped} risks outside grid scale — see table)"
    _apply_cell(
        ws, start_row, label_col,
        value=title_text,
        bold=True, font_size=11, font_color=COLOR_HEADER_FG,
        bg_color=COLOR_SUBHEADER, align=_center(),
    )

    # Row 1: Likelihood axis header row
    header_row = start_row + 1
    _apply_cell(
        ws, header_row, label_col,
        value="Impact  ↓  /  Likelihood  →",
        bold=True, font_size=9, bg_color=COLOR_TABLE_HEAD,
        align=Alignment(horizontal="center", vertical="center"),
    )
    for col_offset, level in enumerate(likelihood_levels):
        _apply_cell(
            ws, header_row, grid_col_start + col_offset,
            value=_level_label_short(level),
            bold=True, font_size=9, bg_color=COLOR_TABLE_HEAD,
            align=_center(), border=True,
        )

    # Rows 2 … N+1: One row per impact level (highest impact at top)
    for row_offset, impact_level in enumerate(impact_levels):
        data_row = start_row + 2 + row_offset
        ws.row_dimensions[data_row].height = 65   # tall enough for multi-line content

        # Impact axis label
        _apply_cell(
            ws, data_row, label_col,
            value=_level_label_short(impact_level),
            bold=True, font_size=9, bg_color=COLOR_TABLE_HEAD,
            align=Alignment(horizontal="right", vertical="center"),
        )

        for col_offset, likelihood_level in enumerate(likelihood_levels):
            col = grid_col_start + col_offset
            cell_color = heatmap_cell_color(impact_level, likelihood_level)
            seqs = cell_risks.get((impact_level, likelihood_level), [])

            # For clarity: show individual numbers for ≤5 risks, count for more
            if not seqs:
                cell_text = ""
            elif len(seqs) <= 5:
                cell_text = "\n".join(f"#{n}" for n in seqs)
            else:
                cell_text = f"{len(seqs)} risks"

            cell = ws.cell(row=data_row, column=col, value=cell_text)
            cell.fill = _fill(cell_color)
            cell.font = _font(bold=bool(seqs), size=9)
            cell.alignment = _center()
            cell.border = _thin_border()

    # Last row: Likelihood axis footer label
    axis_row = start_row + 2 + grid_size
    ws.merge_cells(
        start_row=axis_row, start_column=grid_col_start,
        end_row=axis_row, end_column=grid_col_end,
    )
    _apply_cell(
        ws, axis_row, grid_col_start,
        value="← Likelihood →",
        bold=False, font_size=8, font_color="595959",
        align=_center(),
    )


# ---------------------------------------------------------------------------
# Risk table drawing helper
# ---------------------------------------------------------------------------

def _draw_risk_table(
    ws: Worksheet,
    risks: List[Risk],
    start_row: int,
    include_tenant_col: bool = False,
    tenant_name: str = "",
):
    """
    Draw the detailed risk register table starting at start_row.

    Risks are grouped by severity tier (Very High → High → Moderate → Low →
    Unscored), sorted by score descending within each tier.

    When include_tenant_col is True, a "Tenant" column is prepended at col 1
    and all other columns shift right by one. Used for the All Risks sheet.
    """
    offset = 1 if include_tenant_col else 0

    # Section title
    _merge_header(
        ws, start_row, 1, TABLE_LAST_COL + offset,
        "KEY RISKS AND MANAGEMENT ACTIVITIES",
        font_size=11,
    )

    # Column headers
    header_row = start_row + 1
    if include_tenant_col:
        _apply_cell(
            ws, header_row, 1, "Tenant",
            bold=True, bg_color=COLOR_TABLE_HEAD, border=True, align=_center(),
        )
    for label, col in TABLE_HEADERS:
        _apply_cell(
            ws, header_row, col + offset,
            value=label,
            bold=True, bg_color=COLOR_TABLE_HEAD,
            border=True, align=_center(),
        )

    # Group and sort risks
    tiers = ["Very High", "High", "Moderate", "Low", "Unscored"]
    current_row = header_row + 1

    for tier in tiers:
        tier_risks = sorted(
            [r for r in risks if r.severity_tier == tier],
            key=lambda r: (-(r.score or 0), r.title),
        )
        if not tier_risks:
            continue

        # Tier header row
        tier_bg = TIER_ROW_COLORS[tier]
        _merge_header(
            ws, current_row, 1, TABLE_LAST_COL + offset,
            f"{tier} Risk{'s' if tier != 'Unscored' else ''}",
            bg_color=tier_bg,
            font_color="000000",
            font_size=10,
        )
        current_row += 1

        for seq, risk in enumerate(tier_risks, start=1):
            # Sequence number continues across the entire table
            row = current_row
            ws.row_dimensions[row].height = 75   # Tall rows for wrapped text

            if include_tenant_col:
                _apply_cell(ws, row, 1, tenant_name, border=True, align=_center())

            # Risk title (statement). Description goes in a cell note if long.
            full_statement = risk.title
            if risk.description:
                full_statement += "\n\n" + risk.description

            _apply_cell(ws, row, COL_SEQ + offset,       seq,                      border=True, align=_center())
            _apply_cell(ws, row, COL_STATEMENT + offset, full_statement,           border=True, align=_left_wrap())
            _apply_cell(ws, row, COL_IMPACT + offset,    risk.impact_label,        border=True, align=_center(),
                        bg_color=TIER_COLORS.get(score_to_tier(risk.impact * risk.likelihood if risk.impact and risk.likelihood else None), None))
            _apply_cell(ws, row, COL_LIKELIHOOD + offset,risk.likelihood_label,    border=True, align=_center())
            _apply_cell(ws, row, COL_SCORE + offset,     risk.score,               border=True, align=_center())
            _apply_cell(ws, row, COL_TREATMENT + offset, risk.treatment_label,     border=True, align=_center())
            _apply_cell(ws, row, COL_DETAILS + offset,   risk.treatment_details,   border=True, align=_left_wrap())
            _apply_cell(ws, row, COL_STATUS + offset,    risk.status_label,        border=True, align=_center())
            _apply_cell(ws, row, COL_RES_IMPACT + offset,risk.residual_impact_label, border=True, align=_center())
            _apply_cell(ws, row, COL_RES_LIKELY + offset,risk.residual_likelihood_label, border=True, align=_center())
            _apply_cell(ws, row, COL_RES_SCORE + offset, risk.residual_score,      border=True, align=_center())
            _apply_cell(ws, row, COL_OWNERS + offset,    ", ".join(risk.owners),   border=True, align=_center())
            _apply_cell(ws, row, COL_CATEGORIES + offset,", ".join(risk.categories), border=True, align=_center())

            current_row += 1

    return current_row


# ---------------------------------------------------------------------------
# Sheet builders
# ---------------------------------------------------------------------------

def _set_column_widths(ws: Worksheet, offset: int = 0):
    """Apply the standard column widths to a worksheet."""
    if offset:
        # All Risks sheet has an extra Tenant col at position 1
        ws.column_dimensions["A"].width = 22
    for col_idx, width in COLUMN_WIDTHS.items():
        col_letter = get_column_letter(col_idx + offset)
        ws.column_dimensions[col_letter].width = width


def _build_sheet_header(
    ws: Worksheet,
    title: str,
    subtitle: str,
    refreshed_at: str,
    last_col: int,
):
    """Write the top 3-row header block common to all sheets."""
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 16

    _merge_header(ws, 1, 1, last_col, title, font_size=14)
    _merge_header(ws, 2, 1, last_col, subtitle, bg_color=COLOR_SUBHEADER, font_size=11)
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=last_col)
    _apply_cell(
        ws, 3, 1,
        value=f"Last refreshed: {refreshed_at}",
        bold=False, font_size=9, font_color="595959",
        align=Alignment(horizontal="right", vertical="center"),
    )


def _build_tenant_sheet(
    wb: Workbook,
    tenant: TenantData,
    refreshed_at: str,
):
    """
    Build one sheet for a single tenant. Layout:
      Rows 1–3   : Header (tenant name, refresh timestamp)
      Row  4     : Spacer
      Rows 5–11  : Dual heatmap block (inherent left, residual right)
      Rows 12–13 : Spacer
      Row  14+   : Risk details table
    """
    # Sanitize sheet name (Excel max 31 chars, no special chars)
    sheet_name = tenant.name[:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "").replace("]", "")
    ws = wb.create_sheet(title=sheet_name)

    _set_column_widths(ws)

    # Tab color based on overall risk posture
    ws.sheet_properties.tabColor = TAB_COLORS.get(tenant.highest_tier, "A6A6A6")

    # Freeze panes are set after heatmap/table layout is computed (see below)

    # Header rows
    _build_sheet_header(
        ws,
        title="FIRSTSERVICE — ENTERPRISE RISK MANAGEMENT",
        subtitle=tenant.name,
        refreshed_at=refreshed_at,
        last_col=TABLE_LAST_COL,
    )
    ws.row_dimensions[4].height = 8   # Spacer

    risks = tenant.all_risks

    if not risks:
        ws.merge_cells(start_row=5, start_column=1, end_row=5, end_column=TABLE_LAST_COL)
        _apply_cell(
            ws, 5, 1,
            value="No risks loaded for this tenant. Populate the Risk Register in Drata and run refresh.",
            bold=False, font_size=10, font_color="595959",
            align=Alignment(horizontal="center", vertical="center"),
        )
        return ws

    # Detect the actual grid scale from this tenant's data.
    # Stays at 4 for standard FS tenants; grows for larger Drata configurations.
    size = _grid_size(risks)

    # Heatmap block height = grid_size rows + 3 overhead rows (title, header, footer)
    heatmap_height = size + 3

    # Inherent heatmap: label in col B (index 2), grid in cols C onward.
    # Width = 1 (label col) + size (grid cols).
    inherent_label_col = HEATMAP_INHERENT_COL        # col B = 2
    inherent_grid_start = inherent_label_col + 1     # col C = 3
    inherent_end_col    = inherent_grid_start + size - 1

    _draw_heatmap(
        ws, risks,
        start_row=HEATMAP_START_ROW,
        label_col=inherent_label_col,
        grid_col_start=inherent_grid_start,
        mode="inherent",
        title="INHERENT RISK",
        grid_size=size,
    )

    # Residual heatmap starts 2 columns after the inherent heatmap ends,
    # so they never overlap regardless of grid scale.
    residual_label_col = inherent_end_col + 2
    residual_grid_start = residual_label_col + 1

    has_residual = any(r.residual_impact is not None for r in risks)
    if has_residual:
        _draw_heatmap(
            ws, risks,
            start_row=HEATMAP_START_ROW,
            label_col=residual_label_col,
            grid_col_start=residual_grid_start,
            mode="residual",
            title="RESIDUAL RISK",
            grid_size=size,
        )
    else:
        # Placeholder when residual scores haven't been entered in Drata yet
        res_end_col = residual_grid_start + size - 1
        ws.merge_cells(
            start_row=HEATMAP_START_ROW, start_column=residual_label_col,
            end_row=HEATMAP_START_ROW + heatmap_height, end_column=res_end_col,
        )
        _apply_cell(
            ws, HEATMAP_START_ROW, residual_label_col,
            value="RESIDUAL RISK\n\nResidual scores not yet entered in Drata.\nSet Residual Impact and Residual Likelihood on each risk to populate this view.",
            bold=True, font_size=10, font_color="595959",
            bg_color="F2F2F2",
            align=Alignment(horizontal="center", vertical="center", wrap_text=True),
        )

    # Dynamic spacer rows between heatmap block and risk table
    spacer_start = HEATMAP_START_ROW + heatmap_height + 1
    for r in range(spacer_start, spacer_start + 2):
        ws.row_dimensions[r].height = 8

    # Risk details table starts after heatmap block + spacer rows
    table_start = HEATMAP_START_ROW + heatmap_height + 3

    # Freeze: lock header rows and the seq# + statement columns so the user
    # can scroll right through the wide table while keeping risk context visible.
    # +2 = skip the section title row and column header row to land on first data row.
    first_data_row = table_start + 2
    ws.freeze_panes = f"{get_column_letter(COL_IMPACT)}{first_data_row}"

    _draw_risk_table(ws, risks, start_row=table_start)

    return ws


def _build_dashboard_sheet(
    wb: Workbook,
    tenants: List[TenantData],
    refreshed_at: str,
):
    """
    Build the Dashboard overview sheet — the first sheet in the workbook.

    Shows one row per tenant with summary risk counts and a link to the
    tenant's detail sheet. Tenants with fetch errors are shown in red.
    """
    ws = wb.create_sheet(title="Dashboard", index=0)
    _set_column_widths(ws)

    _build_sheet_header(
        ws,
        title="FIRSTSERVICE — ENTERPRISE RISK MANAGEMENT",
        subtitle="Unified Dashboard  ·  All Tenants",
        refreshed_at=refreshed_at,
        last_col=TABLE_LAST_COL,
    )
    ws.row_dimensions[4].height = 8

    # Summary table header row
    DASH_HEADERS = [
        ("Tenant",       6),
        ("Total Risks",  3),
        ("Very High",    3),
        ("High",         2),
        ("Moderate",     2),
        ("Low",          2),
        ("Unscored",     2),
    ]

    # Build column positions for dashboard table dynamically
    dash_col_positions = []
    cur_col = 1
    for label, span in DASH_HEADERS:
        dash_col_positions.append((label, cur_col, cur_col + span - 1))
        cur_col += span

    header_row = 5
    ws.row_dimensions[header_row].height = 20
    for label, col_start, col_end in dash_col_positions:
        if col_start == col_end:
            _apply_cell(
                ws, header_row, col_start, label,
                bold=True, bg_color=COLOR_TABLE_HEAD, border=True, align=_center(),
            )
        else:
            ws.merge_cells(
                start_row=header_row, start_column=col_start,
                end_row=header_row, end_column=col_end,
            )
            _apply_cell(
                ws, header_row, col_start, label,
                bold=True, bg_color=COLOR_TABLE_HEAD, border=True, align=_center(),
            )

    # One data row per tenant
    for row_offset, tenant in enumerate(tenants):
        data_row = header_row + 1 + row_offset
        ws.row_dimensions[data_row].height = 18

        counts = tenant.risk_counts
        row_data = [
            (tenant.name,           1),   # tenant name col start
            (counts["Total"],       None),
            (counts["Very High"],   None),
            (counts["High"],        None),
            (counts["Moderate"],    None),
            (counts["Low"],         None),
            (counts["Unscored"],    None),
        ]

        if tenant.error:
            # Error row — show the error message in red spanning all cols
            err_col_start = dash_col_positions[0][1]
            err_col_end   = dash_col_positions[-1][2]
            ws.merge_cells(
                start_row=data_row, start_column=err_col_start,
                end_row=data_row, end_column=err_col_end,
            )
            _apply_cell(
                ws, data_row, err_col_start,
                value=f"⚠ {tenant.name}: {tenant.error}",
                bold=False, font_size=9, font_color="FF0000",
                border=True, align=_left_wrap(),
            )
            continue

        # Tenant name with hyperlink to its detail sheet
        sheet_name = tenant.name[:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "").replace("]", "")
        tenant_col_start = dash_col_positions[0][1]
        tenant_col_end   = dash_col_positions[0][2]
        if tenant_col_start != tenant_col_end:
            ws.merge_cells(
                start_row=data_row, start_column=tenant_col_start,
                end_row=data_row, end_column=tenant_col_end,
            )
        cell = ws.cell(row=data_row, column=tenant_col_start, value=tenant.name)
        cell.hyperlink = f"#{sheet_name}!A1"
        cell.font = Font(
            name=FONT_NAME, bold=True, size=10,
            color="1F3864", underline="single",
        )
        cell.alignment = _left_wrap()
        cell.border = _thin_border()

        # Tier count cells — color-coded background
        tier_colors_order = [None, None, "FFCCCC", "FFE0B3", "FFFACD", "D6EFD8", "F2F2F2"]
        for field_idx, (val, _) in enumerate(row_data[1:], start=1):
            col_start = dash_col_positions[field_idx][1]
            col_end   = dash_col_positions[field_idx][2]
            bg = tier_colors_order[field_idx] if field_idx < len(tier_colors_order) else None
            if col_start != col_end:
                ws.merge_cells(
                    start_row=data_row, start_column=col_start,
                    end_row=data_row, end_column=col_end,
                )
            _apply_cell(
                ws, data_row, col_start, val,
                bold=(val > 0 if isinstance(val, int) else False),
                font_size=10,
                bg_color=bg if (isinstance(val, int) and val > 0) else None,
                border=True, align=_center(),
            )

    # Footer note
    footer_row = header_row + 1 + len(tenants) + 1
    ws.merge_cells(
        start_row=footer_row, start_column=1,
        end_row=footer_row, end_column=TABLE_LAST_COL,
    )
    _apply_cell(
        ws, footer_row, 1,
        value="Click a tenant name to jump to its detail sheet.  "
              "See 'All Risks' sheet for a consolidated filterable view.",
        bold=False, font_size=9, font_color="595959",
        align=Alignment(horizontal="left", vertical="center"),
    )


def _build_all_risks_sheet(
    wb: Workbook,
    tenants: List[TenantData],
    refreshed_at: str,
):
    """
    Build the consolidated 'All Risks' sheet — every risk from every tenant
    in a single filterable table, with a 'Tenant' column prepended.
    """
    ws = wb.create_sheet(title="All Risks")
    _set_column_widths(ws, offset=1)   # Tenant col shifts everything right by 1

    _build_sheet_header(
        ws,
        title="FIRSTSERVICE — ENTERPRISE RISK MANAGEMENT",
        subtitle="All Risks  ·  Consolidated View",
        refreshed_at=refreshed_at,
        last_col=TABLE_LAST_COL + 1,
    )
    ws.row_dimensions[4].height = 8

    # Flatten all risks from all successful tenants into one table
    current_row = 5
    all_risks_exist = False

    for tenant in tenants:
        if tenant.error or not tenant.all_risks:
            continue
        all_risks_exist = True
        current_row = _draw_risk_table(
            ws,
            tenant.all_risks,
            start_row=current_row,
            include_tenant_col=True,
            tenant_name=tenant.name,
        )
        current_row += 1   # Spacer between tenants

    if not all_risks_exist:
        ws.merge_cells(start_row=5, start_column=1, end_row=5, end_column=TABLE_LAST_COL + 1)
        _apply_cell(
            ws, 5, 1,
            value="No risk data available. Check that tokens are valid and risks have been entered in Drata.",
            bold=False, font_size=10, font_color="595959",
            align=Alignment(horizontal="center", vertical="center"),
        )

    ws.freeze_panes = "B6"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

class ExcelBuilder:
    """
    Orchestrates building the full multi-sheet dashboard workbook.

    Usage:
        builder = ExcelBuilder()
        builder.build(tenants, output_path="dashboard.xlsx")
    """

    def build(
        self,
        tenants: List[TenantData],
        output_path: str,
        refreshed_at: Optional[str] = None,
    ) -> str:
        """
        Build the workbook from the provided tenant data and save it.
        Returns the output path on success.
        """
        if refreshed_at is None:
            refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        wb = Workbook()
        # Remove the default empty sheet that openpyxl creates
        wb.remove(wb.active)

        # Sheet order: Dashboard first, then one per tenant, then All Risks last
        _build_dashboard_sheet(wb, tenants, refreshed_at)

        for tenant in tenants:
            if tenant.error:
                # Still create a sheet for errored tenants so the tab is visible
                ws = wb.create_sheet(title=tenant.name[:31])
                ws.sheet_properties.tabColor = "A6A6A6"
                _build_sheet_header(
                    ws,
                    title="FIRSTSERVICE — ENTERPRISE RISK MANAGEMENT",
                    subtitle=tenant.name,
                    refreshed_at=refreshed_at,
                    last_col=TABLE_LAST_COL,
                )
                ws.merge_cells(start_row=5, start_column=1, end_row=5, end_column=TABLE_LAST_COL)
                _apply_cell(
                    ws, 5, 1,
                    value=f"⚠ Could not fetch data for this tenant.\nError: {tenant.error}",
                    bold=False, font_size=10, font_color="FF0000",
                    align=Alignment(horizontal="center", vertical="center", wrap_text=True),
                )
            else:
                _build_tenant_sheet(wb, tenant, refreshed_at)

        _build_all_risks_sheet(wb, tenants, refreshed_at)

        wb.save(output_path)
        return output_path
