from conftest import CONTROL, SANCTIONED
from test_clean import SEARCH

from sft import flags
from sft.clean import parse_identity
from sft.flags import Rules, norm_name
from sft.ownership import identity_changes


def rules_hit(events, vessels):
    fl = flags.apply_all(events, vessels)
    return fl, set(zip(fl["imo"], fl["rule"]))


def test_sanctioned_toy_trips_every_rule(events, vessels):
    _, hits = rules_hit(events, vessels)
    for rule in ("long_gap", "gap_near_hotspot", "gap_after_russian_terminal",
                 "encounter_with_listed", "open_sea_loitering"):
        assert (SANCTIONED, rule) in hits, rule


def test_control_short_gap_not_flagged(events, vessels):
    _, hits = rules_hit(events, vessels)
    assert not any(imo == CONTROL for imo, _ in hits)


def test_threshold_is_respected(events, vessels):
    fl = flags.long_gaps(events, Rules(long_gap_h=48))
    assert fl.empty


def test_norm_name():
    assert norm_name("M/T  Dark-Star ") == "DARK STAR"
    assert norm_name(None) == ""


def test_summary_rates(events, vessels):
    fl = flags.apply_all(events, vessels)
    s = flags.summarize(fl, events, vessels).set_index("imo")
    assert s.loc[SANCTIONED, "long_gap"] == 1
    assert s.loc[CONTROL, "days_observed"] >= 1


def test_identity_changes_detects_rename_and_reflag():
    ch = identity_changes(parse_identity(SEARCH, "1111111"))
    assert set(ch["field"]) == {"shipname", "flag", "ssvid"}
