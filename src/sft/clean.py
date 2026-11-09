"""Step 2: raw JSON -> tidy tables in data/processed/.

identities.parquet  one row per GFW self-reported identity period
                    (name / flag / MMSI as broadcast, with date range)
events.parquet      one row per event, common columns + per-type details
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import PROCESSED, RAW

EVENT_COLUMNS = [
    "imo", "event_id", "type", "start", "end", "duration_h", "lat", "lon",
    "vessel_id", "vessel_name", "vessel_flag",
    # gap
    "gap_off_lat", "gap_off_lon", "gap_on_lat", "gap_on_lon",
    "gap_distance_km", "gap_intentional",
    # encounter
    "partner_id", "partner_name", "partner_flag", "partner_ssvid",
    "encounter_median_dist_km",
    # loitering
    "loiter_distance_km", "loiter_avg_speed_kn",
    # port visit
    "port_name", "port_flag", "port_anchorage_id", "port_confidence",
    "port_start_name", "port_start_flag", "port_end_name", "port_end_flag",
    # distances GFW reports at the start of the event
    "dist_shore_km", "dist_port_km",
]


def latest_raw(raw_root: Path = RAW) -> Path:
    marker = raw_root / "LATEST"
    if not marker.exists():
        raise FileNotFoundError("No raw data yet. Run `sft fetch` first.")
    return raw_root / marker.read_text().strip()


def _g(d: dict | None, *path, default=None):
    """Safe nested get: _g(e, 'gap', 'offPosition', 'lat')."""
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return default if cur is None else cur


# ---------------------------------------------------------------- identities
def parse_identity(search_json: dict, imo: str) -> pd.DataFrame:
    rows = []
    for entry in search_json.get("entries", []):
        for r in entry.get("selfReportedInfo", []) or []:
            reported = str(r.get("imo") or "").strip()
            if reported and reported != imo:
                continue  # a different ship that matched the free-text search
            rows.append({
                "imo": imo,
                "vessel_id": r.get("id"),
                "ssvid": r.get("ssvid"),
                "shipname": r.get("shipname"),
                "flag": r.get("flag"),
                "callsign": r.get("callsign"),
                "reported_imo": r.get("imo"),
                "from": r.get("transmissionDateFrom"),
                "to": r.get("transmissionDateTo"),
                "source": "AIS self-reported",
            })
        for r in entry.get("registryInfo", []) or []:
            rows.append({
                "imo": imo,
                "vessel_id": None,
                "ssvid": r.get("ssvid"),
                "shipname": r.get("shipname"),
                "flag": r.get("flag"),
                "callsign": r.get("callsign"),
                "reported_imo": r.get("imo"),
                "from": r.get("transmissionDateFrom"),
                "to": r.get("transmissionDateTo"),
                "source": "registry:" + ",".join(r.get("sourceCode", []) or []),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("from", "to"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce", format="ISO8601")
    return df.drop_duplicates().sort_values(["imo", "from"]).reset_index(drop=True)


# -------------------------------------------------------------------- events
def parse_event(e: dict, imo: str) -> dict:
    etype = (e.get("type") or "").lower()
    row = {
        "imo": imo,
        "event_id": e.get("id"),
        "type": etype,
        "start": e.get("start"),
        "end": e.get("end"),
        "lat": _g(e, "position", "lat"),
        "lon": _g(e, "position", "lon"),
        "vessel_id": _g(e, "vessel", "id"),
        "vessel_name": _g(e, "vessel", "name"),
        "vessel_flag": _g(e, "vessel", "flag"),
        "dist_shore_km": _g(e, "distances", "startDistanceFromShoreKm"),
        "dist_port_km": _g(e, "distances", "startDistanceFromPortKm"),
    }
    if etype == "gap":
        gap = e.get("gap", {}) or {}
        row.update({
            "gap_off_lat": _g(gap, "offPosition", "lat"),
            "gap_off_lon": _g(gap, "offPosition", "lon"),
            "gap_on_lat": _g(gap, "onPosition", "lat"),
            "gap_on_lon": _g(gap, "onPosition", "lon"),
            "gap_distance_km": gap.get("distanceKm"),
            "gap_intentional": gap.get("intentionalDisabling"),
        })
    elif etype == "encounter":
        enc = e.get("encounter", {}) or {}
        row.update({
            "partner_id": _g(enc, "vessel", "id"),
            "partner_name": _g(enc, "vessel", "name"),
            "partner_flag": _g(enc, "vessel", "flag"),
            "partner_ssvid": _g(enc, "vessel", "ssvid"),
            "encounter_median_dist_km": enc.get("medianDistanceKilometers"),
        })
    elif etype == "loitering":
        loi = e.get("loitering", {}) or {}
        row.update({
            "loiter_distance_km": loi.get("totalDistanceKm"),
            "loiter_avg_speed_kn": loi.get("averageSpeedKnots"),
        })
    elif etype == "port_visit":
        pv = e.get("port_visit", {}) or {}
        anch = pv.get("intermediateAnchorage") or pv.get("startAnchorage") or {}
        row.update({
            "port_name": anch.get("name"),
            "port_flag": anch.get("flag"),
            "port_anchorage_id": anch.get("anchorageId") or anch.get("id"),
            "port_confidence": pv.get("confidence"),
            # a long "visit" whose start and end anchorages differ is suspicious:
            # it may be two stays merged into one event
            "port_start_name": _g(pv, "startAnchorage", "name"),
            "port_start_flag": _g(pv, "startAnchorage", "flag"),
            "port_end_name": _g(pv, "endAnchorage", "name"),
            "port_end_flag": _g(pv, "endAnchorage", "flag"),
        })
    return row


def events_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).reindex(columns=EVENT_COLUMNS)
    for c in ("start", "end"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce", format="ISO8601")
    df["duration_h"] = (df["end"] - df["start"]).dt.total_seconds() / 3600
    num = [c for c in EVENT_COLUMNS if c.endswith(("lat", "lon", "_km", "_kn"))]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    return df.drop_duplicates("event_id").sort_values(["imo", "start"]).reset_index(drop=True)


def run(raw_dir: Path | None = None, out: Path = PROCESSED) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw_dir = raw_dir or latest_raw()
    out.mkdir(parents=True, exist_ok=True)

    idents = []
    for f in sorted((raw_dir / "identity").glob("*.json")):
        idents.append(parse_identity(json.loads(f.read_text()), f.stem))
    identities = pd.concat(idents, ignore_index=True) if idents else pd.DataFrame()

    rows = []
    for f in sorted((raw_dir / "events").glob("*.json")):
        imo = f.stem.split("_", 1)[0]
        rows += [parse_event(e, imo) for e in json.loads(f.read_text())]
    events = events_frame(rows)

    identities.to_parquet(out / "identities.parquet", index=False)
    events.to_parquet(out / "events.parquet", index=False)
    print(f"identities: {len(identities)} rows · events: {len(events)} rows -> {out}")
    print(events.groupby(["imo", "type"]).size().unstack(fill_value=0).to_string()
          if not events.empty else "(no events)")
    return identities, events
