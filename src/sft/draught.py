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
import time
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
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


# ------------------------------------------------------------- fast filtering
# Speed notes (measured on a 6M-row test day): parsing every row with pandas took
# 7.2 s; searching the raw bytes for our ships' MMSI/IMO and parsing only the hits
# took 1.7 s. Downloading is the real bottleneck, so we also unpack and filter WHILE
# downloading (nothing big is written to disk) and can fetch several days at once.

def _needles(mmsis: set[str], imos: set[str]) -> list[bytes]:
    out = [f",{m},".encode() for m in mmsis if m]
    out += [f",{i},".encode() for i in imos if i] + [f",IMO{i},".encode() for i in imos if i]
    return out


class LineFilter:
    """Feed raw CSV bytes in chunks of any size; keeps the header plus every line that
    contains one of the needles. Candidates are re-checked exactly in `to_frame`."""

    def __init__(self, needles: list[bytes]):
        self.needles, self.header, self.rest, self.lines = needles, None, b"", []
        self.bytes_seen = 0

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self.bytes_seen += len(data)
        blk = self.rest + data
        if self.header is None:
            nl = blk.find(b"\n")
            if nl == -1:
                self.rest = blk
                return
            self.header, blk = blk[:nl + 1], blk[nl + 1:]
        cut = blk.rfind(b"\n") + 1
        self.rest, blk = blk[cut:], blk[:cut]
        hits: dict[int, bytes] = {}
        for nd in self.needles:
            i = blk.find(nd)
            while i != -1:
                s = blk.rfind(b"\n", 0, i) + 1
                e = blk.find(b"\n", i) + 1
                hits[s] = blk[s:e]
                i = blk.find(nd, e)
        self.lines.extend(hits[k] for k in sorted(hits))

    def finish(self) -> None:
        if self.rest:
            tail, self.rest = self.rest, b""
            self.feed(tail + b"\n")

    def to_frame(self, mmsis: set[str], imos: set[str]) -> pd.DataFrame:
        self.finish()
        if not self.header or not self.lines:
            return pd.DataFrame(columns=sorted(WANTED))
        df = pd.read_csv(io.BytesIO(self.header + b"".join(self.lines)), dtype=str,
                         usecols=lambda c: _norm(c) in WANTED, on_bad_lines="skip")
        df.columns = [_norm(c) for c in df.columns]
        m = df["mmsi"].isin(mmsis)
        if "imo" in df:
            m |= df["imo"].str.replace("IMO", "", regex=False).str.strip().isin(imos)
        return _tidy(df[m].reset_index(drop=True))


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["timestamp"], format="%d/%m/%Y %H:%M:%S", errors="coerce", utc=True)
    miss = ts.isna()
    if miss.any():  # newer files may use ISO timestamps
        ts[miss] = pd.to_datetime(df.loc[miss, "timestamp"], errors="coerce", utc=True)
    df["timestamp"] = ts
    for c in ("latitude", "longitude", "draught", "sog"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


class ZipStream:
    """Unpack the first file of a .zip while it is still downloading (no seeking needed)."""

    def __init__(self):
        self.buf, self.d, self.done = b"", None, False

    def feed(self, data: bytes) -> bytes:
        if self.done:
            return b""
        if self.d is None:
            self.buf += data
            if len(self.buf) < 30:
                return b""
            if self.buf[:4] != b"PK\x03\x04":
                raise ValueError("not a zip file")
            method = int.from_bytes(self.buf[8:10], "little")
            start = 30 + int.from_bytes(self.buf[26:28], "little") + int.from_bytes(self.buf[28:30], "little")
            if len(self.buf) < start:
                return b""
            if method != 8:
                raise ValueError(f"unsupported zip compression method {method}")
            self.d = zlib.decompressobj(-15)
            data, self.buf = self.buf[start:], b""
        out = self.d.decompress(data)
        if self.d.eof:
            self.done = True
        return out


def filter_local(path: Path, mmsis: set[str], imos: set[str], block: int = 1 << 24) -> pd.DataFrame:
    """Fast filter for a DMA file already on disk (.zip or .csv)."""
    lf = LineFilter(_needles(mmsis, imos))
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            inner = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(inner) as fh:
                while chunk := fh.read(block):
                    lf.feed(chunk)
    else:
        with open(path, "rb") as fh:
            while chunk := fh.read(block):
                lf.feed(chunk)
    return lf.to_frame(mmsis, imos)


read_filtered = filter_local  # backwards-compatible name


def stream_filter(day: str, mmsis: set[str], imos: set[str], session: requests.Session,
                  keep_to: Path | None = None) -> pd.DataFrame:
    """Download one DMA day and filter it on the fly. Optionally also save the zip."""
    errors = []
    for pattern in DMA_URLS:
        url = pattern.format(day=day)
        try:
            with session.get(url, stream=True, timeout=120) as r:
                if r.status_code != 200:
                    errors.append(f"{url}: HTTP {r.status_code}")
                    continue
                zs, lf = ZipStream(), LineFilter(_needles(mmsis, imos))
                fh = open(keep_to, "wb") if keep_to else None
                t0, got, shown = time.time(), 0, 0
                try:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if fh:
                            fh.write(chunk)
                        got += len(chunk)
                        lf.feed(zs.feed(chunk))
                        if got - shown >= 50e6:
                            shown = got
                            rate = got / 1e6 / max(time.time() - t0, 1e-6)
                            print(f"  {day}: {got / 1e6:,.0f} MB at {rate:.1f} MB/s", flush=True)
                        if zs.done and not fh:
                            break
                finally:
                    if fh:
                        fh.close()
                secs = time.time() - t0
                print(f"  {day}: done, {got / 1e6:,.0f} MB in {secs:.0f} s "
                      f"({got / 1e6 / max(secs, 1e-6):.1f} MB/s)", flush=True)
                return lf.to_frame(mmsis, imos)
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    raise FileNotFoundError(
        f"No DMA file for {day}. Tried:\n  " + "\n  ".join(errors)
        + "\nDownload it by hand from http://web.ais.dk/aisdata/ into data/raw/dma/")


def fetch_day(day: str, mmsis: set[str], imos: set[str], dma_dir: Path = DMA_DIR,
              keep_file: bool = False, session: requests.Session | None = None) -> pd.DataFrame:
    """One day's DMA rows for our ships, cached so each big file is processed only once."""
    cache = dma_dir / "filtered" / f"{day}.parquet"
    meta = cache.with_suffix(".json")
    if cache.exists() and meta.exists():
        done = json.loads(meta.read_text())
        if mmsis <= set(done["mmsis"]) and imos <= set(done["imos"]):
            print(f"  {day}: cached")
            return pd.read_parquet(cache)
    local = next((dma_dir / n for n in (f"aisdk-{day}.zip", f"aisdk-{day}.csv")
                  if (dma_dir / n).exists()), None)
    if local:
        print(f"  {day}: filtering local file {local.name}")
        df = filter_local(local, mmsis, imos)
        if not keep_file:
            local.unlink()
    else:
        dma_dir.mkdir(parents=True, exist_ok=True)
        df = stream_filter(day, mmsis, imos, session or requests.Session(),
                           keep_to=dma_dir / f"aisdk-{day}.zip" if keep_file else None)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    meta.write_text(json.dumps({"mmsis": sorted(mmsis), "imos": sorted(imos)}))
    print(f"  {day}: {len(df)} reports for our ships")
    return df


# --------------------------------------------------- where exactly was the ship?
def locate_days(client, identities: pd.DataFrame, start: str, end: str,
                chunk_days: int = 90) -> pd.DataFrame:
    """Ask GFW's hourly presence data which days each ship was inside the Danish box.

    One small API report per ~3 months, filtered to the flags our ships used, instead
    of guessing days and downloading ~0.7 GB per guess.
    """
    from .config import PRESENCE_DATASET
    ais = identities[identities["source"] == "AIS self-reported"].dropna(subset=["vessel_id"])
    if ais.empty:
        return pd.DataFrame(columns=["imo", "day", "hours"])
    vid_to_imo = dict(zip(ais["vessel_id"], ais["imo"]))
    flags_used = sorted({f for f in ais["flag"].dropna() if f})
    lat0, lat1, lon0, lon1 = DK_BOX
    ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
    geojson = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
               "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    rows = []
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    while s < e:
        t = min(s + pd.Timedelta(days=chunk_days), e)
        params = [("spatial-aggregation", "true"), ("temporal-resolution", "DAILY"),
                  ("group-by", "VESSEL_ID"), ("datasets[0]", PRESENCE_DATASET),
                  ("date-range", f"{s:%Y-%m-%d},{t:%Y-%m-%d}"), ("format", "JSON")]
        if flags_used:
            params.append(("filters[0]", "flag in (" + ",".join(f"'{f}'" for f in flags_used) + ")"))
        print(f"  GFW presence {s:%Y-%m-%d} -> {t:%Y-%m-%d} …", flush=True)
        for rec in _records(client.report(params, {"geojson": geojson})):
            vid = rec.get("vesselId") or rec.get("vessel_id") or rec.get("id")
            if vid in vid_to_imo and rec.get("date"):
                rows.append({"imo": vid_to_imo[vid], "day": str(rec["date"])[:10],
                             "hours": float(rec.get("hours") or 0)})
        s = t
    df = pd.DataFrame(rows, columns=["imo", "day", "hours"])
    if df.empty:
        return df
    return df.groupby(["imo", "day"], as_index=False)["hours"].sum().sort_values(["imo", "day"])


def _records(obj):
    """Walk any JSON shape and yield dicts that look like presence rows."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "date" in cur and ("vesselId" in cur or "vessel_id" in cur):
                yield cur
            else:
                stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


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


def choose_days(events: pd.DataFrame, identities: pd.DataFrame, client=None,
                start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Exact days from GFW presence if possible, otherwise the rougher suggestions."""
    start = start or f"{events['start'].min():%Y-%m-%d}"
    end = end or f"{(events['end'].max() + pd.Timedelta(days=1)):%Y-%m-%d}"
    try:
        if client is None:
            from .config import get_token
            from .gfw import GFWClient
            client = GFWClient(get_token())
        located = locate_days(client, identities, start, end)
        print(f"GFW presence: {len(located)} day(s) in Danish waters between {start} and {end}")
        return located.assign(reason="GFW presence: " + located["hours"].round(1).astype(str) + " h")
    except Exception as exc:  # no token, API change, network…: fall back, but say so
        print(f"! could not locate days via GFW presence ({exc}); using rough suggestions")
        sug = suggest_days(events)
        return sug[(sug["day"] >= start) & (sug["day"] < end)]


def run(events: pd.DataFrame, identities: pd.DataFrame, days: list[str] | None = None,
        max_days: int = 6, keep_files: bool = False, dma_dir: Path = DMA_DIR,
        out: Path = PROCESSED, session: requests.Session | None = None, client=None,
        start: str | None = None, end: str | None = None, parallel: int = 2):
    if days is None:
        cand = choose_days(events, identities, client, start, end)
        days = list(dict.fromkeys(cand["day"]))
        if len(days) > max_days:
            print(f"{len(days)} candidate days; fetching the first {max_days}. "
                  f"Use --max-days or --days to choose. Remaining: {', '.join(days[max_days:])}")
            days = days[:max_days]
    ais = identities[identities["source"] == "AIS self-reported"]
    mmsis = set(ais["ssvid"].dropna().astype(str))
    imos = set(identities["imo"].dropna().astype(str))
    session = session or requests.Session()
    print(f"Fetching {len(days)} day(s), {parallel} at a time: {', '.join(days)}")

    def one(day):
        try:
            return fetch_day(day, mmsis, imos, dma_dir, keep_files, session)
        except FileNotFoundError as exc:
            print(f"  ! {exc}")
            return None

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        frames = [f for f in pool.map(one, days) if f is not None and not f.empty]
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
