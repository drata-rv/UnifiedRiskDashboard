"""
Current-user identity — read from Azure Easy Auth headers.

Azure App Service / Container Apps built-in authentication (Entra ID) injects
X-MS-CLIENT-PRINCIPAL-NAME on every authenticated request. No login code,
no session store, no password handling lives in this app — the platform
in front of it does all of that.

Local dev (no Easy Auth in front) falls back to a fixed dev identity. In
production (config.ENVIRONMENT == "production"), a missing header fails
closed (401) instead — the app has no way to verify a request actually came
through Easy Auth, so it must not assume it's safe to treat as anonymous/dev.
"""

import os

from fastapi import HTTPException, Request

from . import config

LOCAL_DEV_USER = os.environ.get("LOCAL_DEV_USER", "local-dev@firstservice.test")


def get_current_user(request: Request) -> str:
    principal = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
    if principal:
        return principal
    if config.ENVIRONMENT == "production":
        raise HTTPException(401, "Missing identity — this app must run behind Easy Auth in production")
    return LOCAL_DEV_USER
