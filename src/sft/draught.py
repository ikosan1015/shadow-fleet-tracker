"""Draught check: did the ship sit deeper or shallower between two passages?

Draught (how deep the hull sits) is broadcast in AIS. A laden tanker sits deep,
an empty one shallow. GFW's public API does not provide it, but the Danish
Maritime Authority (DMA) publishes free daily AIS files for Danish waters, and
almost every Baltic tanker passes through them (Skagen, the Great Belt, the Sound).

So each passage through Danish waters gives one "snapshot" of how loaded the ship
was. Comparing consecutive snapshots against the port calls in between tells us
whether cargo apparently changed without a port call — a classic STS signal.

Caveat: draught is typed in by the crew and is sometimes stale or wrong. Treat it
as corroboration (grade it B3/C3), never as proof on its own.

Steps:
  suggest_days()  which days the ship was probably in Danish waters
  fetch_day()     download one DMA day file, keep only our ships, cache as parquet
  passages()      one row per ship per day: median draught, destination, direction hint
  flags()         draught dropped (or rose) between passages with no matching port call
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import requests

from .config import DMA_DIR, DMA_URLS, PROCESSED

# rough box around Danish waters covered by DMA receivers
DK_BOX = (53.5, 58.5, 7.5, 15.5)            # lat_min, lat_max, lon_min, lon_max
BALTIC_EAST = (53.5, 66.0, 15.5, 31.0)      # Baltic beyond the Danish straits

WANTED = {"timestamp", "mmsi", "imo", "latitude", "longitude", "draught",
          "destination", "sog", "navigational status", "name"}


def _norm(col: str) -> str:
    return col.replace("#", "").strip().lower()


def _in(lat, lon, b) -> bool:
    return b[0] <= lat <= b[1] and b[2] <= lon <= b[3]


# ----------------------------------------------------------------- which days
def suggest_days(events: pd.DataFrame, transit_days: tuple[int, int] = (1, 4)) -> pd.DataFrame:
    """Days a ship was probably in Danish waters, with the reason.

    - any GFW event inside the Danish box (e.g. a Skagen call): that day +/- 1
    - a port visit in the eastern Baltic: the 1-4 days after leaving (outbound
      transit) and before arriving (inbound transit)
    """
    rows = []
    for _, e in events.dropna(subset=["lat", "lon", "start"]).iterrows():
        end = e["end"] if pd.notna(e["end"]) else e["start"]
        if _in(e["lat"], e["lon"], DK_BOX):
            for d in pd.date_range(e["start"].normalize() - pd.Timedelta(days=1),
                                   end.normalize() + pd.Timedelta(days=1)):
                rows.append((e["imo"], d.date().isoformat(), f"{e['type']} in Danish waters"))
        elif e["type"] == "port_visit" and _in(e["lat"], e["lon"], BALTIC_EAST):
            port = e.get("port_name") if pd.notna(e.get("port_name")) else "Baltic port"
            lo, hi = transit_days
            for k in range(lo, hi + 1):
                rows.append((e["imo"], (end + pd.Timedelta(days=k)).date().isoformat(),
                             f"likely outbound transit after {port}"))
                rows.append((e["imo"], (e["start"] - pd.Timedelta(days=k)).date().isoformat(),
                             f"likely inbound transit before {port}"))
    df = pd.DataFrame(rows, columns=["imo", "day", "reason"])
    if df.empty:
        return df
    return (df.groupby(["imo", "day"])["reason"].agg(lambda s: "; ".join(sorted(set(s))))
            .reset_index().sort_values(["day", "imo"]).reset_index(drop=True))


# ------------------------------------------------------------- download + filter
def _locate_or_download(day: str, dma_dir: Path, session: requests.Session | None = None) -> Path:
    for name in (f"aisdk-{day}.zip", f"aisdk-{day}.csv"):
        if (dma_dir / name).exists():
            return dma_dir / name
    session = session or requests.Session()
    dma_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for pattern in DMA_URLS:
        url = pattern.format(day=day)
        try:
            with session.get(url, stream=True, timeout=120) as r:
                if r.status_code != 200:
                    errors.append(f"{url}: HTTP {r.status_code}")
                    continue
                target = dma_dir / f"aisdk-{day}.zip"
                tmp = target.with_suffix(".part")
                size = 0
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
                        size += len(chunk)
                        print(f"\r  downloading {day}: {size / 1e6:,.0f} MB", end="", flush=True)
                print()
                tmp.rename(target)
                return target
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    raise FileNotFoundError(
        f"No DMA file for {day}. Tried:\n  " + "\n  ".join(errors)
        + f"\nDownload it by hand from http://web.ais.dk/aisdata/ into {dma_dir}/")


def _open_csv(path: Path):
    if path.suffix == ".zip":
        zf = zipfile.ZipFile(path)
        inner = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        return io.TextIOWrapper(zf.open(inner), encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def read_filtered(path: Path, mmsis: set[str], imos: set[str],
                  chunksize: int = 1_000_000) -> pd.DataFrame:
    """Stream a (huge) DMA file and keep only rows for our ships."""
    keep = []
    with _open_csv(path) as fh:
        reader = pd.read_csv(fh, dtype=str, chunksize=chunksize,
                             usecols=lambda c: _norm(c) in WANTED, on_bad_lines="skip")
        for chunk in reader:
            chunk.columns = [_norm(c) for c in chunk.columns]
            m = chunk["mmsi"].isin(mmsis)
            if "imo" in chunk:
                m |= chunk["imo"].str.replace("IMO", "", regex=False).str.strip().isin(imos)
            if m.any():
                keep.append(chunk[m])
    if not keep:
        return pd.DataFrame(columns=sorted(WANTED))
    df = pd.concat(keep, ignore_index=True)
    ts = pd.to_datetime(df["timestamp"], format="%d/%m/%Y %H:%M:%S", errors="coerce", utc=True)
    miss = ts.isna()
    if miss.any():  # newer files may use ISO timestamps
        ts[miss] = pd.to_datetime(df.loc[miss, "timestamp"], errors="coerce", utc=True)
    df["timestamp"] = ts
    for c in ("latitude", "longitude", "draught", "sog"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fetch_day(day: str, mmsis: set[str], imos: set[str], dma_dir: Path = DMA_DIR,
              keep_file: bool = False, session: requests.Session | None = None) -> pd.DataFrame:
    """One day's DMA rows for our ships, cached so each big file is read only once."""
    cache = dma_dir / "filtered" / f"{day}.parquet"
    meta = cache.with_suffix(".json")
    if cache.exists() and meta.exists():
        done = json.loads(meta.read_text())
        if mmsis <= set(done["mmsis"]) and imos <= set(done["imos"]):
            return pd.read_parquet(cache)
    path = _locate_or_download(day, dma_dir, session)
    print(f"  filtering {path.name} …")
    df = read_filtered(path, mmsis, imos)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    meta.write_text(json.dumps({"mmsis": sorted(mmsis), "imos": sorted(imos)}))
    if not keep_file:
        path.unlink()  # these files are huge; the filtered copy is all we need
    return df


