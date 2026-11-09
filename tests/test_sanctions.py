import json

import pandas as pd

from sft import flags, sanctions
from sft.flags import Rules

MARITIME_CSV = """type,caption,imo,risk,countries,flag,mmsi,id,url,datasets,aliases
VESSEL,Atlas,9413573,sanction,om;ru,om,,NK-atlas,https://www.opensanctions.org/entities/NK-atlas/,eu_journal_sanctions;gb_fcdo_sanctions,Arcadia
VESSEL,Harmless Trader,9000001,,,pa,,NK-ok,https://x,,
VESSEL,Beks Bebek,,sanction.linked,,mh,,NK-bb,https://y,ua_war_sanctions,
ORGANIZATION,Moonlight Shipmanagement,,sanction,ae,,,NK-co,https://z,eu_fsf,
"""


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text
        self.content = text.encode()

    def json(self):
        return self._p


class FakeOS:
    def __init__(self):
        self.posts = []

    def get(self, url, headers=None, timeout=None):
        if url.endswith("index.json"):
            return Resp(200, {"resources": [{"name": "maritime.csv",
                                             "url": "https://data.example/maritime.csv"}]})
        assert url == "https://data.example/maritime.csv"
        return Resp(200, text=MARITIME_CSV)

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        self.posts.append((url, json, headers))
        return Resp(200, {"responses": {qid: {"results": [
            {"id": "NK-api", "caption": "API HIT", "score": 0.9, "match": True,
             "datasets": ["eu_journal_sanctions"], "properties": {"topics": ["sanction"]}}]}
            for qid in json["queries"]}})


def partners_df():
    base = {"imo": "1111111", "start": pd.Timestamp("2025-03-25", tz="UTC"), "end": pd.NaT,
            "lat": 31.7, "lon": 32.3, "duration_h": 100.0, "overlap_h": 10.0}
    rows = [
        # crowded queue: 6 ships, one of them ARCADIA (= listed Atlas) at 2.7 km
        *[{**base, "event_id": "q", "partner_vessel_id": f"v{i}", "partner_name": f"SHIP {i}",
           "partner_flag": "COM", "partner_imo": f"900000{i}", "dist_km": 3.0} for i in range(5)],
        {**base, "event_id": "q", "partner_vessel_id": "arc", "partner_name": "ARCADIA",
         "partner_flag": "OMN", "partner_imo": "9413573", "dist_km": 2.7},
        # quiet spot, one partner 40 m away
        {**base, "event_id": "a", "partner_vessel_id": "bb", "partner_name": "BEKS BEBEK",
         "partner_flag": "MHL", "partner_imo": None, "dist_km": 0.04},
        # crowded, nobody listed or alongside -> no flag
        *[{**base, "event_id": "c", "partner_vessel_id": f"w{i}", "partner_name": f"Q {i}",
           "partner_flag": "PAN", "partner_imo": f"800000{i}", "dist_km": 4.0} for i in range(6)],
    ]
    return pd.DataFrame(rows)


def test_alongside_and_crowding_rules():
    p = flags.annotate_partners(partners_df(), imos={"9413573"}, names=set())
    fl = flags.colocated_loitering(p, Rules()).set_index("event_id")
    assert fl.loc["a", "rule"] == "alongside_meeting"
    assert fl.loc["q", "rule"] == "listed_nearby_in_anchorage"
    assert "crowded area, 6 ships" in fl.loc["q", "detail"]
    assert "c" not in fl.index


def test_update_builds_watchlist_and_flags_use_it(tmp_path, monkeypatch, events, vessels):
    monkeypatch.setattr(sanctions, "get_optional", lambda name: None)
    wl = sanctions.update(session=FakeOS(), out_dir=tmp_path / "os",
                          watchlist_path=tmp_path / "os" / "watchlist.csv")
    assert set(wl["name"]) == {"Atlas"}            # direct listings only, not 'sanction.linked'
    # point the flag rules at this watchlist
    import sft.config as cfg
    monkeypatch.setattr(cfg, "OS_WATCHLIST", tmp_path / "os" / "watchlist.csv")
    imos, names, _ = flags.watch_sets(vessels)
    assert "9413573" in imos and "ARCADIA" in names          # alias of Atlas
    p = flags.annotate_partners(partners_df(), imos, names)
    fl = flags.colocated_loitering(p).set_index("event_id")
    assert fl.loc["q", "rule"] == "listed_nearby_in_anchorage"
    # a manual watchlist entry upgrades the alongside meeting
    wl_manual = tmp_path / "manual.csv"
    wl_manual.write_text("imo,name,list\n,BEKS BEBEK,manual\n")
    monkeypatch.setattr(cfg, "WATCHLIST_CSV", wl_manual)
    imos, names, _ = flags.watch_sets(vessels)
    fl = flags.colocated_loitering(flags.annotate_partners(partners_df(), imos, names))
    assert fl.set_index("event_id").loc["a", "rule"] == "alongside_listed"


def test_check_bulk_then_api(tmp_path, vessels):
    path = tmp_path / "m.csv"
    path.write_text(MARITIME_CSV)
    m = sanctions.load_maritime(path)
    fake = FakeOS()
    out = sanctions.check(vessels, partners_df(), m, key="k", session=fake)
    arc = out[out["name"] == "ARCADIA"].iloc[0]
    assert arc["matched_by"] == "imo (bulk file)" and bool(arc["listed"])
    bb = out[out["name"] == "BEKS BEBEK"].iloc[0]
    assert bb["matched_by"].startswith("name only") and not bool(bb["listed"])  # linked only
    # unmatched ships went to the API with the key in the header
    url, body, headers = fake.posts[0]
    assert url.endswith("/match/default") and headers["Authorization"] == "ApiKey k"
    q = next(iter(body["queries"].values()))
    assert q["schema"] == "Vessel"
    assert (out["matched_by"] == "API match").any()


def test_find_csv_url_nested():
    idx = {"a": [{"b": {"url": "https://x/other.json"}}, {"url": "https://x/maritime.csv"}]}
    assert sanctions._find_csv_url(json.loads(json.dumps(idx))) == "https://x/maritime.csv"


def test_several_records_for_one_imo_are_combined(vessels):
    csv = ("type,caption,imo,risk,countries,flag,mmsi,id,url,datasets,aliases\n"
           "VESSEL,MT EAGLE S,9329760,mare.detained;reg.warn,,ck,,a,https://a,detentions,\n"
           "VESSEL,Eagle S,9329760,sanction,,ck,,b,https://b,eu_journal_sanctions,EAGLE-S\n")
    import io
    m = sanctions.load_maritime(io.StringIO(csv))
    out = sanctions.check(vessels.assign(imo=["9329760", "2222222", "3333333"]), None, m)
    row = out[out["imo"] == "9329760"].iloc[0]
    assert bool(row["listed"]) and bool(row["detained"])
    assert "eu_journal_sanctions" in row["os_datasets"] and "detentions" in row["os_datasets"]
