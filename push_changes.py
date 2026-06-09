#!/usr/bin/env python3
"""
push_changes.py — sync edited All Risks sheet back to Drata.

Change detection is hash-based: at generation time, refresh.py stores a
12-char SHA1 of each row's 7 editable fields in a hidden _orig_hash column.
This script recomputes the hash from current cell values and pushes any row
where the hash differs.

Usage:
    python push_changes.py [--input dashboard.xlsx] [--tokens tokens.json] [--dry-run] [--verbose]

After a successful push, run refresh.py to regenerate the xlsx so _orig_hash
values reflect the new state.

Editable fields (changes to any of these are detected and pushed):
    Impact, Likelihood, Treatment Plan, Treatment Details,
    Status, Residual Impact, Residual Likelihood

Read-only (not written back — require numeric IDs not stored in Excel):
    Title, Description, Owners, Categories
"""

import argparse
import json
import logging
import sys
from typing import Optional

import requests
from openpyxl import load_workbook

from models import (
    LABEL_TO_STATUS,
    LABEL_TO_TREATMENT,
    parse_level,
    risk_row_hash,
)

REGION_URLS = {
    "us":   "https://public-api.drata.com/public/v2",
    "eu":   "https://public-api.eu.drata.com/public/v2",
    "apac": "https://public-api.apac.drata.com/public/v2",
}

DEFAULT_INPUT  = "dashboard.xlsx"
DEFAULT_TOKENS = "tokens.json"
HEADER_ROW     = 5

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token loading
# ---------------------------------------------------------------------------

def _load_tokens(path: str) -> dict:
    """Return {tenant_name: {"token": ..., "base_url": ...}}."""
    with open(path) as f:
        data = json.load(f)
    result = {}
    for t in data["tenants"]:
        region = t.get("region", "us")
        result[t["name"]] = {
            "token":    t["token"],
            "base_url": REGION_URLS.get(region, REGION_URLS["us"]),
        }
    return result


# ---------------------------------------------------------------------------
# Sheet reading helpers
# ---------------------------------------------------------------------------

def _scan_headers(ws) -> dict:
    """Scan the header row and return {label: col_index (1-based)}."""
    return {
        str(cell.value): cell.column
        for cell in ws[HEADER_ROW]
        if cell.value is not None
    }


def _read_label(ws, row: int, col: int) -> Optional[str]:
    """Read a cell as a display label; return None for blank/dash cells."""
    val = ws.cell(row=row, column=col).value
    if val is None:
        return None
    s = str(val).strip()
    return None if s in ("", "—") else s


