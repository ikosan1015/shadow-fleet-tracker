import pandas as pd

from sft import follow


def pv(id_, start, end, name, flag):
    return {"id": id_, "type": "port_visit", "start": start, "end": end,
            "position": {"lat": 0, "lon": 0},
            "port_visit": {"intermediateAnchorage": {"name": name, "flag": flag}}}


class Client:
    def __init__(self):
        self.calls = []

    def events(self, vids, dataset, start, end):
        self.calls.append((tuple(vids), dataset, start, end))
        return [pv("a", "2024-01-01T00:00Z", "2024-01-03T00:00Z", "PRIMORSK", "RUS"),
                pv("b", "2024-01-20T00:00Z", "2024-01-22T00:00Z", "SIKKA", "IND"),
                pv("c", "2024-02-20T00:00Z", "2024-02-21T00:00Z", "FUJAIRAH", "ARE")]


def partners():
    base = {"imo": "1111111", "lat": 36.1, "lon": 14.9, "duration_h": 14.0, "overlap_h": 14.0,
            "start": pd.Timestamp("2024-01-11", tz="UTC"), "end": pd.Timestamp("2024-01-12", tz="UTC")}
    return pd.DataFrame([
        {**base, "event_id": "m1", "partner_vessel_id": "p1", "partner_name": "BEKS BEBEK",
         "partner_flag": "MHL", "partner_imo": None, "dist_km": 0.04, "partner_listed": False,
         "alongside": True, "crowded": False},
        {**base, "event_id": "m2", "partner_vessel_id": "p2", "partner_name": "NOBODY",
         "partner_flag": "PAN", "partner_imo": "9000000", "dist_km": 4.0, "partner_listed": False,
         "alongside": False, "crowded": False},
    ])


def test_follows_only_alongside_or_listed_and_reports_ports(tmp_path):
    c = Client()
    df = follow.run(partners(), client=c, raw_root=tmp_path / "raw", out=tmp_path / "out")
    assert [x[0] for x in c.calls] == [("p1",)]          # NOBODY (4 km, unlisted) not followed
    r = df.iloc[0]
    assert r["prev_port"] == "PRIMORSK" and bool(r["from_russia"])
    assert r["next_port"] == "SIKKA" and r["days_to_next"] == 8.0
    assert r["next_ports"].startswith("SIKKA (IND) 2024-01-20; FUJAIRAH (ARE)")
    assert not bool(r["back_to_russia"])
    # cached on the second run
    follow.run(partners(), client=c, raw_root=tmp_path / "raw", out=tmp_path / "out")
    assert len(c.calls) == 1
