import io
import json
import zipfile

import pandas as pd
from conftest import SANCTIONED
from test_draught import HEADER, identities, row

from sft import draught
from sft.gfw import GFWClient


def zip_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("aisdk-x.csv", text)
    return buf.getvalue()


def day_text(n_other=2000):
    lines = [HEADER]
    for i in range(n_other):
        lines.append(row("05/03/2025 01:00:00", 219000000 + i, 55.0, 11.0, 9.0, "AARHUS"))
        if i % 500 == 0:
            lines.append(row("05/03/2025 02:00:00", 111, 57.0, 10.6, 14.8, "SUEZ"))
    # a decoy whose DESTINATION contains our MMSI as text, and an IMO-only match
    lines.append(row("05/03/2025 03:00:00", 999, 55.0, 11.0, 9.0, '"X,111,Y"'))
    lines.append(row("05/03/2025 04:00:00", 555, 57.1, 10.7, 14.7, "SUEZ", imo="IMO1111111"))
    return "\n".join(lines) + "\n"


class Stream:
    def __init__(self, payload, chunk=777):
        self.payload, self.chunk, self.status_code = payload, chunk, 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size):
        for i in range(0, len(self.payload), self.chunk):  # odd chunk size on purpose
            yield self.payload[i:i + self.chunk]


class Session:
    def __init__(self, payload):
        self.payload, self.urls = payload, []

    def get(self, url, stream, timeout):
        self.urls.append(url)
        return Stream(self.payload)


def test_stream_matches_slow_reference(tmp_path):
    text = day_text()
    mmsis, imos = {"111"}, {"1111111"}
    fast = draught.stream_filter("2025-03-05", mmsis, imos, Session(zip_bytes(text)))
    ref = pd.read_csv(io.StringIO(text), dtype=str)
    ref.columns = [draught._norm(c) for c in ref.columns]
    want = ref[(ref["mmsi"] == "111") | (ref["imo"] == "IMO1111111")]
    assert len(fast) == len(want) == 5       # 4 by MMSI + 1 by IMO; decoy rejected
    assert set(fast["mmsi"]) == {"111", "555"}
    assert fast["draught"].max() == 14.8


def test_line_filter_handles_header_split_across_chunks():
    lf = draught.LineFilter([b",111,"])
    data = (HEADER + "\n" + row("05/03/2025 02:00:00", 111, 57, 10, 14.8, "S")).encode()
    for i in range(0, len(data), 7):        # tiny chunks, no trailing newline
        lf.feed(data[i:i + 7])
    df = lf.to_frame({"111"}, set())
    assert len(df) == 1 and df["draught"].iloc[0] == 14.8


def test_fetch_day_streams_then_uses_cache(tmp_path):
    s = Session(zip_bytes(day_text(200)))
    df = draught.fetch_day("2025-03-05", {"111"}, set(), tmp_path, session=s)
    assert len(df) == 1 and len(s.urls) == 1
    assert not any(p.suffix == ".zip" for p in tmp_path.iterdir())   # nothing big kept
    draught.fetch_day("2025-03-05", {"111"}, set(), tmp_path, session=s)
    assert len(s.urls) == 1                                           # served from cache


class FakeReportClient:
    def __init__(self):
        self.calls = []

    def report(self, params, body):
        self.calls.append(dict(params))
        return {"entries": [{"public-global-presence:v3.0": [
            {"date": "2025-03-06", "vesselId": "v1", "hours": 9, "flag": "XX"},
            {"date": "2025-03-07", "vesselId": "v1", "hours": 3},
            {"date": "2025-03-06", "vesselId": "someone-else", "hours": 24},
        ]}]}


def test_locate_days_reads_presence_rows():
    c = FakeReportClient()
    df = draught.locate_days(c, identities(), "2025-03-01", "2025-03-15")
    assert list(df["day"]) == ["2025-03-06", "2025-03-07"]
    assert set(df["imo"]) == {SANCTIONED}
    p = c.calls[0]
    assert p["group-by"] == "VESSEL_ID" and p["spatial-aggregation"] == "true"
    assert p["filters[0]"] == "flag in ('XX')" and p["date-range"] == "2025-03-01,2025-03-15"


def test_run_uses_located_days(tmp_path, events):
    s = Session(zip_bytes(day_text(200)))
    pas, _ = draught.run(events, identities(), client=FakeReportClient(), session=s,
                         dma_dir=tmp_path / "dma", out=tmp_path / "out",
                         start="2025-03-01", end="2025-03-15")
    assert sorted(u.split("aisdk-")[1][:10] for u in s.urls) == ["2025-03-06", "2025-03-07"]


class ReportSession:
    def __init__(self, zipped):
        self.headers, self.posts, self.zipped = {}, [], zipped

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        payload = {"entries": [{"date": "2025-03-06", "vesselId": "v1", "hours": 2}]}

        class R:
            status_code, headers, text = 200, {}, ""
            content = (zip_json(payload) if self.zipped else b"{}")

            def json(_):
                return payload
        return R()


def zip_json(obj) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("report.json", json.dumps(obj))
    return buf.getvalue()


def test_report_unzips_and_keeps_filter_readable():
    s = ReportSession(zipped=True)
    c = GFWClient("t", pause=0, session=s)
    out = c.report([("filters[0]", "flag in ('COK')")], {"geojson": {"type": "Polygon"}})
    assert out["entries"][0]["vesselId"] == "v1"
    from urllib.parse import unquote_plus
    assert "filters[0]=flag in ('COK')" in unquote_plus(s.posts[0][0])
