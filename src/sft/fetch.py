"""Step 1: pull raw data from GFW and cache it, untouched, in data/raw/.

Layout:
  data/raw/<YYYY-MM-DD>/identity/<imo>.json
  data/raw/<YYYY-MM-DD>/events/<imo>_<event_type>.json
  data/raw/<YYYY-MM-DD>/manifest.json      (what was asked, when, how many rows)
  data/raw/LATEST                          (points at the newest fetch folder)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .config import DEFAULT_END, DEFAULT_START, EVENT_DATASETS, RAW, VESSELS_CSV, get_token
from .gfw import GFWClient, vessel_ids_for_imo


def load_vessels(path: Path = VESSELS_CSV) -> pd.DataFrame:
    df = pd.read_csv(path, comment="#", dtype=str).fillna("")
    df["imo"] = df["imo"].str.strip()
    df = df[df["imo"].str.fullmatch(r"\d{7}")]
    if df.empty:
        raise ValueError(f"No valid 7-digit IMOs in {path}. Add your vessels first.")
    return df


def _dump(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def run(start: str = DEFAULT_START, end: str = DEFAULT_END,
        client: GFWClient | None = None, raw_root: Path = RAW,
        vessels: pd.DataFrame | None = None) -> Path:
    client = client or GFWClient(get_token())
    vessels = vessels if vessels is not None else load_vessels()
    out = raw_root / date.today().isoformat()
    manifest = {"fetched_at": datetime.now(timezone.utc).isoformat(),
                "start": start, "end": end, "vessels": []}

    for v in vessels.itertuples():
        print(f"[{v.imo}] {v.name}: searching identity…")
        ident = client.search_vessel(v.imo)
        _dump(ident, out / "identity" / f"{v.imo}.json")
        ids = vessel_ids_for_imo(ident, v.imo)
        rec = {"imo": v.imo, "name": v.name, "gfw_ids": ids, "events": {}}
        if not ids:
            print(f"  ! no GFW vessel id matched IMO {v.imo} — check manually")
        for etype, dataset in EVENT_DATASETS.items():
            events = client.events(ids, dataset, start, end)
            _dump(events, out / "events" / f"{v.imo}_{etype}.json")
            rec["events"][etype] = len(events)
            print(f"  {etype:<11} {len(events):>5}")
        manifest["vessels"].append(rec)

    _dump(manifest, out / "manifest.json")
    (raw_root / "LATEST").write_text(out.name)
    print(f"Saved raw data to {out}")
    return out