# ------------------------------------------------------------------ analysis
def attach_imo(reports: pd.DataFrame, identities: pd.DataFrame) -> pd.DataFrame:
    """Map MMSI (and IMO column if present) back to the IMO we study."""
    ais = identities[identities["source"] == "AIS self-reported"]
    by_mmsi = dict(zip(ais["ssvid"].astype(str), ais["imo"]))
    df = reports.copy()
    df["study_imo"] = df["mmsi"].astype(str).map(by_mmsi)
    if "imo" in df:
        own = df["imo"].astype(str).str.replace("IMO", "", regex=False).str.strip()
        df["study_imo"] = df["study_imo"].fillna(own.where(own.isin(set(ais["imo"]))))
    return df.dropna(subset=["study_imo"])


def passages(reports: pd.DataFrame) -> pd.DataFrame:
    """One row per ship per day in Danish waters: how deep it sat and where it said it went."""
    if reports.empty:
        return pd.DataFrame(columns=["imo", "day", "first_seen", "last_seen", "n_reports",
                                     "draught_m", "draught_min", "draught_max",
                                     "destination", "direction_hint", "lat", "lon"])
    r = reports.dropna(subset=["timestamp"]).copy()
    r["day"] = r["timestamp"].dt.date.astype(str)
    rows = []
    for (imo, day), g in r.sort_values("timestamp").groupby(["study_imo", "day"]):
        d = g["draught"][g["draught"] > 0] if "draught" in g else pd.Series(dtype=float)
        pos = g.dropna(subset=["latitude", "longitude"])
        pos = pos[pos["latitude"].between(-90, 90) & pos["longitude"].between(-180, 180)]
        hint = "?"
        if len(pos) >= 2:
            dlat = pos["latitude"].iloc[-1] - pos["latitude"].iloc[0]
            hint = "leaving Baltic" if dlat > 0.3 else "entering Baltic" if dlat < -0.3 else "?"
        dest = g["destination"].dropna() if "destination" in g else pd.Series(dtype=str)
        rows.append({
            "imo": imo, "day": day,
            "first_seen": g["timestamp"].min(), "last_seen": g["timestamp"].max(),
            "n_reports": len(g),
            "draught_m": round(float(d.median()), 1) if not d.empty else None,
            "draught_min": float(d.min()) if not d.empty else None,
            "draught_max": float(d.max()) if not d.empty else None,
            "destination": dest.mode().iloc[0] if not dest.empty else None,
            "direction_hint": hint,
            "lat": pos["latitude"].mean() if not pos.empty else None,
            "lon": pos["longitude"].mean() if not pos.empty else None,
        })
    return pd.DataFrame(rows)


