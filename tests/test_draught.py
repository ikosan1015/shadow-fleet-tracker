import zipfile

import pandas as pd
from conftest import SANCTIONED

from sft import draught, flags

HEADER = ("# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,"
          "Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,"
          "Type of position fixing device,Draught,Destination,ETA,Data source type,A,B,C,D")


def row(ts, mmsi, lat, lon, draught_m, dest, imo="Unknown"):
    return (f"{ts},Class A,{mmsi},{lat},{lon},Under way using engine,0,12.0,0,0,{imo},X,NAME,"
            f"Tanker,,40,240,GPS,{draught_m},{dest},,AIS,1,1,1,1")


def write_day(dma_dir, day, rows):
    dma_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dma_dir / f"aisdk-{day}.zip", "w") as z:
        z.writestr(f"aisdk-{day}.csv", "\n".join([HEADER] + rows) + "\n")


def identities():
    return pd.DataFrame({"imo": [SANCTIONED], "vessel_id": ["v1"], "shipname": ["TOY"],
                         "flag": ["XX"], "ssvid": ["111"], "source": ["AIS self-reported"]})


def test_suggest_days_from_primorsk_call(events):
    s = draught.suggest_days(events)
    # toy tanker left Primorsk on 2025-01-02 -> outbound transit days 03..06
    days = set(s[s["imo"] == SANCTIONED]["day"])
    assert {"2025-01-03", "2025-01-06"} <= days
    assert s["reason"].str.contains("outbound transit after PRIMORSK").any()


def test_full_draught_run_flags_unload_at_sea(tmp_path, events):
    dma = tmp_path / "dma"
    # leaves the Baltic laden (14.8 m), comes back light (8.1 m), only loitering in between
    write_day(dma, "2025-01-03", [
        row("03/01/2025 06:00:00", 111, 55.0, 11.0, 14.8, "SUEZ"),
        row("03/01/2025 18:00:00", 111, 57.6, 10.6, 14.8, "SUEZ"),
        row("03/01/2025 06:00:00", 999, 56.0, 11.0, 9.0, "AARHUS"),   # someone else
    ])
    write_day(dma, "2025-01-10", [
        row("10/01/2025 06:00:00", 111, 57.6, 10.6, 8.1, "PRIMORSK"),
        row("10/01/2025 18:00:00", 111, 55.0, 11.0, 8.1, "PRIMORSK"),
    ])
    pas, fl = draught.run(events, identities(), days=["2025-01-03", "2025-01-10"],
                          dma_dir=dma, out=tmp_path / "out")
    assert list(pas["draught_m"]) == [14.8, 8.1]
    assert list(pas["direction_hint"]) == ["leaving Baltic", "entering Baltic"]
    assert list(fl["rule"]) == ["unloaded_without_port_call"]
    # the big files are deleted after filtering, and the cache is reused
    assert not (dma / "aisdk-2025-01-03.zip").exists()
    pas2, _ = draught.run(events, identities(), days=["2025-01-03"], dma_dir=dma,
                          out=tmp_path / "out")
    assert len(pas2) == 2   # merged with the earlier run


def test_port_call_in_between_explains_the_change(events):
    pas = pd.DataFrame({
        "imo": [SANCTIONED] * 2, "day": ["2024-12-20", "2025-01-02"],
        "first_seen": pd.to_datetime(["2024-12-20T00:00Z", "2025-01-02T12:00Z"]),
        "last_seen": pd.to_datetime(["2024-12-20T12:00Z", "2025-01-02T13:00Z"]),
        "draught_m": [8.0, 14.5], "lat": [56, 56], "lon": [11, 11]})
    # the 24 h Primorsk call (Jan 1-2) is a loading port -> no "loaded_without_port_call"
    assert draught.flags(pas, events).empty


def test_draught_flags_reach_the_flag_table(events, vessels):
    df = pd.DataFrame([{"imo": SANCTIONED, "event_id": "draught-x", "rule": "unloaded_without_port_call",
                        "start": pd.Timestamp("2025-01-04", tz="UTC"), "lat": 56, "lon": 11,
                        "detail": "x"}])
    fl = flags.apply_all(events, vessels, draught_flags=df)
    assert "unloaded_without_port_call" in set(fl["rule"])
