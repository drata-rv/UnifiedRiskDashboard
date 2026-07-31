"""
Drata API client for the FirstService Risk Dashboard.

Handles all HTTP communication with the Drata V2 public API, including
cursor-based pagination, expand parameters for sub-objects, and isolated
error handling so a single tenant failure does not abort the full run.

API reference: https://public-api.drata.com/public/v2
Auth: Bearer token in the Authorization header (one token per tenant).
"""

import logging
from typing import List, Optional, Tuple

import requests

from .models import Risk, RiskRegister, TenantData

logger = logging.getLogger(__name__)

# Default base URL — US region. EU/APAC tenants would need:
#   https://public-api.eu.drata.com/public/v2
#   https://public-api.apac.drata.com/public/v2
DEFAULT_BASE_URL = "https://public-api.drata.com/public/v2"

# Sub-objects to expand on each risk request. Owners and categories are
# needed for the risk table display. The rest add weight we don't need.
RISK_EXPAND = ["owners", "categories"]

# Page size for paginated requests. 100 is Drata's practical max.
PAGE_SIZE = 100


class DrataAPIError(Exception):
    """Raised when the Drata API returns a non-2xx response."""
    pass


class DrataClient:
    """
    Thin wrapper around the Drata V2 API for risk data.

    One client instance per tenant (each tenant has its own Bearer token).
    Uses a persistent requests.Session for connection reuse.
    """

    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make a GET request and return the parsed JSON body."""
        url = f"{self.base_url}{path}"
        try:
            response = self._session.get(url, params=params, timeout=30)
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise DrataAPIError(
                f"HTTP {exc.response.status_code} from {url}: {exc.response.text[:200]}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise DrataAPIError(f"Request failed for {url}: {exc}") from exc
        return response.json()

    def _paginate(self, path: str, base_params: Optional[dict] = None) -> List[dict]:
        """
        Fetch all pages from a cursor-paginated endpoint and return a flat
        list of all 'data' items.

        Drata's pagination uses a 'cursor' query param. The first request
        uses cursor=None; subsequent requests pass the cursor value from
        the previous response's pagination.cursor field.
        """
        params = dict(base_params or {})
        params["size"] = PAGE_SIZE
        params["includeTotalCount"] = "true"

        all_items: List[dict] = []
        cursor: Optional[str] = None

        while True:
            if cursor:
                params["cursor"] = cursor
            elif "cursor" in params:
                # Don't send an empty cursor on the first request
                del params["cursor"]

            data = self._get(path, params)
            batch = data.get("data", [])
            all_items.extend(batch)

            pagination = data.get("pagination", {})
            cursor = pagination.get("cursor")

            total_count = pagination.get("totalCount")
            logger.debug(
                "Fetched %d items from %s (total: %s, cursor: %s)",
                len(batch), path, total_count, cursor
            )

            # Stop when no more pages (cursor is null/absent or batch was empty)
            if not cursor or not batch:
                break

        return all_items

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def get_risk_registers(self) -> List[RiskRegister]:
        """
        Return all Risk Registers for this tenant.
        Most tenants will have one register, but the API supports multiple.
        """
        items = self._paginate("/risk-registers")
        return [
            RiskRegister(
                id=item["id"],
                name=item["name"],
                description=item.get("description"),
            )
            for item in items
        ]

    def get_risks(self, register_id: int) -> List[Risk]:
        """
        Return all Risks for a given Risk Register, with owners and categories
        expanded. Handles pagination automatically.
        """
        params = {
            # expand[] must be sent as repeated params; requests handles list values
            "expand[]": RISK_EXPAND,
        }
        items = self._paginate(f"/risk-registers/{register_id}/risks", params)
        return [self._parse_risk(item) for item in items]

    def get_users(self) -> List[dict]:
        """
        Return every user in this tenant — the pool of valid risk owners.
        Confirmed live: /users is paginated the same way as everything else
        and returns {id, email, firstName, lastName, ...} per record.
        """
        items = self._paginate("/users")
        return [
            {
                "id": item["id"],
                "email": item.get("email") or "",
                "name": f"{item.get('firstName') or ''} {item.get('lastName') or ''}".strip()
                        or item.get("email") or "",
            }
            for item in items
        ]

    def fetch_all_data(self) -> Tuple[List[RiskRegister], Optional[str]]:
        """
        Convenience method that fetches all registers and all their risks in
        one call. Returns (registers_with_risks, error_message).

        Designed to be called in a try/except at the tenant level so a
        single broken token does not abort the full dashboard run.
        """
        try:
            registers = self.get_risk_registers()
            for register in registers:
                register.risks = self.get_risks(register.id)
            return registers, None
        except DrataAPIError as exc:
            logger.error("API error: %s", exc)
            return [], str(exc)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_risk(item: dict) -> Risk:
        """
        Parse a raw Drata risk JSON object into a Risk dataclass.

        Owners come back as {id, email, firstName, lastName} when expanded —
        the numeric id is kept (not just a display string) because the PUT
        endpoint requires owners as [{"id": <int>}] to reassign them.
        Categories come back as objects with a 'name' field; kept as plain
        display strings since there's no confirmed way to look up valid
        category ids to build a picker (categories are read-only here).
        """
        owners = [
            {
                "id": o["id"],
                "name": f"{o.get('firstName') or ''} {o.get('lastName') or ''}".strip()
                        or o.get("email") or str(o["id"]),
            }
            for o in item.get("owners", [])
            if isinstance(o, dict) and o.get("id") is not None
        ]
        categories = [
            c.get("name", "")
            for c in item.get("categories", [])
            if isinstance(c, dict)
        ]

        return Risk(
            id=item["id"],
            risk_id=item.get("riskId") or "",
            title=item.get("title") or "",
            description=item.get("description") or "",
            impact=item.get("impact"),
            likelihood=item.get("likelihood"),
            score=item.get("score"),
            residual_impact=item.get("residualImpact"),
            residual_likelihood=item.get("residualLikelihood"),
            residual_score=item.get("residualScore"),
            treatment_plan=item.get("treatmentPlan"),
            treatment_details=item.get("treatmentDetails"),
            status=item.get("status"),
            categories=[c for c in categories if c],
            owners=owners,
        )
