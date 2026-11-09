import pandas as pd
from conftest import SANCTIONED

from sft import flags, partners
from sft.gfw import GFWClient


def loiter(id_, vid, name, start, end, lat, lon):
    return {"id": id_, "type": "loitering", "start": start, "end": end,
            "position": {"lat": lat, "lon": lon},
            "vessel": {"id": vid, "name": name, "ssvid": "613000000", "flag": "CM"}}


class FakeClient:
    """Answers the area query around our toy loitering event l1 (35.0N 20.0E, 00-10h)."""

    def __init__(self):
        self.area_calls = []

    def events_in_area(self, dataset, start, end, geometry):
        self.area_calls.append(geometry)
        return [
            loiter("own", "vid-l", "TOY", "2025-01-05T00:00:00Z", "2025-01-05T10:00:00Z", 35.0, 20.0),
            # overlaps 8 h, ~2 km away -> match
            loiter("hit", "p1", "M/T Dark Star", "2025-01-05T02:00:00Z", "2025-01-05T12:00:00Z",
                   35.015, 20.0),
            # ~20 km away -> no
            loiter("far", "p2", "FAR AWAY", "2025-01-05T00:00:00Z", "2025-01-05T10:00:00Z",
                   35.18, 20.0),
            # same spot but the next day -> no
            loiter("late", "p3", "LATE", "2025-01-06T00:00:00Z", "2025-01-06T10:00:00Z", 35.0, 20.0),
        ]

    def get_vessel(self, vid):
        return {"selfReportedInfo": [{"imo": "3333333", "shipname": "DARK STAR", "flag": "CMR"}],
                "combinedSourcesInfo": [{"shiptypes": [{"name": "BUNKER_OR_TANKER"}]}]}


def identities():
    return pd.DataFrame({"imo": [SANCTIONED], "vessel_id": ["vid-l"], "shipname": ["TOY"],
                         "flag": ["XX"], "ssvid": ["111"], "source": ["AIS self-reported"]})


def test_box_is_lon_lat_and_closed():
    ring = partners.box(35.0, 20.0, 10)["coordinates"][0]
    assert ring[0] == ring[-1]
    assert 19.8 < ring[0][0] < 20.0 and 34.9 < ring[0][1] < 35.0  # [lon, lat]


def test_finds_only_the_close_overlapping_partner(tmp_path, events, vessels):
    client = FakeClient()
    df = partners.run(events, identities(), vessels, client=client,
                      raw_root=tmp_path / "raw", out=tmp_path / "out")
    assert list(df["partner_name"]) == ["M/T Dark Star"]
    row = df.iloc[0]
    assert row["overlap_h"] == 8.0 and row["dist_km"] < 2
    assert row["partner_imo"] == "3333333" and row["partner_type"] == "BUNKER_OR_TANKER"
    assert bool(row["partner_listed"])          # DARK STAR is sanctioned in the toy vessels list
    # second run uses the cache: no new API calls
    partners.run(events, identities(), vessels, client=client,
                 raw_root=tmp_path / "raw", out=tmp_path / "out")
    assert len(client.area_calls) == 1


def test_partner_feeds_a_flag(tmp_path, events, vessels):
    df = partners.run(events, identities(), vessels, client=FakeClient(),
                      raw_root=tmp_path / "raw", out=tmp_path / "out")
    fl = flags.apply_all(events, vessels, identities(), partners=df)
    assert "colocated_with_listed" in set(fl["rule"])


class PostSession:
    def __init__(self):
        self.headers, self.bodies = {}, []

    def post(self, url, json, timeout):
        self.bodies.append((url, json))
        offset = int(url.split("offset=")[1].split("&")[0])

        class R:
            status_code, headers, text = 200, {}, ""

            def json(_):
                return {"entries": [{"id": f"e{offset}"}], "nextOffset": 1 if offset == 0 else None}
        return R()


def test_events_in_area_posts_geometry_and_paginates():
    s = PostSession()
    c = GFWClient("t", pause=0, session=s)
    out = c.events_in_area("ds", "2025-01-01", "2025-01-02", partners.box(35, 20, 5))
    assert [e["id"] for e in out] == ["e0", "e1"]
    url, body = s.bodies[0]
    assert "/events?" in url and body["geometry"]["type"] == "Polygon" and "vessels" not in body


def test_meeting_between_two_studied_ships_is_kept(tmp_path, events, vessels):
    """DARK STAR is also a studied ship here: its meeting with our toy tanker must still show."""
    ids = pd.concat([identities(), pd.DataFrame({
        "imo": ["3333333"], "vessel_id": ["p1"], "shipname": ["DARK STAR"], "flag": ["CMR"],
        "ssvid": ["613000000"], "source": ["AIS self-reported"]})], ignore_index=True)
    df = partners.run(events, ids, vessels, client=FakeClient(),
                      raw_root=tmp_path / "raw", out=tmp_path / "out")
    assert "M/T Dark Star" in set(df["partner_name"])
