"""OpenSanctions: who is listed?

Two routes, both free for non-commercial use (data licence CC BY-NC 4.0, so
credit "OpenSanctions" in the report):

1. Bulk maritime file (no key needed). A daily CSV of ~vessels and shipping
   companies with IMO, flag, risk and source lists. `update()` downloads it and
   builds data/raw/opensanctions/watchlist.csv, which the flag rules then use.
2. Matching API (optional key in .env as OPENSANCTIONS_API_KEY). Used by `check()`
   for ships the bulk file did not match by IMO, e.g. a partner with no IMO.

`check()` writes data/processed/sanctions_check.csv: every studied vessel and
every partner found by `sft partners`, with what OpenSanctions says about it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .config import (
    OS_API,
    OS_DIR,
    OS_MARITIME_FALLBACK,
    OS_MARITIME_INDEX,
    OS_WATCHLIST,
    PROCESSED,
    get_optional,
)
from .flags import norm_name

UA = {"User-Agent": "shadow-fleet-tracker (non-commercial research)"}


def _headers(key: str | None) -> dict:
    return {**UA, **({"Authorization": f"ApiKey {key}"} if key else {})}


def clean_imo(x) -> str:
    digits = re.sub(r"\D", "", str(x or ""))
    return digits if len(digits) == 7 else ""


# ------------------------------------------------------------- bulk download
def _find_csv_url(index: dict) -> str | None:
    """index.json lists the dataset's files; find the maritime CSV wherever it is."""
    stack = [index]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            url = cur.get("url")
            if isinstance(url, str) and url.endswith("maritime.csv"):
                return url
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def download_maritime(session: requests.Session | None = None, key: str | None = None,
                      out_dir: Path = OS_DIR) -> Path:
    s = session or requests.Session()
    url = OS_MARITIME_FALLBACK
    try:
        r = s.get(OS_MARITIME_INDEX, headers=_headers(key), timeout=60)
        if r.status_code == 200:
            url = _find_csv_url(r.json()) or url
    except (requests.RequestException, ValueError):
        pass
    print(f"Downloading {url}")
    r = s.get(url, headers=_headers(key), timeout=300)
    if r.status_code != 200:
        raise RuntimeError(
            f"OpenSanctions download failed: HTTP {r.status_code} {r.text[:200]}\n"
            "Download maritime.csv by hand from https://www.opensanctions.org/datasets/maritime/ "
            "and run: uv run sft sanctions update --file <path-to-maritime.csv>")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"maritime-{date.today().isoformat()}.csv"
    path.write_bytes(r.content)
    return path


