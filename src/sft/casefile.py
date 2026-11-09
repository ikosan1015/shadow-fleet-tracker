"""One case file per vessel: everything the pipeline knows, in one readable page.

report/cases/<imo>-<name>.md   (also rendered into the dashboard)

It is a working document for the write-up, not the write-up itself: it lists the
evidence, grades nothing on its own, and marks what still has to be checked by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .config import ROOT
from .flags import RULE_INFO

CASES = ROOT / "report" / "cases"


@dataclass
class CaseData:
    vessel: pd.Series                       # row of vessels.csv
    events: pd.DataFrame
    identities: pd.DataFrame
    flags: pd.DataFrame
    partners: pd.DataFrame | None = None    # annotated (listed / alongside / crowded)
    trips: pd.DataFrame | None = None       # partner_trips.csv
    sanctions: pd.DataFrame | None = None   # sanctions_check.csv
    sanctions_file: str | None = None


# ------------------------------------------------------------------ helpers
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "vessel"


def md_table(df: pd.DataFrame, max_rows: int | None = None) -> str:
    if df is None or df.empty:
        return "_none_\n"
    d = df.head(max_rows) if max_rows else df

    def cell(v):
        if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
            return ""
        if isinstance(v, pd.Timestamp):
            return f"{v:%Y-%m-%d %H:%M}" if (v.hour or v.minute) else f"{v:%Y-%m-%d}"
        if isinstance(v, bool):
            return "yes" if v else ""
        if isinstance(v, float):
            return f"{v:,.1f}"
        return str(v).replace("|", "/").replace("\n", " ")

    lines = ["| " + " | ".join(map(str, d.columns)) + " |",
             "|" + "|".join("---" for _ in d.columns) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in d.itertuples(index=False)]
    more = f"\n_… {len(df) - len(d)} more rows in the data files._" if len(d) < len(df) else ""
    return "\n".join(lines) + "\n" + more + "\n"


def copernicus_link(lat: float, lon: float, day: pd.Timestamp) -> str:
    start = (day - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    end = (day + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    return (f"https://browser.dataspace.copernicus.eu/?zoom=12&lat={lat:.4f}&lng={lon:.4f}"
            f"&fromTime={start}T00:00:00.000Z&toTime={end}T23:59:59.999Z")


def _txt(v, empty: str = "") -> str:
    """str() that turns None / NaN into `empty` instead of 'nan'."""
    return empty if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)


def d(ts, fmt="%Y-%m-%d") -> str:
    """Format a date, or '?' if missing."""
    return ts.strftime(fmt) if isinstance(ts, pd.Timestamp) and pd.notna(ts) else "?"


def _strength(rule: str) -> int:
    return RULE_INFO.get(rule, ("other", 0, rule))[1]


def _meaning(rule: str) -> str:
    return RULE_INFO.get(rule, ("other", 0, rule))[2]


def _sanction_row(sc: pd.DataFrame | None, imo: str, name: str) -> pd.Series | None:
    if sc is None or sc.empty:
        return None
    hit = sc[(sc["imo"].astype(str) == str(imo)) & (str(imo) != "")]
    if hit.empty:
        hit = sc[sc["name"] == name]
    return hit.iloc[0] if not hit.empty else None


# ------------------------------------------------------------------ sections
def section_summary(c: CaseData) -> str:
    v, ev, fl = c.vessel, c.events, c.flags
    out = ["## Summary\n"]
    s = _sanction_row(c.sanctions, v["imo"], v["name"])
    if s is not None and str(s.get("os_risk", "")) not in ("", "nan"):
        out.append(f"- **OpenSanctions:** {s['os_risk']} — datasets: {_txt(s.get('os_datasets'))}")
    out.append(f"- **Study group:** {v.get('group', '')}"
               + (f"; EU listed: {v.get('eu_listed')}, UK listed: {v.get('uk_listed')}"
                  if v.get("eu_listed") else ""))
    if not ev.empty:
        counts = ev["type"].value_counts().to_dict()
        out.append(f"- **Observed:** {d(ev['start'].min())} → {d(ev['end'].max())}; "
                   + ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in counts.items()))
        if "gap" not in counts:
            out.append("- **No GFW AIS-off events.** GFW only records gaps ≥ 12 h starting ≥ 50 nm "
                       "offshore, so near-shore dark periods are invisible here (see METHODOLOGY).")
    if not fl.empty:
        cats = fl["rule"].map(lambda r: RULE_INFO.get(r, ("other",))[0]).value_counts()
        out.append("- **Flags:** " + ", ".join(f"{n} {cat}" for cat, n in cats.items()))
        top = fl.assign(strength=fl["rule"].map(_strength)).sort_values(
            ["strength", "start"], ascending=[False, True])
        cat = top["rule"].map(lambda r: RULE_INFO.get(r, ("other",))[0])
        top = top[(top["strength"] >= 2) & (cat != "data quality")].head(6)
        if not top.empty:
            out.append("\n**Strongest leads** (to verify, not findings):\n")
            for _, r in top.iterrows():
                out.append(f"1. **{d(r['start'])}** — {_meaning(r['rule'])} — {r['detail']}")
    return "\n".join(out) + "\n"


def section_identity(c: CaseData) -> str:
    from .ownership import identity_changes
    ids = c.identities
    if ids is None or ids.empty:
        return "## Identity history\n\n_no identity records_\n"
    ids = ids.reindex(columns=sorted(set(ids.columns) | {"source", "shipname", "flag", "ssvid",
                                                          "from", "to"}))
    t = ids[["source", "shipname", "flag", "ssvid", "from", "to"]].rename(
        columns={"ssvid": "MMSI", "shipname": "name"})
    ch = identity_changes(ids) if ids["from"].notna().any() else pd.DataFrame()
    lines = ["## Identity history\n", md_table(t)]
    if not ch.empty:
        lines.append("**Changes in broadcast identity:**\n")
        for _, r in ch.iterrows():
            lines.append(f"- {d(r['date'])}: {r['field']} {r['old']} → {r['new']}")
    return "\n".join(lines) + "\n"


def section_ports(c: CaseData) -> str:
    pv = c.events[c.events["type"] == "port_visit"].copy()
    if pv.empty:
        return "## Port calls\n\n_none recorded_\n"
    t = pd.DataFrame({
        "arrived": pv["start"], "left": pv["end"], "days": (pv["duration_h"] / 24).round(1),
        "port": pv["port_name"].fillna("(unnamed anchorage)"), "country": pv["port_flag"]})
    by = pv["port_flag"].fillna("?").value_counts()
    return ("## Port calls\n\n" + md_table(t) + "\n**Calls by country:** "
            + ", ".join(f"{k} {v}" for k, v in by.items()) + "\n")


def section_meetings(c: CaseData) -> str:
    p = c.partners
    if p is None or p.empty:
        return "## Meetings at sea\n\n_No co-located loitering found (or `sft partners` not run)._\n"
    sc = c.sanctions
    now_known = {}
    if sc is not None and not sc.empty:
        for _, r in sc[sc["role"] == "partner"].iterrows():
            now_known[(str(r["name"]), str(r["imo"]))] = r.get("os_caption")
    t = pd.DataFrame({
        "date": p["start"], "lat": p["lat"].map("{:.3f}".format), "lon": p["lon"].map("{:.3f}".format),
        "partner (broadcast)": p["partner_name"], "flag": p["partner_flag"], "IMO": p["partner_imo"],
        "OpenSanctions name": [_txt(now_known.get((str(n), str(i) if pd.notna(i) else ""))).split(";")[0]
                               for n, i in zip(p["partner_name"], p["partner_imo"])],
        "sanctioned today": p["partner_listed"].fillna(False).astype(bool),
        "km": p["dist_km"].map("{:.2f}".format), "overlap h": p["overlap_h"],
        "alongside": p["alongside"].fillna(False).astype(bool),
        "ships around": p["crowd_n"],
    }).sort_values(["date", "km"])
    out = ["## Meetings at sea (co-located loitering)\n",
           "Other vessels loitering within 5 km during overlapping hours. "
           "'Ships around' > 4 means a crowded anchorage or queue, where proximity means little.\n",
           md_table(t)]
    if c.trips is not None and not c.trips.empty:
        tt = c.trips[c.trips["imo"].astype(str) == str(c.vessel["imo"])]
        if not tt.empty:
            out.append("\n**Where the partners came from and went next** (GFW port visits):\n")
            out.append(md_table(pd.DataFrame({
                "meeting": pd.to_datetime(tt["meeting_start"], utc=True),
                "partner": tt["partner_name"],
                "came from": [f"{_txt(a, 'unnamed anchorage')} ({_txt(b, '?')})" if pd.notna(b) else
                              "none recorded" for a, b in zip(tt["prev_port"], tt["prev_port_flag"])],
                "days before": tt["days_since_prev"],
                "next ports": tt["next_ports"].map(lambda x: _txt(x, "none recorded within 60 days"))})))
    return "\n".join(out) + "\n"


def _own_ports_around(ev: pd.DataFrame, when: pd.Timestamp) -> tuple[str, str]:
    pv = ev[ev["type"] == "port_visit"]
    before = pv[pv["end"] <= when].sort_values("end")
    after = pv[pv["start"] >= when].sort_values("start")

    def f(r):
        return f"{r['port_name'] if pd.notna(r['port_name']) else 'unnamed anchorage'} " \
               f"({r['port_flag']}, {d(r['start'])})"
    return (f(before.iloc[-1]) if not before.empty else "none recorded",
            f(after.iloc[0]) if not after.empty else "none recorded")


def section_ladder(c: CaseData) -> str:
    p = c.partners
    if p is None or p.empty:
        return ""
    strong = p[p["alongside"].fillna(False).astype(bool)
               | (p["partner_listed"].fillna(False).astype(bool) & ~p["crowded"].fillna(False).astype(bool))]
    if strong.empty:
        return ""
    out = ["## Evidence ladder for the strongest meetings\n",
           "Ticked = supported by data in this repo; unticked = still to check by hand.\n"]
    trips = c.trips if c.trips is not None else pd.DataFrame()
    for eid, g in strong.sort_values("dist_km").groupby("event_id", sort=False):
        m = g.iloc[0]
        prev_own, next_own = _own_ports_around(c.events, m["start"])
        partner = f"{m['partner_name']} ({m['partner_flag']}" + (
            f", IMO {m['partner_imo']}" if pd.notna(m["partner_imo"]) else "") + ")"
        tr = trips[(trips.get("event_id", pd.Series(dtype=str)) == eid)] if not trips.empty else trips
        tr = tr[tr["partner_vessel_id"] == m["partner_vessel_id"]] if not tr.empty else tr
        cargo_note = ""
        if not tr.empty:
            r = tr.iloc[0]
            if pd.isna(r["prev_port_flag"]) and pd.isna(r["next_ports"]):
                cargo_note = " GFW recorded no port visits for the partner from 30 days before to 60 after."
            else:
                came = (f"{_txt(r['prev_port'], 'an unnamed anchorage')} ({_txt(r['prev_port_flag'], '?')})"
                        if pd.notna(r["prev_port_flag"]) else "no recorded port")
                cargo_note = (f" Partner came from {came} and next called at: "
                              f"{_txt(r['next_ports'], 'nothing recorded within 60 days')}.")
        out += [
            f"### {d(m['start'])} — {partner}, {m['dist_km']:.2f} km, {m['overlap_h']:.0f} h overlap\n",
            f"Position {m['lat']:.3f}, {m['lon']:.3f}. "
            f"This ship's previous port: {prev_own}; next: {next_own}.\n",
            f"- [x] 1. Loitering of {m['duration_h']:.0f} h at sea",
            f"- [x] 2. Partner co-located ({'alongside' if m['alongside'] else 'nearby'})"
            + ("; partner sanctioned today — check designation date" if m["partner_listed"] else ""),
            f"- [ ] 3. Cargo logic — to assess.{cargo_note}",
            "- [ ] 4. Draught change — not obtained (no free historical source covering the period)",
            f"- [ ] 5. Satellite imagery — [open Sentinel imagery for these dates]"
            f"({copernicus_link(m['lat'], m['lon'], m['start'])})",
            "- [ ] 6. Independent reporting / official record\n",
        ]
    return "\n".join(out) + "\n"


def section_data_issues(c: CaseData) -> str:
    fl = c.flags
    dq = fl[fl["rule"].isin(["suspect_long_port_visit", "unexplained_silence"])] if not fl.empty else fl
    out = ["## Data issues and blind spots\n"]
    if dq is None or dq.empty:
        out.append("_No suspect records detected._")
    else:
        for _, r in dq.sort_values("start").iterrows():
            out.append(f"- **{d(r['start'])}** — {_meaning(r['rule'])} — {r['detail']}")
    return "\n".join(out) + "\n"


def section_sources(c: CaseData) -> str:
    lines = ["## Sources\n",
             "- Global Fishing Watch API v3 (events, vessel identity). Events are *apparent* and "
             "algorithmic; see the GFW data caveats.",
             f"- OpenSanctions maritime dataset ({c.sanctions_file or 'not loaded'}), CC BY-NC 4.0. "
             "Confirm each listing in the original EU / UK notice before publishing."]
    sc = c.sanctions
    if sc is not None and not sc.empty and "os_url" in sc:
        urls = sc[sc["listed"].fillna(False).astype(bool)][["name", "os_url"]].dropna()
        for _, r in urls.iterrows():
            first = str(r["os_url"]).split()[0]
            lines.append(f"  - {r['name']}: {first}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- build
def build(c: CaseData) -> str:
    v = c.vessel
    head = (f"# {v['name']} (IMO {v['imo']})\n\n"
            f"_Case file generated {date.today():%Y-%m-%d} by shadow-fleet-tracker. All events are "
            "apparent events derived from AIS by Global Fishing Watch; flags are leads to verify, "
            "not findings._\n")
    parts = [head, section_summary(c), section_identity(c), section_ports(c),
             section_meetings(c), section_ladder(c), section_data_issues(c), section_sources(c)]
    return "\n".join(p for p in parts if p)


def run(vessels, events, identities, flags, partners=None, trips=None, sanctions=None,
        sanctions_file=None, out: Path = CASES) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for _, v in vessels.iterrows():
        imo = v["imo"]
        ev = events[events["imo"] == imo]
        if ev.empty:
            continue
        c = CaseData(
            vessel=v, events=ev,
            identities=identities[identities["imo"] == imo] if identities is not None else None,
            flags=flags[flags["imo"] == imo],
            partners=partners[partners["imo"] == imo] if partners is not None else None,
            trips=trips, sanctions=sanctions, sanctions_file=sanctions_file)
        path = out / f"{imo}-{slug(v['name'])}.md"
        path.write_text(build(c))
        written[imo] = path
        print(f"Case file: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return written
