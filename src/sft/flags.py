"""Step 3: transparent, auditable anomaly rules.

Each rule returns rows of (imo, event_id, rule, detail). A flag is a LEAD TO CHECK,
not a finding. Thresholds live in `Rules` so they can be tuned and justified in
METHODOLOGY.md.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import pandas as pd

from .geo import RUSSIAN_TERMINALS, STS_HOTSPOTS, haversine_km, nearest


@dataclass(frozen=True)
class Rules:
    long_gap_h: float = 12.0
    hotspot_radius_km: float = 50.0
    terminal_radius_km: float = 30.0
    gap_after_terminal_h: float = 72.0
    loiter_min_h: float = 6.0
    loiter_min_port_dist_km: float = 20.0


FLAG_COLUMNS = ["imo", "event_id", "rule", "start", "lat", "lon", "detail"]


def norm_name(s) -> str:
    """'M/T  Eagle-S ' -> 'EAGLE S' so names from different sources compare."""
    s = re.sub(r"^(M/?T|MT)\s+", "", str(s or "").upper().strip())
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def _flag(df: pd.DataFrame, rule: str, detail) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    out = df[["imo", "event_id", "start", "lat", "lon"]].copy()
    out["rule"] = rule
    out["detail"] = detail(df) if callable(detail) else detail
    return out[FLAG_COLUMNS]


# --------------------------------------------------------------------- rules
def long_gaps(ev: pd.DataFrame, r: Rules) -> pd.DataFrame:
    g = ev[(ev["type"] == "gap") & (ev["duration_h"] >= r.long_gap_h)]
    return _flag(g, "long_gap", lambda d: d["duration_h"].round(1).astype(str) + " h dark")


def gaps_near_hotspots(ev: pd.DataFrame, r: Rules) -> pd.DataFrame:
    g = ev[ev["type"] == "gap"].copy()
    if g.empty:
        return _flag(g, "gap_near_hotspot", "")
    hits = []
    for idx, row in g.iterrows():
        for end, la, lo in (("off", row["gap_off_lat"], row["gap_off_lon"]),
                            ("on", row["gap_on_lat"], row["gap_on_lon"])):
            name, d = nearest(la, lo, STS_HOTSPOTS)
            if d <= r.hotspot_radius_km:
                hits.append((idx, f"AIS {end} {d:.0f} km from {name}"))
                break
    if not hits:
        return _flag(g.iloc[0:0], "gap_near_hotspot", "")
    sel = g.loc[[h[0] for h in hits]]
    return _flag(sel, "gap_near_hotspot", [h[1] for h in hits])


def gaps_after_russian_terminal(ev: pd.DataFrame, r: Rules) -> pd.DataFrame:
    pv = ev[ev["type"] == "port_visit"].copy()
    gaps = ev[ev["type"] == "gap"]
    out = []
    for _, p in pv.iterrows():
        name, d = nearest(p["lat"], p["lon"], RUSSIAN_TERMINALS)
        if d > r.terminal_radius_km or pd.isna(p["end"]):
            continue
        window = gaps[(gaps["imo"] == p["imo"])
                      & (gaps["start"] >= p["end"])
                      & (gaps["start"] <= p["end"] + pd.Timedelta(hours=r.gap_after_terminal_h))]
        if not window.empty:
            g = window.iloc[[0]]
            hrs = (g["start"].iloc[0] - p["end"]).total_seconds() / 3600
            out.append(_flag(g, "gap_after_russian_terminal",
                             f"went dark {hrs:.0f} h after leaving {name}"))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=FLAG_COLUMNS)


def encounters_with_listed(ev: pd.DataFrame, watch_names: set[str],
                           watch_ids: set[str]) -> pd.DataFrame:
    e = ev[ev["type"] == "encounter"].copy()
    if e.empty:
        return _flag(e, "encounter_with_listed", "")
    hit = e["partner_name"].map(norm_name).isin(watch_names) | e["partner_id"].isin(watch_ids)
    e = e[hit]
    return _flag(e, "encounter_with_listed",
                 lambda d: "met " + d["partner_name"].fillna("?") + " (" +
                 d["partner_flag"].fillna("?") + ")")


def open_sea_loitering(ev: pd.DataFrame, r: Rules) -> pd.DataFrame:
    lo = ev[(ev["type"] == "loitering") & (ev["duration_h"] >= r.loiter_min_h)].copy()
    ports = ev[ev["type"] == "port_visit"][["lat", "lon"]].dropna().drop_duplicates()
    if lo.empty:
        return _flag(lo, "open_sea_loitering", "")
    if ports.empty:
        lo["port_km"] = float("inf")
    else:
        lo["port_km"] = [
            float(haversine_km(la, ln, ports["lat"].values, ports["lon"].values).min())
            for la, ln in zip(lo["lat"], lo["lon"])
        ]
    lo = lo[lo["port_km"] >= r.loiter_min_port_dist_km]
    return _flag(lo, "open_sea_loitering",
                 lambda d: d["duration_h"].round(1).astype(str) + " h, "
                 + d["port_km"].round(0).astype(str) + " km from nearest seen port")


# ------------------------------------------------------------------ pipeline
def apply_all(ev: pd.DataFrame, vessels: pd.DataFrame,
              identities: pd.DataFrame | None = None, rules: Rules = Rules()) -> pd.DataFrame:
    sanctioned = vessels[vessels["group"].str.lower() == "sanctioned"]
    watch_names = {norm_name(n) for n in sanctioned["name"] if n}
    watch_ids: set[str] = set()
    if identities is not None and not identities.empty:
        sid = identities[identities["imo"].isin(sanctioned["imo"])]
        watch_names |= {norm_name(n) for n in sid["shipname"].dropna()}
        watch_ids |= set(sid["vessel_id"].dropna())
    parts = [
        long_gaps(ev, rules),
        gaps_near_hotspots(ev, rules),
        gaps_after_russian_terminal(ev, rules),
        encounters_with_listed(ev, watch_names, watch_ids),
        open_sea_loitering(ev, rules),
    ]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    return pd.concat(parts, ignore_index=True).sort_values(["imo", "start"]).reset_index(drop=True)


def summarize(flags: pd.DataFrame, ev: pd.DataFrame, vessels: pd.DataFrame) -> pd.DataFrame:
    """Flags per 30 days of observed activity, per vessel and rule.

    Normalising by time observed matters: a ship seen for 3 years will have more
    events than one seen for 3 months. Compare sanctioned vs control on rates.
    """
    span = ev.groupby("imo").agg(first=("start", "min"), last=("end", "max"))
    span["days_observed"] = ((span["last"] - span["first"]).dt.total_seconds() / 86400).clip(lower=1)
    counts = flags.pivot_table(index="imo", columns="rule", values="event_id",
                               aggfunc="count", fill_value=0)
    out = span[["days_observed"]].join(counts, how="left").fillna(0)
    rule_cols = [c for c in out.columns if c != "days_observed"]
    for c in rule_cols:
        out[f"{c}_per30d"] = (out[c] / out["days_observed"] * 30).round(2)
    meta = vessels.set_index("imo")[["name", "group"]]
    return meta.join(out, how="right").reset_index()


def rules_dict(r: Rules = Rules()) -> dict:
    return asdict(r)
