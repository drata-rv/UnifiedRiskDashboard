"""Environment-driven settings. No config files, no secrets in code."""

import os
from pathlib import Path

# "production" makes user_auth fail closed (401) instead of falling back to a
# fixed dev identity when Easy Auth's identity header is missing. Defaults to
# "production" in the shipped container (see Dockerfile); local dev leaves this
# unset.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

TOKENS_MODE = os.environ.get("TOKENS_MODE", "local")  # "local" | "azure"
TOKENS_PATH = Path(os.environ.get("TOKENS_PATH", "tokens.json" if TOKENS_MODE == "local" else "tokens.azure.json"))

DB_PATH = Path(os.environ.get("DB_PATH", "data/app.db"))

LOCK_TTL_SECONDS = int(os.environ.get("LOCK_TTL_SECONDS", "600"))

REGION_URLS = {
    "us":   "https://public-api.drata.com/public/v2",
    "eu":   "https://public-api.eu.drata.com/public/v2",
    "apac": "https://public-api.apac.drata.com/public/v2",
}
