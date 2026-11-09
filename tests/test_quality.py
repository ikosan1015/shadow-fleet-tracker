import pandas as pd

from sft import flags
from sft.flags import Rules


def ev(rows):
    df = pd.DataFrame(rows)
    for c in ("start", "end"):
        df[c] = pd.to_datetime(df[c], utc=True)
    df["duration_h"] = (df["end"] - df["start"]).dt.total_seconds() / 3600
    return df


def test_merged_port_visit_is_flagged_but_normal_long_stay_is_not():
    e = ev([
        # 105 days, starts at VISTINO, no end anchorage -> suspect (the Eagle S case)
        {"imo": "1", "event_id": "pv1", "type": "port_visit", "start": "2024-11-21", "end": "2025-03-07",
         "lat": 59.77, "lon": 28.42, "port_name": "VISTINO", "port_start_name": "VISTINO",
         "port_end_name": None},
        # 50 days in a shipyard, same anchorage in and out -> fine
        {"imo": "1", "event_id": "pv2", "type": "port_visit", "start": "2024-04-05", "end": "2024-05-26",
         "lat": 40.66, "lon": 29.27, "port_name": "YALOVA", "port_start_name": "YALOVA",
         "port_end_name": "YALOVA"},
    ])
    fl = flags.suspect_long_port_visits(e, Rules())
    assert list(fl["event_id"]) == ["pv1"]
    assert "106 days 'at VISTINO', end anchorage: none" in fl["detail"].iloc[0]


def test_silence_while_stationary_is_flagged_but_ocean_voyage_is_not():
    e = ev([
        {"imo": "1", "event_id": "a", "type": "port_visit", "start": "2025-06-12", "end": "2025-06-13",
         "lat": 31.26, "lon": 32.30, "port_name": "PORT SAID"},
        # 104 days later, same place -> stationary silence
        {"imo": "1", "event_id": "b", "type": "port_visit", "start": "2025-09-25", "end": "2025-09-26",
         "lat": 31.26, "lon": 32.30, "port_name": "PORT SAID"},
        # 15 days later, ~3,500 km away -> just a voyage
        {"imo": "1", "event_id": "c", "type": "port_visit", "start": "2025-10-11", "end": "2025-10-12",
         "lat": 57.7, "lon": 10.6, "port_name": "SKAGEN"},
    ])
    e.loc[1, ["lat", "lon"]] = [31.26, 32.30]
    e.loc[2, ["lat", "lon"]] = [22.0, 69.0]   # India
    e = e.sort_values("start")
    fl = flags.unexplained_silences(e, Rules())
    assert list(fl["event_id"]) == ["silence-a"]
    assert "with no events after port visit PORT SAID" in fl["detail"].iloc[0]


def test_every_rule_has_a_description():
    produced = {"long_gap", "gap_near_hotspot", "gap_after_russian_terminal", "encounter_with_listed",
                "open_sea_loitering", "alongside_listed", "alongside_meeting", "colocated_with_listed",
                "colocated_loitering", "listed_nearby_in_anchorage", "suspect_long_port_visit",
                "unexplained_silence", "unloaded_without_port_call", "loaded_without_port_call"}
    assert produced <= set(flags.RULE_INFO)
