"""Step 4: one interactive map per vessel + an index page, into dashboard/site/.

Layers (toggle in the map):
  port visits (blue) · AIS gaps (red dashed line from 'off' to 'on') ·
  encounters (orange) · loitering (purple) · STS hotspots & Russian terminals.
Flagged events get a ⚑ in their popup.
"""

from __future__ import annotations

import html
from pathlib import Path

import folium
import pandas as pd

from .config import SITE
from .geo import RUSSIAN_TERMINALS, STS_HOTSPOTS

COLORS = {"port_visit": "#2c7fb8", "gap": "#d7301f", "encounter": "#fd8d3c",
          "loitering": "#7b3294"}
ATTRIBUTION = "Vessel events: Global Fishing Watch (globalfishingwatch.org), apparent events."


def _popup(row: pd.Series, flagged: dict[str, str]) -> str:
    lines = [f"<b>{html.escape(str(row['type']))}</b>",
             f"{row['start']:%Y-%m-%d %H:%M} → {row['end']:%Y-%m-%d %H:%M} UTC"
             if pd.notna(row["start"]) and pd.notna(row["end"]) else "",
             f"{row['duration_h']:.1f} h" if pd.notna(row["duration_h"]) else ""]
    if row["type"] == "port_visit" and pd.notna(row.get("port_name")):
        lines.append(f"port: {html.escape(str(row['port_name']))} ({row.get('port_flag')})")
    if row["type"] == "encounter" and pd.notna(row.get("partner_name")):
        lines.append(f"with: {html.escape(str(row['partner_name']))} ({row.get('partner_flag')})")
    if row["event_id"] in flagged:
        lines.append(f"<span style='color:#d7301f'>⚑ {html.escape(flagged[row['event_id']])}</span>")
    return "<br>".join(x for x in lines if x)


def vessel_map(ev: pd.DataFrame, flags: pd.DataFrame, title: str) -> folium.Map:
    pts = ev[["lat", "lon"]].dropna()
    center = [pts["lat"].mean(), pts["lon"].mean()] if not pts.empty else [45, 20]
    m = folium.Map(location=center, zoom_start=4, tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
              "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri", name="Esri light grey", max_zoom=16,
    ).add_to(m)
    flagged = (flags.groupby("event_id")["detail"]
               .apply(lambda s: "; ".join(map(str, s))).to_dict())

    for etype, color in COLORS.items():
        layer = folium.FeatureGroup(name=etype.replace("_", " "))
        for _, row in ev[ev["type"] == etype].iterrows():
            popup = folium.Popup(_popup(row, flagged), max_width=320)
            if etype == "gap" and pd.notna(row["gap_off_lat"]) and pd.notna(row["gap_on_lat"]):
                folium.PolyLine([(row["gap_off_lat"], row["gap_off_lon"]),
                                 (row["gap_on_lat"], row["gap_on_lon"])],
                                color=color, weight=3 if row["event_id"] in flagged else 1.5,
                                dash_array="6 6", popup=popup).add_to(layer)
            elif pd.notna(row["lat"]):
                folium.CircleMarker((row["lat"], row["lon"]),
                                    radius=7 if row["event_id"] in flagged else 4,
                                    color=color, fill=True, fill_opacity=0.8,
                                    popup=popup).add_to(layer)
        layer.add_to(m)

    ref = folium.FeatureGroup(name="STS hotspots & RU terminals (approx.)")
    for name, (la, lo) in STS_HOTSPOTS.items():
        folium.Circle((la, lo), radius=50_000, color="#444", fill=False, weight=1,
                      tooltip=f"STS area: {name}").add_to(ref)
    for name, (la, lo) in RUSSIAN_TERMINALS.items():
        folium.Marker((la, lo), tooltip=f"Terminal: {name}",
                      icon=folium.Icon(color="black", icon="tint")).add_to(ref)
    ref.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(
        f"<div style='position:fixed;bottom:8px;left:8px;z-index:9999;background:#fff;"
        f"padding:4px 8px;font:12px sans-serif'><b>{html.escape(title)}</b> · {ATTRIBUTION}</div>"))
    if not pts.empty:
        m.fit_bounds([[pts["lat"].min(), pts["lon"].min()], [pts["lat"].max(), pts["lon"].max()]])
    return m


def run(events: pd.DataFrame, flags: pd.DataFrame, vessels: pd.DataFrame,
        summary: pd.DataFrame | None = None, out: Path = SITE) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    names = vessels.set_index("imo")["name"].to_dict()
    groups = vessels.set_index("imo")["group"].to_dict()
    items = []
    for imo, ev in events.groupby("imo"):
        title = f"{names.get(imo, '')} (IMO {imo})"
        vessel_map(ev, flags[flags["imo"] == imo], title).save(out / f"{imo}.html")
        n_flags = int((flags["imo"] == imo).sum())
        items.append(f"<tr><td><a href='{imo}.html'>{html.escape(title)}</a></td>"
                     f"<td>{html.escape(str(groups.get(imo, '')))}</td>"
                     f"<td>{len(ev)}</td><td>{n_flags}</td></tr>")
    table = summary.to_html(index=False, classes="summary") if summary is not None else ""
    (out / "index.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shadow Fleet Tracker</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 16px}}
table{{border-collapse:collapse;width:100%;margin:1rem 0;font-size:13px}}
td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}}
.note{{color:#555;font-size:13px}}</style></head><body>
<h1>Shadow Fleet Tracker</h1>
<p class="note">Work in progress. All events are <em>apparent</em> events derived from AIS by
Global Fishing Watch. Flags are leads for verification, not findings.</p>
<h2>Vessels</h2>
<table><tr><th>Vessel</th><th>Group</th><th>Events</th><th>Flags</th></tr>{''.join(items)}</table>
<h2>Flag rates (per 30 days observed)</h2>{table}
<p class="note">{ATTRIBUTION}</p></body></html>""")
    print(f"Maps written to {out}")
    return out
