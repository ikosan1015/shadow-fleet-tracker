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
