"""Command line: `sft fetch | clean | partners | draught | flags | maps | all`."""

from __future__ import annotations

import argparse
import json

import pandas as pd

from . import clean, fetch, flags, maps
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
    draught.run(ev, ids, days=args.days, max_days=args.max_days, keep_files=args.keep_files)


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


def cmd_maps(_args=None):
    ev, _ = _load_processed()
    fl = pd.read_parquet(PROCESSED / "flags.parquet")
    summary = pd.read_csv(PROCESSED / "flag_summary.csv", dtype={"imo": str})
    maps.run(ev, fl, fetch.load_vessels(), summary, partners=_optional("partners.parquet"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="sft", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download identities + events from GFW")
    f.add_argument("--start", default=DEFAULT_START)
    f.add_argument("--end", default=DEFAULT_END)
    sub.add_parser("clean", help="raw JSON -> parquet")
    sub.add_parser("partners", help="find other ships loitering at the same place and time")
    d = sub.add_parser("draught", help="draught snapshots from Danish AIS (large downloads)")
    d.add_argument("--suggest", action="store_true", help="only list candidate days")
    d.add_argument("--days", nargs="+", help="specific days, e.g. 2024-07-04 2024-07-05")
    d.add_argument("--max-days", type=int, default=6, help="cap on days fetched (default 6)")
    d.add_argument("--keep-files", action="store_true", help="keep the big DMA files")
    sub.add_parser("flags", help="apply anomaly rules")
    sub.add_parser("maps", help="build HTML maps")
    a = sub.add_parser("all", help="fetch + clean + partners + flags + maps (not draught)")
    a.add_argument("--start", default=DEFAULT_START)
    a.add_argument("--end", default=DEFAULT_END)
    args = p.parse_args(argv)

    if args.cmd in ("fetch", "all"):
        fetch.run(args.start, args.end)
    if args.cmd in ("clean", "all"):
        clean.run()
    if args.cmd in ("partners", "all"):
        cmd_partners(args)
    if args.cmd == "draught":
        cmd_draught(args)
    if args.cmd in ("flags", "all"):
        cmd_flags()
    if args.cmd in ("maps", "all"):
        cmd_maps()


if __name__ == "__main__":
    main()
