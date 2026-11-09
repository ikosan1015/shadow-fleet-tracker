"""Static dashboard in dashboard/site/ (publish with `make publish` -> GitHub Pages).

index.html            overview: vessels, strongest leads, sanctioned partners met
case-<imo>.html       case file + activity timeline for one vessel
<imo>.html            interactive map for one vessel (built by maps.py)
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

import markdown as md
import pandas as pd
import plotly.graph_objects as go

from . import maps
from .config import SITE
from .flags import RULE_INFO

REPO_URL = "https://github.com/ikosan1015/shadow-fleet-tracker"
TYPE_COLORS = {"port_visit": "#2c7fb8", "loitering": "#7b3294", "gap": "#d7301f",
               "encounter": "#fd8d3c"}
STRENGTH_COLORS = {3: "#c0392b", 2: "#e67e22", 1: "#95a5a6"}

CSS = """
:root{--bg:#fafaf8;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--card:#fff;--accent:#c0392b;
--chip:#f0f0ec;--link:#1f5fa8}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececec;--muted:#a0a0a8;--line:#2c2c30;
--card:#1c1c1f;--chip:#26262a;--link:#7fb0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--link)}main{max-width:1080px;margin:0 auto;padding:28px 16px 64px}
header h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 20px}
.note{background:var(--chip);border-left:3px solid var(--accent);padding:10px 14px;border-radius:6px;
font-size:13.5px;color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:22px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.stat b{display:block;font-size:26px;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card h3{margin:0 0 2px;font-size:18px}.card .meta{color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:12.5px}
.chip.red{background:#c0392b;color:#fff}.chip.orange{background:#e67e22;color:#fff}
.btns a{display:inline-block;margin-right:10px;font-weight:600;text-decoration:none}
h2{font-size:20px;margin:34px 0 10px;padding-top:8px;border-top:1px solid var(--line)}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 16px;display:block;overflow-x:auto}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
footer{margin-top:40px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:14px}
.case h1{display:none}.case ul li input{margin-right:6px}
nav{font-size:14px;margin-bottom:14px}
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}"
            f"<footer>Vessel events: Global Fishing Watch (apparent, AIS-derived). Sanctions data: "
            f"OpenSanctions (CC BY-NC 4.0). Flags are leads for verification, not findings. "
            f"Built {date.today():%Y-%m-%d} · <a href='{REPO_URL}'>code and methodology</a>"
            f"</footer></main></body></html>")


def _d(ts, fmt="%Y-%m-%d"):
    return ts.strftime(fmt) if isinstance(ts, pd.Timestamp) and pd.notna(ts) else "?"


def _strength(rule):
    return RULE_INFO.get(rule, ("other", 0, rule))[1]


def _meaning(rule):
    return RULE_INFO.get(rule, ("other", 0, rule))[2]


# -------------------------------------------------------------------- timeline
def timeline(ev: pd.DataFrame, fl: pd.DataFrame, plotlyjs: str | bool = "cdn") -> str:
    fig = go.Figure()
    for etype, color in TYPE_COLORS.items():
        d = ev[ev["type"] == etype].dropna(subset=["start"])
        if d.empty:
            continue
        end = d["end"].fillna(d["start"])
        dur_ms = ((end - d["start"]).dt.total_seconds() * 1000).clip(lower=6 * 3600 * 1000)
        label = d.get("port_name", pd.Series(index=d.index, dtype=object)).fillna("")
        fig.add_bar(x=dur_ms, base=d["start"], y=[etype.replace("_", " ")] * len(d),
                    orientation="h", marker_color=color, name=etype.replace("_", " "),
                    customdata=list(zip(d["start"].dt.strftime("%Y-%m-%d"),
                                        end.dt.strftime("%Y-%m-%d"), label)),
                    hovertemplate="%{customdata[0]} → %{customdata[1]}<br>%{customdata[2]}"
                                  "<extra></extra>")
    if fl is not None and not fl.empty:
        f = fl.assign(strength=fl["rule"].map(_strength)).dropna(subset=["start"])
        for s, color in STRENGTH_COLORS.items():
            d = f[f["strength"] == s]
            if d.empty:
                continue
            fig.add_scatter(x=d["start"], y=["flags"] * len(d), mode="markers",
                            marker=dict(color=color, size=8 + 3 * s, symbol="diamond"),
                            name=f"flag strength {s}",
                            text=[f"{_meaning(r)}<br>{html.escape(str(x))}"
                                  for r, x in zip(d["rule"], d["detail"])],
                            hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra></extra>")
    fig.update_layout(barmode="overlay", height=300, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=-0.25), xaxis=dict(type="date"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="system-ui, sans-serif", size=12))
    present = [t.replace("_", " ") for t in ("port_visit", "loitering", "encounter", "gap")
               if (ev["type"] == t).any()]
    if fl is not None and not fl.empty:
        present.append("flags")
    fig.update_yaxes(categoryorder="array", categoryarray=present)
    return fig.to_html(include_plotlyjs=plotlyjs, full_html=False, config={"displayModeBar": False})


# ----------------------------------------------------------------------- pages
def case_page(v: pd.Series, ev: pd.DataFrame, fl: pd.DataFrame, case_md: str) -> str:
    body = md.markdown(case_md, extensions=["tables", "sane_lists"])
    body = body.replace("<li>[x]", "<li><input type='checkbox' checked disabled>") \
               .replace("<li>[ ]", "<li><input type='checkbox' disabled>")
    title = f"{v['name']} (IMO {v['imo']})"
    return page(title, (
        f"<nav><a href='index.html'>← All vessels</a></nav><header><h1>{html.escape(title)}</h1>"
        f"<p class='sub'>Case file · <a href='{v['imo']}.html'>open interactive map</a></p></header>"
        f"<h2>Activity timeline</h2>{timeline(ev, fl)}<div class='case'>{body}</div>"))


