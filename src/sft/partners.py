"""Who else was there? Co-located loitering as an inferred ship-to-ship meeting.

GFW does not detect tanker-tanker encounters. But it records loitering for
every vessel. So for each long loitering event of one of OUR ships, we ask GFW
for loitering events by ANY vessel inside a small box around it, and keep the
ones that overlap in time and sit close by.

Two tankers loitering a few km apart for many overlapping hours is evidence
consistent with a meeting at sea. It is not proof of a cargo transfer; see the
evidence ladder in METHODOLOGY.md.

Output: data/processed/partners.parquet, one row per (our event, candidate partner).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .config import EVENT_DATASETS, PROCESSED, RAW
from .flags import norm_name, watch_sets
from .geo import haversine_km
from .gfw import GFWClient

PARTNER_COLUMNS = [
    "imo", "event_id", "start", "end", "lat", "lon", "duration_h",
    "partner_vessel_id", "partner_name", "partner_ssvid", "partner_flag",
    "partner_imo", "partner_type", "partner_event_id", "partner_start", "partner_end",
    "overlap_h", "overlap_frac", "dist_km", "partner_listed",
]


@dataclass(frozen=True)
class MatchRules:
    min_loiter_h: float = 6.0       # only search around our loitering events this long
    search_radius_km: float = 10.0  # size of the search box
    max_dist_km: float = 5.0        # keep partners whose loitering centre is this close
    min_overlap_h: float = 2.0      # ...and overlaps in time by at least this
    min_overlap_frac: float = 0.5   # ...covering at least half of the shorter event


def box(lat: float, lon: float, km: float) -> dict:
    """GeoJSON polygon (lon, lat order) of +/- km around a point."""
    dlat = km / 111.0
    dlon = km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    ring = [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat],
            [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]
    return {"type": "Polygon", "coordinates": [ring]}


def overlap_hours(a0, a1, b0, b1) -> float:
    return max((min(a1, b1) - max(a0, b0)).total_seconds() / 3600, 0.0)


def identity_summary(vessel_json: dict) -> dict:
    """Pull IMO / name / flag / ship type out of a GFW vessel record, defensively."""
    recs = (vessel_json.get("selfReportedInfo") or []) + (vessel_json.get("registryInfo") or [])
    imo = next((str(r["imo"]) for r in recs if r.get("imo")), None)
    name = next((r["shipname"] for r in recs if r.get("shipname")), None)
    flag = next((r["flag"] for r in recs if r.get("flag")), None)
    types = []
    for c in vessel_json.get("combinedSourcesInfo") or []:
        for t in c.get("shiptypes") or []:
            if t.get("name"):
                types.append(t["name"])
    return {"partner_imo": imo, "partner_name_registry": name, "partner_flag_registry": flag,
            "partner_type": ",".join(sorted(set(types))) or None}


def candidates_for_event(ev: pd.Series, found: list[dict], own_ids: set[str],
                         rules: MatchRules) -> list[dict]:
    rows = []
    for e in found:
        vid = (e.get("vessel") or {}).get("id")
        if not vid or vid in own_ids:
            continue  # our own ship
        try:
            p0, p1 = pd.Timestamp(e["start"]), pd.Timestamp(e["end"])
            plat, plon = e["position"]["lat"], e["position"]["lon"]
        except (KeyError, TypeError, ValueError):
            continue
        ov = overlap_hours(ev["start"], ev["end"], p0, p1)
        shorter = min(ev["duration_h"], (p1 - p0).total_seconds() / 3600) or 1e-9
        dist = float(haversine_km(ev["lat"], ev["lon"], plat, plon))
        if dist > rules.max_dist_km or ov < rules.min_overlap_h or ov / shorter < rules.min_overlap_frac:
            continue
        v = e.get("vessel") or {}
        rows.append({
            "imo": ev["imo"], "event_id": ev["event_id"], "start": ev["start"], "end": ev["end"],
            "lat": ev["lat"], "lon": ev["lon"], "duration_h": ev["duration_h"],
            "partner_vessel_id": vid, "partner_name": v.get("name"),
            "partner_ssvid": v.get("ssvid"), "partner_flag": v.get("flag"),
            "partner_event_id": e.get("id"), "partner_start": p0, "partner_end": p1,
            "overlap_h": round(ov, 1), "overlap_frac": round(ov / shorter, 2),
            "dist_km": round(dist, 2),
        })
    return rows


def run(events: pd.DataFrame, identities: pd.DataFrame, vessels: pd.DataFrame,
        client: GFWClient | None = None, rules: MatchRules = MatchRules(),
        raw_root: Path = RAW, out: Path = PROCESSED, lookup_partners: bool = True) -> pd.DataFrame:
    if client is None:
        from .config import get_token
        client = GFWClient(get_token())
    dataset = EVENT_DATASETS["loitering"]
    cache = raw_root / date.today().isoformat() / "partners"
    cache.mkdir(parents=True, exist_ok=True)
    # each ship's OWN GFW ids, so a meeting between two studied ships is still found
    ids_by_imo: dict[str, set[str]] = {}
    if not identities.empty:
        for imo, g in identities.dropna(subset=["vessel_id"]).groupby("imo"):
            ids_by_imo[imo] = set(g["vessel_id"])

    lo = events[(events["type"] == "loitering") & (events["duration_h"] >= rules.min_loiter_h)]
    lo = lo.dropna(subset=["lat", "lon", "start", "end"])
    print(f"Searching around {len(lo)} loitering events (>= {rules.min_loiter_h} h)…")
    rows: list[dict] = []
    for _, ev in lo.iterrows():
        cache_file = cache / f"{ev['event_id']}.json"
        if cache_file.exists():
            found = json.loads(cache_file.read_text())
        else:
            start = (ev["start"] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            end = (ev["end"] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            found = client.events_in_area(dataset, start, end, box(ev["lat"], ev["lon"],
                                                                   rules.search_radius_km))
            cache_file.write_text(json.dumps(found))
        hits = candidates_for_event(ev, found, ids_by_imo.get(ev["imo"], set()), rules)
        if hits:
            print(f"  {ev['start']:%Y-%m-%d} {ev['imo']}: {len(hits)} candidate(s)")
        rows += hits

    df = pd.DataFrame(rows).reindex(columns=PARTNER_COLUMNS)
    if not df.empty and lookup_partners:
        info = {}
        for vid in df["partner_vessel_id"].unique():
            try:
                info[vid] = identity_summary(client.get_vessel(vid))
            except Exception as exc:  # one failed lookup should not lose the rest
                print(f"  ! could not look up {vid}: {exc}")
                info[vid] = {}
        extra = pd.DataFrame.from_dict(info, orient="index")
        for col in ("partner_imo", "partner_type"):
            if col in extra:
                df[col] = df["partner_vessel_id"].map(extra[col])
        if "partner_name_registry" in extra:
            df["partner_name"] = df["partner_name"].fillna(
                df["partner_vessel_id"].map(extra["partner_name_registry"]))

    imos, names, _ = watch_sets(vessels, identities)
    df["partner_listed"] = (df["partner_imo"].isin(imos)
                            | df["partner_name"].map(norm_name).isin(names))
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "partners.parquet", index=False)
    print(f"{len(df)} candidate meetings -> {out / 'partners.parquet'}")
    return df
