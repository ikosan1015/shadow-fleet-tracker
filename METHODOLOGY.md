# Methodology

> Living document. Every threshold in `src/sft/flags.py` should be justified here.

## 1. Data source

All vessel activity comes from the Global Fishing Watch (GFW) API v3, which derives
events from AIS. GFW describes every event as **apparent**, not certain, and so does
this project. Data caveats: <https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats>

| Event | GFW dataset | GFW definition (paraphrased) |
|---|---|---|
| Port visit | `public-global-port-visits-events:latest` | entry / stop / gap / exit at a GFW anchorage; confidence 1–4 |
| AIS gap | `public-global-gaps-events:latest` | period with no AIS positions received; may be flagged as possibly intentional |
| Encounter | `public-global-encounters-events:latest` | two vessels apparently within ~500 m for ≥ 2 h |
| Loitering | `public-global-loitering-events:latest` | single vessel slow / stationary at sea; may also be waiting or maintenance |

## 2. Vessel selection

- Sanctioned set: _list IMOs, which list (EU / UK), listing dates, why chosen_
- Control set: _similar DWT, build year, trade area; not on any list_
- Study window: _e.g. 2023-01-01 → 2026-10-31_

## 3. Anomaly rules (v0 — tune and justify)

| Rule | Threshold | Why |
|---|---|---|
| `long_gap` | gap ≥ 12 h | _TBD: compare with control-group distribution_ |
| `gap_near_hotspot` | gap starts or ends ≤ 50 km from an STS hotspot | hotspots in `src/sft/geo.py` |
| `gap_after_russian_terminal` | gap starts ≤ 72 h after leaving a Russian oil terminal | _TBD_ |
| `encounter_with_listed` | encounter partner's IMO / name is on the watchlist | _TBD_ |
| `open_sea_loitering` | loitering ≥ 6 h, ≥ 20 km from any port anchorage | _TBD_ |

Each flagged event is a **lead to check**, not a finding.

## 4. Scoring against the control group

_Per-vessel rates (flags per 30 days of AIS coverage), sanctioned vs control._

## 5. Ownership and identity

_How GFW identity history was combined with registry / sanctions records._

## 6. Limitations

- AIS can be spoofed; GFW positions reflect what was broadcast.
- Satellite reception varies by region, so some gaps are technical, not deliberate.
- Encounters require both vessels to transmit; a "dark" STS partner is invisible.
- Identity matching in GFW can merge or split vessels across name / flag changes.