def vessel_card(v, ev, fl, partners, sanctions) -> str:
    chips = []
    s = None
    if sanctions is not None and not sanctions.empty:
        hit = sanctions[sanctions["imo"].astype(str) == str(v["imo"])]
        s = hit.iloc[0] if not hit.empty else None
    if s is not None and bool(s.get("listed")):
        chips.append("<span class='chip red'>sanctioned</span>")
    if s is not None and bool(s.get("shadow_tag")):
        chips.append("<span class='chip'>shadow-fleet tag</span>")
    if s is not None and bool(s.get("detained")):
        chips.append("<span class='chip'>detention record</span>")
    cat = fl["rule"].map(lambda r: RULE_INFO.get(r, ("other",))[0]) if not fl.empty else pd.Series(dtype=str)
    st = fl["rule"].map(_strength) if not fl.empty else pd.Series(dtype=int)
    strong = int(((st >= 3) & (cat != "data quality")).sum())
    medium = int(((st == 2) & (cat != "data quality")).sum())
    issues = int((cat == "data quality").sum())
    if strong:
        chips.append(f"<span class='chip red'>{strong} strong lead{'s' if strong > 1 else ''}</span>")
    if medium:
        chips.append(f"<span class='chip orange'>{medium} medium lead{'s' if medium > 1 else ''}</span>")
    if issues:
        chips.append(f"<span class='chip'>{issues} data issue{'s' if issues > 1 else ''}</span>")
    met = 0
    if partners is not None and not partners.empty:
        pp = partners[partners["imo"] == v["imo"]]
        met = pp[pp["partner_listed"].fillna(False).astype(bool)]["partner_vessel_id"].nunique()
    period = f"{_d(ev['start'].min(), '%b %Y')} – {_d(ev['end'].max(), '%b %Y')}" if not ev.empty else ""
    return (f"<div class='card'><h3>{html.escape(str(v['name']))}</h3>"
            f"<div class='meta'>IMO {v['imo']} · {html.escape(str(v.get('group', '')))} · {period}"
            f"</div><div class='chips'>{''.join(chips)}</div>"
            f"<div class='meta'>{len(ev)} events · {met} sanctioned vessel(s) loitering within 5 km</div>"
            f"<p class='btns'><a href='case-{v['imo']}.html'>Case file →</a>"
            f"<a href='{v['imo']}.html'>Map →</a></p></div>")


def index_page(vessels, events, flags, partners, sanctions) -> str:
    names = vessels.set_index("imo")["name"].to_dict()
    strong = flags.assign(strength=flags["rule"].map(_strength)) if not flags.empty else flags
    if not strong.empty:
        cat = strong["rule"].map(lambda r: RULE_INFO.get(r, ("other",))[0])
        leads = strong[(strong["strength"] >= 2) & (cat != "data quality")].sort_values(
            ["strength", "start"], ascending=[False, True])
    else:
        leads = strong
    n_listed_met = 0
    if partners is not None and not partners.empty:
        n_listed_met = partners[partners["partner_listed"].fillna(False).astype(bool)][
            "partner_vessel_id"].nunique()
    stats = [(len(vessels), "vessels studied"), (len(events), "AIS-derived events"),
             (len(leads), "medium/strong leads"), (n_listed_met, "sanctioned vessels loitering within 5 km")]
    rows = "".join(
        f"<tr><td>{_d(r['start'])}</td><td><a href='case-{r['imo']}.html'>"
        f"{html.escape(str(names.get(r['imo'], r['imo'])))}</a></td>"
        f"<td><span class='dot' style='background:{STRENGTH_COLORS[r['strength']]}'></span>"
        f"{html.escape(_meaning(r['rule']))}</td><td>{html.escape(str(r['detail']))}</td></tr>"
        for _, r in leads.iterrows())
    cards = "".join(vessel_card(v, events[events["imo"] == v["imo"]], flags[flags["imo"] == v["imo"]],
                                partners, sanctions) for _, v in vessels.iterrows())
    return page("Shadow Fleet Tracker", (
        "<header><h1>Shadow Fleet Tracker</h1><p class='sub'>Reconstructing the voyages of sanctioned "
        "tankers from open AIS data, sanctions lists and public records.</p></header>"
        "<p class='note'>Work in progress. Every event is an <em>apparent</em> event inferred from AIS "
        "by Global Fishing Watch, and every flag is a lead to verify, not a finding. “Sanctioned” means "
        "listed today according to OpenSanctions, not necessarily at the time of an event.</p>"
        "<div class='stats'>" + "".join(f"<div class='stat'><b>{n:,}</b><span>{t}</span></div>"
                                        for n, t in stats) + "</div>"
        f"<h2>Vessels</h2><div class='cards'>{cards}</div>"
        "<h2>Strongest leads</h2><table><tr><th>date</th><th>vessel</th><th>what</th><th>detail</th>"
        f"</tr>{rows or '<tr><td colspan=4>none yet</td></tr>'}</table>"))


def build(vessels, events, flags, partners=None, sanctions=None, case_files=None,
          summary=None, out: Path = SITE) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    maps.run(events, flags, vessels, summary, out=out, partners=partners)   # per-vessel maps
    for _, v in vessels.iterrows():
        ev = events[events["imo"] == v["imo"]]
        if ev.empty:
            continue
        case_md = case_files[v["imo"]].read_text() if case_files and v["imo"] in case_files else ""
        (out / f"case-{v['imo']}.html").write_text(
            case_page(v, ev, flags[flags["imo"] == v["imo"]], case_md))
    (out / "index.html").write_text(index_page(vessels, events, flags, partners, sanctions))
    print(f"Dashboard written to {out}/index.html")
    return out
