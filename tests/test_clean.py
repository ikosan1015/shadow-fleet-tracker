from sft.clean import parse_identity
from sft.gfw import vessel_ids_for_imo

SEARCH = {"entries": [
    {"selfReportedInfo": [
        {"id": "v1", "ssvid": "111", "shipname": "OLD NAME", "flag": "PAN", "imo": "1111111",
         "transmissionDateFrom": "2022-01-01T00:00:00Z", "transmissionDateTo": "2024-01-01T00:00:00Z"},
        {"id": "v2", "ssvid": "222", "shipname": "NEW NAME", "flag": "CMR", "imo": "1111111",
         "transmissionDateFrom": "2024-02-01T00:00:00Z", "transmissionDateTo": "2026-01-01T00:00:00Z"},
    ], "registryInfo": []},
    {"selfReportedInfo": [{"id": "other", "imo": "9999999", "shipname": "UNRELATED"}],
     "registryInfo": []},
]}


def test_vessel_ids_collects_all_ids_for_imo():
    assert vessel_ids_for_imo(SEARCH, "1111111") == ["v1", "v2"]


def test_parse_identity_skips_other_ships():
    df = parse_identity(SEARCH, "1111111")
    assert list(df["shipname"]) == ["OLD NAME", "NEW NAME"]


def test_events_frame_types_and_durations(events):
    assert set(events["type"]) == {"port_visit", "gap", "encounter", "loitering"}
    g1 = events.set_index("event_id").loc["g1"]
    assert g1["duration_h"] == 30
    assert g1["gap_intentional"] is True or g1["gap_intentional"] == True  # noqa: E712
    assert events.set_index("event_id").loc["p1", "port_name"] == "PRIMORSK"
