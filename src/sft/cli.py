"""Command line.

  sft fetch | clean | partners | follow | sanctions update|check | flags | casefile | maps
  sft report   = flags + casefile + maps (no API calls)
  sft all      = fetch + clean + partners + follow + report
  sft draught  = optional, parked (large Danish AIS downloads)
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from . import clean, fetch, flags
from .config import DEFAULT_END, DEFAULT_START, PROCESSED


def _load_processed():
    ev = pd.read_parquet(PROCESSED / "events.parquet")
    idp = PROCESSED / "identities.parquet"
    ids = pd.read_parquet(idp) if idp.exists() else pd.DataFrame()
    return ev, ids


def _optional(name: str) -> pd.DataFrame | None:
    p = PROCESSED / name
    return pd.read_parquet(p) if p.exists() else None


def cmd_partners(args=None):
    from . import partners
    ev, ids = _load_processed()
    partners.run(ev, ids, fetch.load_vessels())


def cmd_draught(args):
    from . import draught
    ev, ids = _load_processed()
    if args.suggest:
        print(draught.suggest_days(ev).to_string(index=False))
        return
    if args.locate:
        print(draught.choose_days(ev, ids, start=args.start, end=args.end).to_string(index=False))
        return
    draught.run(ev, ids, days=args.days, max_days=args.max_days, keep_files=args.keep_files,
                start=args.start, end=args.end, parallel=args.parallel)


def cmd_sanctions(args):
    from . import sanctions
    if args.action == "update":
        sanctions.update(file=args.file)
    else:
        sanctions.run_check(fetch.load_vessels(), _optional("partners.parquet"))


def cmd_flags(_args=None):
    ev, ids = _load_processed()
    vessels = fetch.load_vessels()
    fl = flags.apply_all(ev, vessels, ids, partners=_optional("partners.parquet"),
                         draught_flags=_optional("draught_flags.parquet"))
    summary = flags.summarize(fl, ev, vessels)
    fl.to_parquet(PROCESSED / "flags.parquet", index=False)
    summary.to_csv(PROCESSED / "flag_summary.csv", index=False)
    (PROCESSED / "rules_used.json").write_text(json.dumps(flags.rules_dict(), indent=2))
    print(f"{len(fl)} flags")
    print(summary.to_string(index=False))


def _annotated_partners(vessels, ids):
    imos, names, _ = flags.watch_sets(vessels, ids)
    return flags.annotate_partners(_optional("partners.parquet"), imos, names)


def _sanctions_table():
    p = PROCESSED / "sanctions_check.csv"
    return pd.read_csv(p, dtype={"imo": str}) if p.exists() else None


def cmd_follow(_args=None):
    from . import follow
    ev, ids = _load_processed()
    follow.run(_annotated_partners(fetch.load_vessels(), ids))


def cmd_casefile(_args=None):
    from . import casefile
    from .sanctions import latest_maritime
    ev, ids = _load_processed()
    vessels = fetch.load_vessels()
    trips = PROCESSED / "partner_trips.csv"
    osf = latest_maritime()
    return casefile.run(
        vessels, ev, ids, pd.read_parquet(PROCESSED / "flags.parquet"),
        partners=_annotated_partners(vessels, ids),
        trips=pd.read_csv(trips, dtype={"imo": str}, parse_dates=["meeting_start", "meeting_end"])
        if trips.exists() else None,
        sanctions=_sanctions_table(), sanctions_file=osf.name if osf else None)


def cmd_maps(_args=None):
    from . import dashboard
    ev, ids = _load_processed()
    vessels = fetch.load_vessels()
    fl = pd.read_parquet(PROCESSED / "flags.parquet")
    summary = pd.read_csv(PROCESSED / "flag_summary.csv", dtype={"imo": str})
    cases = {}
    from .casefile import CASES, slug
    for _, v in vessels.iterrows():
        p = CASES / f"{v['imo']}-{slug(v['name'])}.md"
        if p.exists():
            cases[v["imo"]] = p
    dashboard.build(vessels, ev, fl, _annotated_partners(vessels, ids), _sanctions_table(),
                    cases, summary)


def main(argv=None):
    p = argparse.ArgumentParser(prog="sft", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download identities + events from GFW")
    f.add_argument("--start", default=DEFAULT_START)
    f.add_argument("--end", default=DEFAULT_END)
    sub.add_parser("clean", help="raw JSON -> parquet")
    sub.add_parser("partners", help="find other ships loitering at the same place and time")
    sub.add_parser("follow", help="port calls of alongside/listed partners around each meeting")
    d = sub.add_parser("draught", help="draught snapshots from Danish AIS (large downloads)")
    d.add_argument("--suggest", action="store_true", help="only list rough candidate days")
    d.add_argument("--locate", action="store_true",
                   help="only list the exact days GFW saw the ship in Danish waters")
    d.add_argument("--start", help="limit the search, e.g. 2025-03-01")
    d.add_argument("--end", help="limit the search, e.g. 2025-03-15")
    d.add_argument("--parallel", type=int, default=2, help="days downloaded at once (default 2)")
    d.add_argument("--days", nargs="+", help="specific days, e.g. 2024-07-04 2024-07-05")
    d.add_argument("--max-days", type=int, default=6, help="cap on days fetched (default 6)")
    d.add_argument("--keep-files", action="store_true", help="keep the big DMA files")
    sc = sub.add_parser("sanctions", help="OpenSanctions: update watchlist / check ships")
    sc.add_argument("action", choices=["update", "check"],
                    help="update = download vessel list; check = look up studied ships + partners")
    sc.add_argument("--file", help="use a maritime.csv you downloaded by hand (update only)")
    sub.add_parser("flags", help="apply anomaly rules")
    sub.add_parser("casefile", help="write report/cases/<imo>-<name>.md per vessel")
    sub.add_parser("maps", help="build the dashboard (maps, case pages, index)")
    sub.add_parser("report", help="flags + casefile + maps, no API calls")
    a = sub.add_parser("all", help="fetch + clean + partners + follow + report (not draught)")
    a.add_argument("--start", default=DEFAULT_START)
    a.add_argument("--end", default=DEFAULT_END)
    args = p.parse_args(argv)

    if args.cmd in ("fetch", "all"):
        fetch.run(args.start, args.end)
    if args.cmd in ("clean", "all"):
        clean.run()
    if args.cmd in ("partners", "all"):
        cmd_partners(args)
    if args.cmd in ("follow", "all"):
        cmd_follow(args)
    if args.cmd == "draught":
        cmd_draught(args)
    if args.cmd == "sanctions":
        cmd_sanctions(args)
    if args.cmd in ("flags", "report", "all"):
        cmd_flags()
    if args.cmd in ("casefile", "report", "all"):
        cmd_casefile()
    if args.cmd in ("maps", "report", "all"):
        cmd_maps()


if __name__ == "__main__":
    main()
