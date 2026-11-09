"""Follow the partners: where did the other ship come from, and where did it go next?

For every meeting worth checking (a partner that was alongside, or is on the
watchlist), we pull that partner's GFW port visits from 30 days before to 60 days
after, and record its last port before the meeting and its next ports after.

This is the "cargo logic" rung of the evidence ladder: e.g. a partner that came from
a Russian terminal and next called at a refinery port is consistent with having
delivered or collected cargo; a partner that sailed straight back to Russia is not
consistent with having taken cargo away.

Output: data/processed/partner_trips.csv
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from .clean import events_frame, parse_event
from .config import EVENT_DATASETS, PROCESSED, RAW
from .gfw import GFWClient

TRIP_COLUMNS = [
    "imo", "event_id", "meeting_start", "meeting_end", "lat", "lon",
    "partner_vessel_id", "partner_name", "partner_flag", "partner_imo",
    "partner_listed", "alongside", "crowded", "dist_km", "overlap_h",
    "prev_port", "prev_port_flag", "prev_port_end", "days_since_prev",
    "next_port", "next_port_flag", "next_port_start", "days_to_next",
    "next_ports", "from_russia", "back_to_russia",
]


def select_meetings(partners: pd.DataFrame) -> pd.DataFrame:
    """Meetings worth following: partner alongside, or on the watchlist."""
    if partners is None or partners.empty:
        return pd.DataFrame(columns=partners.columns if partners is not None else [])
    keep = partners["alongside"].fillna(False).astype(bool) | \
        partners["partner_listed"].fillna(False).astype(bool)
    return partners[keep].drop_duplicates(["event_id", "partner_vessel_id"])


def _fmt(row) -> str:
    name = row["port_name"] if pd.notna(row["port_name"]) else "unnamed anchorage"
    flag = row["port_flag"] if pd.notna(row["port_flag"]) else "?"
    return f"{name} ({flag}) {row['start']:%Y-%m-%d}"


def trips_for(meeting: pd.Series, ports: pd.DataFrame, n_next: int = 3) -> dict:
    ms, me = meeting["start"], meeting["end"] if pd.notna(meeting["end"]) else meeting["start"]
    before = ports[ports["end"] <= ms].sort_values("end")
    after = ports[ports["start"] >= me].sort_values("start")
    prev = before.iloc[-1] if not before.empty else None
    nxt = after.head(n_next)
    first = nxt.iloc[0] if not nxt.empty else None
    return {
        "prev_port": None if prev is None else prev["port_name"],
        "prev_port_flag": None if prev is None else prev["port_flag"],
        "prev_port_end": None if prev is None else prev["end"],
        "days_since_prev": None if prev is None else round((ms - prev["end"]).total_seconds() / 86400, 1),
        "next_port": None if first is None else first["port_name"],
        "next_port_flag": None if first is None else first["port_flag"],
        "next_port_start": None if first is None else first["start"],
        "days_to_next": None if first is None else round((first["start"] - me).total_seconds() / 86400, 1),
        "next_ports": "; ".join(_fmt(r) for _, r in nxt.iterrows()) or None,
        "from_russia": prev is not None and prev["port_flag"] == "RUS",
        "back_to_russia": first is not None and first["port_flag"] == "RUS",
    }


def run(partners: pd.DataFrame, client: GFWClient | None = None, days_before: int = 30,
        days_after: int = 60, raw_root: Path = RAW, out: Path = PROCESSED) -> pd.DataFrame:
    meetings = select_meetings(partners)
    out.mkdir(parents=True, exist_ok=True)
    if meetings.empty:
        print("No alongside or watchlisted partners to follow.")
        df = pd.DataFrame(columns=TRIP_COLUMNS)
        df.to_csv(out / "partner_trips.csv", index=False)
        return df
    if client is None:
        from .config import get_token
        client = GFWClient(get_token())
    cache = raw_root / date.today().isoformat() / "follow"
    cache.mkdir(parents=True, exist_ok=True)

    # one request per partner, covering all its meetings
    ports_by_vid: dict[str, pd.DataFrame] = {}
    for vid, g in meetings.dropna(subset=["start"]).groupby("partner_vessel_id"):
        start = (g["start"].min() - pd.Timedelta(days=days_before)).strftime("%Y-%m-%d")
        end = (g["start"].max() + pd.Timedelta(days=days_after)).strftime("%Y-%m-%d")
        f = cache / f"{vid}_{start}_{end}.json"
        if f.exists():
            raw = json.loads(f.read_text())
        else:
            name = g["partner_name"].iloc[0]
            print(f"  following {name} ({start} -> {end}) …")
            raw = client.events([vid], EVENT_DATASETS["port_visit"], start, end)
            f.write_text(json.dumps(raw))
        ports_by_vid[vid] = events_frame([parse_event(e, vid) for e in raw])

    rows = []
    for _, m in meetings.iterrows():
        ports = ports_by_vid.get(m["partner_vessel_id"], pd.DataFrame())
        base = {
            "imo": m["imo"], "event_id": m["event_id"], "meeting_start": m["start"],
            "meeting_end": m["end"], "lat": m["lat"], "lon": m["lon"],
            "partner_vessel_id": m["partner_vessel_id"], "partner_name": m["partner_name"],
            "partner_flag": m["partner_flag"], "partner_imo": m.get("partner_imo"),
            "partner_listed": m.get("partner_listed"), "alongside": m.get("alongside"),
            "crowded": m.get("crowded"), "dist_km": m["dist_km"], "overlap_h": m["overlap_h"],
        }
        rows.append({**base, **(trips_for(m, ports) if not ports.empty else {})})
    df = pd.DataFrame(rows).reindex(columns=TRIP_COLUMNS).sort_values("meeting_start")
    df.to_csv(out / "partner_trips.csv", index=False)
    show = ["meeting_start", "partner_name", "prev_port", "prev_port_flag", "next_ports"]
    print(df[show].to_string(index=False))
    return df
