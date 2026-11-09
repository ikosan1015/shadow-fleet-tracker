"""Paths, dataset names and settings shared by every module."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
VESSELS_CSV = DATA / "vessels.csv"
OWNERSHIP_CSV = DATA / "ownership.csv"
WATCHLIST_CSV = DATA / "watchlist.csv"

# Danish Maritime Authority historical AIS (free; daily files incl. draught).
# Files are ~0.5-1 GB each. If the URL pattern changes, download by hand and put
# the .zip or .csv in data/raw/dma/ — local files are used first.
DMA_URLS = [
    "http://web.ais.dk/aisdata/aisdk-{day}.zip",
    "http://aisdata.ais.dk/aisdk-{day}.zip",
]
DMA_DIR = RAW / "dma"

# OpenSanctions (free for non-commercial use, CC BY-NC 4.0 — attribute in the report)
OS_DIR = RAW / "opensanctions"
OS_WATCHLIST = OS_DIR / "watchlist.csv"
OS_MARITIME_INDEX = "https://data.opensanctions.org/datasets/latest/maritime/index.json"
OS_MARITIME_FALLBACK = "https://data.opensanctions.org/datasets/latest/maritime/maritime.csv"
OS_API = "https://api.opensanctions.org"


def get_optional(name: str) -> str | None:
    """An optional secret from .env (e.g. OPENSANCTIONS_API_KEY); None if unset."""
    load_dotenv(ROOT / ".env")
    return os.getenv(name, "").strip() or None
SITE = ROOT / "dashboard" / "site"

API_BASE = "https://gateway.api.globalfishingwatch.org/v3"
IDENTITY_DATASET = "public-global-vessel-identity:latest"

# event type -> GFW dataset id
EVENT_DATASETS = {
    "port_visit": "public-global-port-visits-events:latest",
    "gap": "public-global-gaps-events:latest",
    "encounter": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
}

PRESENCE_DATASET = "public-global-presence:latest"   # hourly AIS presence, all vessel types

DEFAULT_START = "2023-01-01"
DEFAULT_END = "2026-10-31"


def get_token() -> str:
    """Read the GFW token from .env / environment. Never hard-code it."""
    load_dotenv(ROOT / ".env")
    token = os.getenv("GFW_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "GFW_TOKEN is not set. Copy .env.example to .env and paste your token."
        )
    return token