def _read_text(ws, row: int, col: int) -> str:
    """Read a free-text cell; return empty string for blank/None."""
    val = ws.cell(row=row, column=col).value
    if val is None:
        return ""
    return str(val).strip()


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def _build_payload(
    impact_label: Optional[str],
    likelihood_label: Optional[str],
    treatment_label: Optional[str],
    treatment_details: str,
    status_label: Optional[str],
    res_impact_label: Optional[str],
    res_likelihood_label: Optional[str],
) -> dict:
    """
    Build the PUT request body from display-label cell values.
    Score and residualScore are computed server-side — never included.
    Enum fields (treatment, status) are omitted if the label doesn't map.
    """
    payload = {}

    impact = parse_level(impact_label)
    if impact is not None:
        payload["impact"] = impact

    likelihood = parse_level(likelihood_label)
    if likelihood is not None:
        payload["likelihood"] = likelihood

    treatment = LABEL_TO_TREATMENT.get(treatment_label) if treatment_label else None
    if treatment:
        payload["treatmentPlan"] = treatment

    # Free text — always include so users can clear an existing value
    payload["treatmentDetails"] = treatment_details

    status = LABEL_TO_STATUS.get(status_label) if status_label else None
    if status:
        payload["status"] = status

    res_impact = parse_level(res_impact_label)
    if res_impact is not None:
        payload["residualImpact"] = res_impact

    res_likelihood = parse_level(res_likelihood_label)
    if res_likelihood is not None:
        payload["residualLikelihood"] = res_likelihood

    return payload


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def _push_risk(
    base_url: str,
    token: str,
    register_id: int,
    risk_id: int,
    payload: dict,
    dry_run: bool,
) -> bool:
    """PUT /risk-registers/{register_id}/risks/{risk_id}. Returns True on success."""
    url = f"{base_url}/risk-registers/{register_id}/risks/{risk_id}"
    if dry_run:
        log.info("  DRY-RUN  PUT %s  fields=%s", url, list(payload.keys()))
        return True
    resp = requests.put(
        url,
        json=payload,
        headers={
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
        },
        timeout=30,
    )
    if resp.ok:
        return True
    log.error("  PUT %s → HTTP %s: %s", url, resp.status_code, resp.text[:300])
    return False


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def push_changes(input_path: str, tokens_path: str, dry_run: bool = False):
    tenant_tokens = _load_tokens(tokens_path)
    log.info("Loaded %d tenant token(s) from %s", len(tenant_tokens), tokens_path)

    wb = load_workbook(input_path, data_only=True)
    if "All Risks" not in wb.sheetnames:
        log.error("'All Risks' sheet not found in %s — was this generated by refresh.py?", input_path)
        sys.exit(1)

    ws = wb["All Risks"]
    headers = _scan_headers(ws)

    required_cols = [
        "Tenant", "_drata_risk_id", "_drata_register_id", "_orig_hash",
        "Impact", "Likelihood", "Treatment Plan", "Treatment Details",
        "Status", "Residual Impact", "Residual Likelihood",
    ]
    missing = [h for h in required_cols if h not in headers]
    if missing:
        log.error(
            "Missing columns in All Risks sheet: %s\n"
            "Regenerate the xlsx with the latest refresh.py before pushing.",
            missing,
        )
        sys.exit(1)

    C = headers  # column index lookup: C["Impact"] → int

    pushed, unchanged, skipped, errors = 0, 0, 0, 0
    warned_tenants = set()  # type: ignore

    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        tenant_name = _read_label(ws, row, C["Tenant"])
        if not tenant_name:
            continue  # blank row at end of data

        risk_id     = ws.cell(row=row, column=C["_drata_risk_id"]).value
        register_id = ws.cell(row=row, column=C["_drata_register_id"]).value
        orig_hash   = ws.cell(row=row, column=C["_orig_hash"]).value

        if not all([risk_id, register_id, orig_hash]):
            log.warning(
                "Row %d (%s): missing metadata — skipping. "
                "Regenerate the xlsx with the latest refresh.py.",
                row, tenant_name,
            )
            skipped += 1
            continue

        # Read current editable cell values
        impact_label         = _read_label(ws, row, C["Impact"])
        likelihood_label     = _read_label(ws, row, C["Likelihood"])
        treatment_label      = _read_label(ws, row, C["Treatment Plan"])
        treatment_details    = _read_text(ws,  row, C["Treatment Details"])
        status_label         = _read_label(ws, row, C["Status"])
        res_impact_label     = _read_label(ws, row, C["Residual Impact"])
        res_likelihood_label = _read_label(ws, row, C["Residual Likelihood"])

        # Recompute hash using API values (same form as at generation time)
        current_hash = risk_row_hash(
            LABEL_TO_TREATMENT.get(treatment_label) if treatment_label else None,
            treatment_details or None,
            LABEL_TO_STATUS.get(status_label) if status_label else None,
            parse_level(impact_label),
            parse_level(likelihood_label),
            parse_level(res_impact_label),
            parse_level(res_likelihood_label),
        )

        if current_hash == orig_hash:
            unchanged += 1
            continue

        # Row was edited — look up tenant token
        if tenant_name not in tenant_tokens:
            if tenant_name not in warned_tenants:
                log.warning("No token configured for tenant %r — skipping all its rows", tenant_name)
                warned_tenants.add(tenant_name)
            skipped += 1
            continue

        tenant_info = tenant_tokens[tenant_name]
        payload = _build_payload(
            impact_label, likelihood_label, treatment_label,
            treatment_details, status_label, res_impact_label, res_likelihood_label,
        )

        if not payload:
            log.warning("Row %d: hash changed but payload is empty — skipping", row)
            skipped += 1
            continue

        log.info(
            "Row %d  [%s  risk_id=%s  register=%s]  pushing %s",
            row, tenant_name, risk_id, register_id, list(payload.keys()),
        )

        ok = _push_risk(
            tenant_info["base_url"], tenant_info["token"],
            int(register_id), int(risk_id),
            payload, dry_run,
        )
        if ok:
            pushed += 1
        else:
            errors += 1

    total_scanned = pushed + unchanged + skipped + errors
    label = "DRY-RUN " if dry_run else ""
    print(f"\n{label}Sync complete — {total_scanned} rows scanned")
    print(f"  Pushed (changed)  : {pushed}")
    print(f"  Unchanged         : {unchanged}")
    print(f"  Skipped           : {skipped}")
    print(f"  Errors            : {errors}")
    if dry_run:
        print("\n  No changes were written to Drata (--dry-run mode).")
    else:
        print("\n  Run refresh.py to regenerate the xlsx with updated hashes.")

    if errors:
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Push edits from the All Risks sheet back to Drata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--input",   default=DEFAULT_INPUT,  metavar="FILE",
                    help=f"Path to dashboard xlsx (default: {DEFAULT_INPUT})")
    ap.add_argument("--tokens",  default=DEFAULT_TOKENS, metavar="FILE",
                    help=f"Path to tokens.json (default: {DEFAULT_TOKENS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect changes and log them without writing to Drata")
    ap.add_argument("--verbose", action="store_true",
                    help="Enable DEBUG logging")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    push_changes(args.input, args.tokens, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
