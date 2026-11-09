"""Case file + dashboard on the toy data: they build, say the right things, and survive gaps."""

import pandas as pd
from conftest import SANCTIONED
from test_partners import FakeClient, identities

from sft import casefile, dashboard, flags, partners


def build_all(tmp_path, events, vessels):
    p = partners.run(events, identities(), vessels, client=FakeClient(),
                     raw_root=tmp_path / "raw", out=tmp_path / "out")
    imos, names, _ = flags.watch_sets(vessels, identities())
    p = flags.annotate_partners(p, imos, names)
    fl = flags.apply_all(events, vessels, identities(), partners=p)
    sc = pd.DataFrame([{"role": "studied (sanctioned)", "name": "TOY TANKER", "imo": SANCTIONED,
                        "os_risk": "sanction; mare.shadow", "os_datasets": "eu_journal_sanctions",
                        "os_caption": "Toy", "os_url": "https://example.org/toy", "listed": True,
                        "shadow_tag": True, "detained": False}])
    cases = casefile.run(vessels, events, identities(), fl, partners=p, trips=None,
                         sanctions=sc, sanctions_file="maritime-test.csv", out=tmp_path / "cases")
    site = dashboard.build(vessels, events, fl, p, sc, cases, out=tmp_path / "site")
    return cases, site


def test_case_file_and_dashboard(tmp_path, events, vessels):
    cases, site = build_all(tmp_path, events, vessels)
    text = cases[SANCTIONED].read_text()
    for heading in ("## Summary", "## Identity history", "## Port calls",
                    "## Meetings at sea", "## Evidence ladder", "## Data issues", "## Sources"):
        assert heading in text
    assert "M/T Dark Star" in text and "nan" not in text.lower().replace("nanc", "")
    assert "browser.dataspace.copernicus.eu" in text
    idx = (site / "index.html").read_text()
    assert "TOY TANKER" in idx and f"case-{SANCTIONED}.html" in idx
    case_html = (site / f"case-{SANCTIONED}.html").read_text()
    assert "Activity timeline" in case_html and "<table>" in case_html
    assert "type='checkbox' checked" in case_html
    assert (site / f"{SANCTIONED}.html").exists()


def test_case_file_survives_missing_dates(tmp_path, events, vessels):
    ev = events.copy()
    ev.loc[ev.index[0], "start"] = pd.NaT
    fl = flags.apply_all(ev, vessels)
    cases = casefile.run(vessels, ev, identities(), fl, out=tmp_path / "cases")
    assert cases[SANCTIONED].exists()