def flags(pas: pd.DataFrame, events: pd.DataFrame, min_change_m: float = 2.0,
          min_port_h: float = 12.0) -> pd.DataFrame:
    """Draught changed between two passages, with no port call that explains it.

    - deeper -> shallower, and no NON-Russian port stay >= min_port_h in between:
      "unloaded_without_port_call" (cargo apparently left the ship at sea)
    - shallower -> deeper, and no port stay >= min_port_h in between:
      "loaded_without_port_call" (cargo apparently arrived at sea)
    """
    cols = ["imo", "event_id", "rule", "start", "lat", "lon", "detail"]
    out = []
    pas = pas.dropna(subset=["draught_m"]).sort_values(["imo", "first_seen"])
    for imo, g in pas.groupby("imo"):
        g = g.reset_index(drop=True)
        ev = events[events["imo"] == imo]
        for i in range(1, len(g)):
            a, b = g.loc[i - 1], g.loc[i]
            change = b["draught_m"] - a["draught_m"]
            if abs(change) < min_change_m:
                continue
            between = ev[(ev["start"] >= a["last_seen"]) & (ev["end"] <= b["first_seen"])]
            ports = between[(between["type"] == "port_visit") & (between["duration_h"] >= min_port_h)]
            n_loiter = int((between["type"] == "loitering").sum())
            if change < 0:
                explain = ports[ports["port_flag"].fillna("") != "RUS"]
                rule = "unloaded_without_port_call"
            else:
                explain = ports
                rule = "loaded_without_port_call"
            if not explain.empty:
                continue
            out.append({
                "imo": imo, "event_id": f"draught-{imo}-{a['day']}-{b['day']}", "rule": rule,
                "start": a["last_seen"], "lat": b["lat"], "lon": b["lon"],
                "detail": (f"draught {a['draught_m']} m ({a['day']}) -> {b['draught_m']} m "
                           f"({b['day']}); {len(ports)} port stays, {n_loiter} loitering in between"),
            })
    return pd.DataFrame(out, columns=cols)


def run(events: pd.DataFrame, identities: pd.DataFrame, days: list[str] | None = None,
        max_days: int = 6, keep_files: bool = False, dma_dir: Path = DMA_DIR,
        out: Path = PROCESSED, session: requests.Session | None = None):
    sug = suggest_days(events)
    if days is None:
        days = list(dict.fromkeys(sug["day"]))
        if len(days) > max_days:
            print(f"{len(days)} candidate days; fetching the first {max_days}. "
                  f"Use --max-days or --days to choose. Remaining: {', '.join(days[max_days:])}")
            days = days[:max_days]
    ais = identities[identities["source"] == "AIS self-reported"]
    mmsis = set(ais["ssvid"].dropna().astype(str))
    imos = set(identities["imo"].dropna().astype(str))

    frames = []
    for day in days:
        print(f"[{day}]")
        try:
            frames.append(fetch_day(day, mmsis, imos, dma_dir, keep_files, session))
        except FileNotFoundError as exc:
            print(f"  ! {exc}")
    reports = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    reports = attach_imo(reports, identities) if not reports.empty else reports
    pas = passages(reports)
    # merge with earlier runs so passages accumulate over time
    pas_file = out / "draught_passages.csv"
    if pas_file.exists():
        old = pd.read_csv(pas_file, dtype={"imo": str}, parse_dates=["first_seen", "last_seen"])
        pas = (pd.concat([old, pas]).drop_duplicates(["imo", "day"], keep="last")
               .sort_values(["imo", "day"]).reset_index(drop=True))
    fl = flags(pas, events)
    out.mkdir(parents=True, exist_ok=True)
    pas.to_csv(pas_file, index=False)
    fl.to_parquet(out / "draught_flags.parquet", index=False)
    print(pas.to_string(index=False) if not pas.empty else "No draught reports found for our ships.")
    print(f"{len(fl)} draught flags")
    return pas, fl
