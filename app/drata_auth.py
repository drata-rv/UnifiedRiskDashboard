"""
auth.py — Token loading for both local (tokens.json) and Azure Key Vault modes.

Used by refresh.py and push_changes.py. Returns the same list shape in both modes:
    [{"name": "...", "token": "...", "region": "us"}, ...]
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def load_tokens(tokens_path: Path, azure: bool = False) -> List[dict]:
    """
    Load tenant configurations.

    Local mode (azure=False):
        Reads tokens_path as tokens.json — each entry includes the token string directly.

    Azure mode (azure=True):
        Reads tokens_path as a manifest (tokens.azure.json) with tenant names and Key Vault
        secret names but no token strings. Fetches each token from the vault URL stored in
        AZURE_VAULT_URL using DefaultAzureCredential (supports az login, Managed Identity,
        and AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID env vars).
    """
    if azure:
        return _load_from_vault(tokens_path)
    return _load_local(tokens_path)


def _load_local(tokens_path: Path) -> List[dict]:
    if not tokens_path.exists():
        logger.error(
            "Tokens file not found at %s\n"
            "Copy tokens.example.json to tokens.json and fill in the API tokens.",
            tokens_path,
        )
        sys.exit(1)

    with open(tokens_path) as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError as exc:
            logger.error("%s is not valid JSON: %s", tokens_path, exc)
            sys.exit(1)

    tenants = config.get("tenants", [])
    if not tenants:
        logger.error("%s has no 'tenants' entries.", tokens_path)
        sys.exit(1)

    for i, t in enumerate(tenants):
        if not t.get("name"):
            logger.error("tenants[%d] is missing a 'name' field.", i)
            sys.exit(1)
        if not t.get("token"):
            logger.error("tenants[%d] (%s) is missing a 'token' field.", i, t.get("name"))
            sys.exit(1)

    return tenants


def _load_from_vault(manifest_path: Path) -> List[dict]:
    vault_url = os.environ.get("AZURE_VAULT_URL")
    if not vault_url:
        logger.error(
            "AZURE_VAULT_URL environment variable is not set.\n"
            "Set it before running in Azure mode, e.g.:\n"
            "  export AZURE_VAULT_URL=https://your-vault.vault.azure.net/"
        )
        sys.exit(1)

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        from azure.core.exceptions import ResourceNotFoundError
    except ImportError:
        logger.error(
            "Azure SDK not installed. Run:\n"
            "  pip install azure-keyvault-secrets azure-identity"
        )
        sys.exit(1)

    if not manifest_path.exists():
        logger.error(
            "Azure manifest not found at %s\n"
            "Copy tokens.azure.example.json to tokens.azure.json and fill in tenant metadata.",
            manifest_path,
        )
        sys.exit(1)

    with open(manifest_path) as f:
        try:
            manifest = json.load(f)
        except json.JSONDecodeError as exc:
            logger.error("%s is not valid JSON: %s", manifest_path, exc)
            sys.exit(1)

    tenants_meta = manifest.get("tenants", [])
    if not tenants_meta:
        logger.error("%s has no 'tenants' entries.", manifest_path)
        sys.exit(1)

    credential = DefaultAzureCredential()
    secret_client = SecretClient(vault_url=vault_url, credential=credential)
    logger.info("Connected to Azure Key Vault: %s", vault_url)

    tenants = []
    for i, t in enumerate(tenants_meta):
        name = t.get("name")
        secret_name = t.get("secret_name")
        region = t.get("region", "us")

        if not name:
            logger.error("tenants[%d] is missing a 'name' field — skipping.", i)
            continue
        if not secret_name:
            logger.error(
                "tenants[%d] (%s) is missing a 'secret_name' field — skipping.", i, name
            )
            continue

        try:
            secret = secret_client.get_secret(secret_name)
            token = secret.value
        except ResourceNotFoundError:
            logger.error(
                "[%s] Secret %r not found in vault %s — skipping tenant.",
                name, secret_name, vault_url,
            )
            continue
        except Exception as exc:
            logger.error(
                "[%s] Failed to fetch secret %r: %s — skipping tenant.",
                name, secret_name, exc,
            )
            continue

        tenants.append({"name": name, "token": token, "region": region})
        logger.info("[%s] Token loaded from vault (secret: %s)", name, secret_name)

    if not tenants:
        logger.error("No tokens could be loaded from Azure Key Vault — aborting.")
        sys.exit(1)

    return tenants
