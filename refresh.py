"""
FirstService Risk Dashboard — Refresh Script

Usage:
    python refresh.py                              # local tokens.json → dashboard.xlsx
    python refresh.py --output my.xlsx             # custom output path
    python refresh.py --dry-run                    # validate tokens & connectivity only
    python refresh.py --verbose                    # show per-tenant API call details
    python refresh.py --interactive                # rich terminal UI (demo mode)
    python refresh.py --azure                      # fetch tokens from Azure Key Vault
    python refresh.py --azure --tokens my.json     # custom Azure manifest path

Token sources:
    Local (default):  tokens.json — token strings stored directly (see tokens.example.json)
    Azure (--azure):  tokens.azure.json manifest + AZURE_VAULT_URL env var (see tokens.azure.example.json)

Who runs this:
    A Drata admin with tokens for each tenant (Christian, Brian, Ian, or Adam).
    The output Excel file is then shared with the GRC team (Stephanie, Daniel, etc.)
    who open it like any other Excel file — no Python required on their machines.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from auth import load_tokens
from drata_client import DrataClient
from excel_builder import ExcelBuilder
from models import TenantData

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Region → base URL mapping
# ---------------------------------------------------------------------------

REGION_URLS = {
    "us":   "https://public-api.drata.com/public/v2",
    "eu":   "https://public-api.eu.drata.com/public/v2",
    "apac": "https://public-api.apac.drata.com/public/v2",
}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_tenant(config: dict) -> TenantData:
    """
    Fetch all risk register data for a single tenant.

    Errors are caught here so one bad token does not abort the entire run.
    The TenantData object will have its 'error' field set on failure.
    """
    name   = config["name"]
    token  = config["token"]
    region = config.get("region", "us")
    base_url = REGION_URLS.get(region, REGION_URLS["us"])

    logger.info("[%s] Fetching data from %s ...", name, base_url)
    client = DrataClient(token=token, base_url=base_url)
    registers, error = client.fetch_all_data()

    if error:
        logger.warning("[%s] Fetch failed: %s", name, error)
        return TenantData(name=name, error=error)

    total_risks = sum(len(reg.risks) for reg in registers)
    logger.info(
        "[%s] OK — %d register(s), %d risk(s) total",
        name, len(registers), total_risks,
    )
    return TenantData(name=name, registers=registers)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh the FirstService Risk Dashboard Excel file.",
    )
    parser.add_argument(
        "--tokens",
        default="tokens.json",
        help="Path to tokens file (default: tokens.json; use tokens.azure.json with --azure)",
    )
    parser.add_argument(
        "--azure",
        action="store_true",
        help="Load tokens from Azure Key Vault (requires AZURE_VAULT_URL env var)",
    )
    parser.add_argument(
        "--output",
        default="dashboard.xlsx",
        help="Output Excel file path (default: dashboard.xlsx)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate tokens and test connectivity without writing any file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed per-API-call logging",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Rich terminal UI with live progress (demo mode)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.azure and args.tokens == "tokens.json":
        args.tokens = "tokens.azure.json"

    tokens_path = Path(args.tokens)
    tenant_configs = load_tokens(tokens_path, azure=args.azure)

    if args.interactive:
        from interactive import run_interactive
        run_interactive(args, tenant_configs, fetch_tenant)
        return

    logger.info("Loaded %d tenant(s) from %s", len(tenant_configs), tokens_path)

    # Fetch data for every tenant (failures are isolated, not fatal)
    tenant_data = []
    for config in tenant_configs:
        tenant_data.append(fetch_tenant(config))

    # Summary
    success = [t for t in tenant_data if not t.error]
    failed  = [t for t in tenant_data if t.error]
    logger.info(
        "Fetch complete: %d succeeded, %d failed",
        len(success), len(failed),
    )

    if args.dry_run:
        logger.info("--dry-run: skipping Excel generation.")
        if failed:
            logger.warning("Failed tenants: %s", [t.name for t in failed])
        sys.exit(0 if not failed else 1)

    # Build and save the workbook
    output_path = args.output
    refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    logger.info("Building Excel workbook ...")
    builder = ExcelBuilder()
    builder.build(tenant_data, output_path=output_path, refreshed_at=refreshed_at)

    logger.info("Dashboard saved to: %s", output_path)

    if failed:
        logger.warning(
            "Some tenants failed to load — they appear as error rows in the dashboard:\n%s",
            "\n".join(f"  - {t.name}: {t.error}" for t in failed),
        )


if __name__ == "__main__":
    main()
