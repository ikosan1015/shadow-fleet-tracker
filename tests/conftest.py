"""Hand-made toy data shaped like GFW API responses. No network needed."""

import pandas as pd
import pytest

from sft.clean import events_frame, parse_event

SANCTIONED = "1111111"
CONTROL = "2222222"


def gfw_event(id_, type_, start, end, lat, lon, **extra):
    e = {"id": id_, "type": type_, "start": start, "end": end,
         "position": {"lat": lat, "lon": lon},
         "vessel": {"id": "vid-" + id_[:1], "name": "TOY", "flag": "XX"}}
    e.update(extra)
    return e


@pytest.fixture
def raw_events():
    return {
        SANCTIONED: [
            # port call at Primorsk, leaves 2025-01-02 00:00
            gfw_event("p1", "port_visit", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z",
                      60.35, 28.66, port_visit={"intermediateAnchorage":
                      {"name": "PRIMORSK", "flag": "RUS", "anchorageId": "a1"},
                      "confidence": 4}),
            # 30 h gap starting 10 h later, switching off near the Laconian Gulf
            gfw_event("g1", "gap", "2025-01-02T10:00:00Z", "2025-01-03T16:00:00Z",
                      36.5, 22.7, gap={"offPosition": {"lat": 36.56, "lon": 22.71},
                                       "onPosition": {"lat": 36.0, "lon": 23.5},
                                       "distanceKm": 90, "intentionalDisabling": True}),
            # encounter with another listed tanker
            gfw_event("e1", "encounter", "2025-01-04T00:00:00Z", "2025-01-04T05:00:00Z",
                      36.4, 22.8, encounter={"vessel": {"id": "x", "name": "M/T Dark Star",
                                                        "flag": "CM", "ssvid": "613000000"},
                                             "medianDistanceKilometers": 0.1}),
            # 10 h loitering in open sea
            gfw_event("l1", "loitering", "2025-01-05T00:00:00Z", "2025-01-05T10:00:00Z",
                      35.0, 20.0, loitering={"totalDistanceKm": 3, "averageSpeedKnots": 0.5}),
        ],
        CONTROL: [
            gfw_event("p2", "port_visit", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z",
                      51.95, 4.05, port_visit={"intermediateAnchorage":
                      {"name": "ROTTERDAM", "flag": "NLD"}, "confidence": 4}),
            gfw_event("g2", "gap", "2025-01-03T00:00:00Z", "2025-01-03T03:00:00Z",
                      50.0, 0.0, gap={"offPosition": {"lat": 50.0, "lon": 0.0},
                                      "onPosition": {"lat": 50.1, "lon": 0.3}}),
        ],
    }


@pytest.fixture
def events(raw_events):
    rows = [parse_event(e, imo) for imo, evs in raw_events.items() for e in evs]
    return events_frame(rows)


@pytest.fixture
def vessels():
    return pd.DataFrame({
        "imo": [SANCTIONED, CONTROL, "3333333"],
        "name": ["TOY TANKER", "TOY CONTROL", "DARK STAR"],
        "group": ["sanctioned", "control", "sanctioned"],
    })
