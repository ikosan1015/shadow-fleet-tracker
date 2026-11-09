"""End-to-end with a fake API: fetch -> clean -> flags -> maps, all in tmp_path."""

from conftest import SANCTIONED
from test_clean import SEARCH

from sft import clean, fetch, flags, maps
from sft.gfw import GFWClient


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.headers, self.text = payload, status, {}, ""

    def json(self):
        return self.payload


class FakeSession:
    """Serves the toy fixtures; paginates events 2 at a time to test nextOffset."""

    def __init__(self, raw_events):
        self.headers = {}
        self.raw = raw_events
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(url)
        if "/vessels/search" in url:
            return FakeResponse(SEARCH)
        etype = next(t for t in ("port-visits", "gaps", "encounters", "loitering") if t in url)
        want = {"port-visits": "port_visit", "gaps": "gap",
                "encounters": "encounter", "loitering": "loitering"}[etype]
        evs = [e for e in self.raw[SANCTIONED] if e["type"] == want]
        offset = int(url.split("offset=")[1].split("&")[0])
        page = evs[offset:offset + 1]
        nxt = offset + 1 if offset + 1 < len(evs) else None
        return FakeResponse({"entries": page, "nextOffset": nxt})


def test_full_pipeline(tmp_path, raw_events, vessels):
    session = FakeSession(raw_events)
    client = GFWClient("fake-token", pause=0, session=session)
    v = vessels[vessels["imo"] == SANCTIONED]

    raw_dir = fetch.run("2025-01-01", "2025-02-01", client=client,
                        raw_root=tmp_path / "raw", vessels=v)
    assert (raw_dir / "manifest.json").exists()
    assert "vessels[0]=v1&vessels[1]=v2" in session.calls[1]   # array params, both ids

    ids, ev = clean.run(raw_dir, out=tmp_path / "processed")
    assert len(ev) == 4

    fl = flags.apply_all(ev, vessels, ids)
    assert len(fl) >= 5
    out = maps.run(ev, fl, vessels, flags.summarize(fl, ev, vessels), out=tmp_path / "site")
    assert (out / "index.html").exists() and (out / f"{SANCTIONED}.html").exists()