def load_maritime(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna("")
    df.columns = [c.strip().lower() for c in df.columns]
    if "type" in df:
        df = df[df["type"].str.upper() == "VESSEL"]
    df = df.copy()
    df["imo"] = df["imo"].map(clean_imo) if "imo" in df else ""
    for col in ("caption", "aliases", "risk", "flag", "datasets", "url"):
        if col not in df:
            df[col] = ""
    return df.reset_index(drop=True)


def is_direct_sanction(risk: str) -> bool:
    """True for a direct listing ('sanction'), False for 'sanction.linked' / secondary mentions."""
    tokens = [t.strip().lower() for t in re.split(r"[;,|]", str(risk or "")) if t.strip()]
    return any(t.startswith("sanction") and not any(w in t for w in ("linked", "secondary"))
               for t in tokens)


def build_watchlist(maritime: pd.DataFrame) -> pd.DataFrame:
    """Vessels OpenSanctions marks as DIRECTLY sanctioned -> watchlist format used by flags.py.
    Ships only linked to sanctioned parties stay out of the watchlist but still show
    their risk label in `sft sanctions check`."""
    listed = maritime[maritime["risk"].map(is_direct_sanction)]
    if listed.empty:
        print("! no direct-sanction values in the risk column; check the file format.")
    return pd.DataFrame({
        "imo": listed["imo"],
        "name": listed.get("caption", ""),
        "aliases": listed.get("aliases", ""),
        "list": "OpenSanctions:" + listed.get("risk", ""),
        "flag": listed.get("flag", ""),
        "datasets": listed.get("datasets", ""),
        "url": listed.get("url", ""),
    }).reset_index(drop=True)


def latest_maritime(out_dir: Path = OS_DIR) -> Path | None:
    files = sorted(out_dir.glob("maritime-*.csv"))
    return files[-1] if files else None


def update(session: requests.Session | None = None, out_dir: Path = OS_DIR,
           watchlist_path: Path = OS_WATCHLIST, file: Path | None = None) -> pd.DataFrame:
    """Download (or import a hand-downloaded) maritime.csv and rebuild the watchlist."""
    if file:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"maritime-{date.today().isoformat()}.csv"
        path.write_bytes(Path(file).read_bytes())
    else:
        path = download_maritime(session, get_optional("OPENSANCTIONS_API_KEY"), out_dir)
    m = load_maritime(path)
    wl = build_watchlist(m)
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    wl.to_csv(watchlist_path, index=False)
    if "risk" in m:
        print("Vessels by risk label:\n" + m["risk"].value_counts().to_string())
    print(f"{len(m):,} vessels in file; {len(wl):,} sanctioned -> {watchlist_path}")
    return wl


# -------------------------------------------------------------------- API
def api_match(ships: list[dict], key: str, session: requests.Session | None = None,
              threshold: float = 0.8, batch: int = 20) -> dict[str, dict]:
    """POST /match/default for vessels. ships: [{"qid", "name", "imo"}]. Returns best hit per qid."""
    s = session or requests.Session()
    out: dict[str, dict] = {}
    for i in range(0, len(ships), batch):
        queries = {}
        for sh in ships[i:i + batch]:
            props = {"name": [sh["name"]]} if sh.get("name") else {}
            if sh.get("imo"):
                props["imoNumber"] = [sh["imo"]]
            if props:
                queries[sh["qid"]] = {"schema": "Vessel", "properties": props}
        if not queries:
            continue
        r = s.post(f"{OS_API}/match/default", params={"algorithm": "logic-v2",
                   "threshold": threshold}, json={"queries": queries},
                   headers=_headers(key), timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"OpenSanctions API: HTTP {r.status_code} {r.text[:200]}")
        for qid, resp in (r.json().get("responses") or {}).items():
            hits = [h for h in resp.get("results") or [] if h.get("match")]
            if hits:
                h = max(hits, key=lambda x: x.get("score", 0))
                props = h.get("properties") or {}
                out[qid] = {"os_caption": h.get("caption"), "os_score": h.get("score"),
                            "os_risk": ";".join(props.get("topics", []) or []),
                            "os_datasets": ";".join(h.get("datasets", []) or []),
                            "os_url": f"https://www.opensanctions.org/entities/{h.get('id')}/"}
    return out


# ------------------------------------------------------------------- check
def ships_to_check(vessels: pd.DataFrame, partners: pd.DataFrame | None) -> pd.DataFrame:
    rows = [{"role": f"studied ({g})", "name": n, "imo": clean_imo(i), "flag": ""}
            for n, i, g in zip(vessels["name"], vessels["imo"], vessels["group"])]
    if partners is not None and not partners.empty:
        u = partners.drop_duplicates("partner_vessel_id")
        rows += [{"role": "partner", "name": n, "imo": clean_imo(i), "flag": f}
                 for n, i, f in zip(u["partner_name"], u["partner_imo"], u["partner_flag"])]
    df = pd.DataFrame(rows).fillna("")
    return df.drop_duplicates(["role", "name", "imo"]).reset_index(drop=True)


def _join_unique(values, sep="; ") -> str:
    out: list[str] = []
    for v in values:
        for part in re.split(r"[;]", str(v or "")):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return sep.join(out)


def merge_by_imo(maritime: pd.DataFrame) -> pd.DataFrame:
    """One row per IMO. The file can hold SEVERAL records for one ship (e.g. one from a
    sanctions list, one from a detention database), so labels are combined, not dropped."""
    m = maritime[maritime["imo"] != ""]
    agg = {c: (lambda s: _join_unique(s)) for c in ("caption", "risk", "flag", "datasets", "aliases")}
    agg["url"] = lambda s: _join_unique(s, sep=" ")
    return m.groupby("imo").agg(agg)


def check(vessels: pd.DataFrame, partners: pd.DataFrame | None, maritime: pd.DataFrame,
          key: str | None = None, session: requests.Session | None = None) -> pd.DataFrame:
    ships = ships_to_check(vessels, partners)
    by_imo = merge_by_imo(maritime)
    names: dict[str, int] = {}
    for idx, (cap, al) in enumerate(zip(maritime.get("caption", ""), maritime.get("aliases", ""))):
        for n in [cap] + str(al).split(";"):
            if norm_name(n):
                names.setdefault(norm_name(n), idx)

    def row_info(r: pd.Series, how: str) -> dict:
        return {"matched_by": how, "os_caption": r.get("caption"), "os_risk": r.get("risk"),
                "os_flag": r.get("flag"), "os_datasets": r.get("datasets"), "os_url": r.get("url")}

    results = []
    for _, sh in ships.iterrows():
        info = {"matched_by": "none"}
        if sh["imo"] and sh["imo"] in by_imo.index:
            info = row_info(by_imo.loc[sh["imo"]], "imo (bulk file)")
        elif not sh["imo"] and norm_name(sh["name"]) in names:
            # name-only matches are weak: many ships share names. Verify by hand.
            info = row_info(maritime.iloc[names[norm_name(sh["name"])]], "name only (bulk file) - VERIFY")
        results.append({**sh.to_dict(), **info})
    out = pd.DataFrame(results)

    if key:
        todo = out[out["matched_by"] == "none"]
        hits = api_match([{"qid": str(i), "name": r["name"], "imo": r["imo"]}
                          for i, r in todo.iterrows()], key, session)
        for qid, h in hits.items():
            for col, val in h.items():
                out.loc[int(qid), col] = val
            out.loc[int(qid), "matched_by"] = "API match"
    risk = out["os_risk"].fillna("")
    out["listed"] = risk.map(is_direct_sanction)
    out["shadow_tag"] = risk.str.contains("mare.shadow", regex=False)
    out["detained"] = risk.str.contains("mare.detained", regex=False)
    return out


def run_check(vessels: pd.DataFrame, partners: pd.DataFrame | None, out: Path = PROCESSED,
              session: requests.Session | None = None) -> pd.DataFrame:
    path = latest_maritime()
    if path is None:
        raise FileNotFoundError("No OpenSanctions file yet. Run `sft sanctions update` first.")
    key = get_optional("OPENSANCTIONS_API_KEY")
    if not key:
        print("(no OPENSANCTIONS_API_KEY in .env: bulk-file matching only)")
    res = check(vessels, partners, load_maritime(path), key, session)
    out.mkdir(parents=True, exist_ok=True)
    res.to_csv(out / "sanctions_check.csv", index=False)
    show = ["role", "name", "imo", "flag", "listed", "shadow_tag", "detained", "matched_by",
            "os_caption", "os_risk"]
    print(res[[c for c in show if c in res]].to_string(index=False))
    print("\nlisted = directly sanctioned TODAY (not necessarily at the time of a meeting).\n"
          "shadow_tag = OpenSanctions 'mare.shadow' tag; detained = port-state detention record.")
    print(f"\nSource file: {path.name} (OpenSanctions, CC BY-NC 4.0)")
    return res
