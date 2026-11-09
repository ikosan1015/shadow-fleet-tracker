"""Thin, transparent client for the Global Fishing Watch API v3.

Only the endpoints this project needs:
  - GET /vessels/search   (IMO -> GFW vessel ids + identity history)
  - GET /events           (paginated events per dataset)
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from urllib.parse import urlencode

import requests

from .config import API_BASE, IDENTITY_DATASET

USER_AGENT = "shadow-fleet-tracker (non-commercial research)"


def _indexed(name: str, values: Iterable[str]) -> list[tuple[str, str]]:
    """GFW expects array params as name[0]=a&name[1]=b."""
    return [(f"{name}[{i}]", v) for i, v in enumerate(values)]


class GFWClient:
    def __init__(self, token: str, base: str = API_BASE, pause: float = 0.5,
                 max_retries: int = 5, session: requests.Session | None = None):
        self.base = base.rstrip("/")
        self.pause = pause
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        })

    # -- low level -----------------------------------------------------------
    def _get(self, path: str, params: list[tuple[str, str]]) -> dict:
        # keep [] and : readable, as in GFW's own examples
        url = f"{self.base}{path}?{urlencode(params, safe='[]:')}"
        for attempt in range(self.max_retries):
            r = self.session.get(url, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise requests.HTTPError(
                    f"{r.status_code} for {path}: {r.text[:300]}", response=r
                )
            time.sleep(self.pause)
            return r.json()
        r.raise_for_status()
        raise RuntimeError(f"Gave up after {self.max_retries} retries: {path}")

    # -- vessels -------------------------------------------------------------
    def search_vessel(self, query: str) -> dict:
        """Free-text search (IMO, MMSI, name...). Returns the raw JSON."""
        params = [("query", query)]
        params += _indexed("datasets", [IDENTITY_DATASET])
        params += _indexed("includes", ["MATCH_CRITERIA", "OWNERSHIP"])
        return self._get("/vessels/search", params)

    # -- events --------------------------------------------------------------
    def events(self, vessel_ids: list[str], dataset: str, start: str, end: str,
               limit: int = 500) -> list[dict]:
        """All events for these vessel ids in one dataset, following pagination."""
        if not vessel_ids:
            return []
        out: list[dict] = []
        offset = 0
        while True:
            params = _indexed("vessels", vessel_ids)
            params += _indexed("datasets", [dataset])
            params += [("start-date", start), ("end-date", end),
                       ("limit", str(limit)), ("offset", str(offset))]
            page = self._get("/events", params)
            entries = page.get("entries", [])
            out.extend(entries)
            nxt = page.get("nextOffset")
            if not entries or nxt is None or nxt <= offset:
                return out
            offset = nxt


def vessel_ids_for_imo(search_json: dict, imo: str) -> list[str]:
    """Collect every GFW vessel id whose identity record carries this IMO.

    One physical ship can have several ids (e.g. after an MMSI change).
    That is itself worth noting in the write-up.
    """
    imo = str(imo).strip()
    ids: set[str] = set()
    for entry in search_json.get("entries", []):
        records = entry.get("selfReportedInfo", []) or []
        registry = entry.get("registryInfo", []) or []
        entry_imos = {str(r.get("imo") or "").strip() for r in records + registry}
        if imo in entry_imos:
            ids.update(r["id"] for r in records if r.get("id"))
    return sorted(ids)
