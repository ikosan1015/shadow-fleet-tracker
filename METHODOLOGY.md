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
| `alongside_listed` | a watchlisted vessel loitering ≤ 0.5 km away with overlapping time | see 3a |
| `alongside_meeting` | any vessel ≤ 0.5 km away (hulls practically touching) | see 3a |
| `colocated_with_listed` | a watchlisted vessel ≤ 5 km away, not in a crowded anchorage | see 3a |
| `colocated_loitering` | an unlisted vessel ≤ 5 km away, not in a crowded anchorage | see 3a |
| `listed_nearby_in_anchorage` | a watchlisted vessel ≤ 5 km away in a crowded anchorage (weak: may just be queuing) | see 3a |
| `unloaded_without_port_call` | draught fell ≥ 2 m between two passages, no non-Russian port stay ≥ 12 h in between | see 3b |
| `loaded_without_port_call` | draught rose ≥ 2 m between two passages, no port stay ≥ 12 h in between | see 3b |

Each flagged event is a **lead to check**, not a finding.

### 3a. Inferred meetings from co-located loitering (`src/sft/partners.py`)

GFW's encounter model does not cover tanker–tanker pairs, so ship-to-ship (STS)
transfers between tankers do not appear as encounters. Instead, for every loitering
event of a studied vessel lasting ≥ 6 h, we query GFW for loitering events by **any**
vessel inside a ±10 km box over the same dates, and keep those within 5 km that
overlap in time as set out above. Partner identity (IMO, flag, ship type) comes from the
GFW Vessels API. A match is evidence *consistent with* a meeting at sea, not proof
of a cargo transfer. Raw API answers are cached in `data/raw/<date>/partners/`.

Two refinements separate strong from weak matches:

- **Alongside vs nearby.** A partner ≤ 0.5 km away is effectively alongside (a large tanker
  is ~40–60 m wide); 0.5–5 km is only "in the same area".
- **Crowded anchorages.** If more than 4 distinct vessels loitered around one of our events
  (e.g. the Suez Canal waiting area off Port Said), the spot is treated as an anchorage or
  queue. Such events are flagged only if a partner was alongside or watchlisted, and then
  with the crowd size in the flag detail.

### 3d. Sanctions status (`src/sft/sanctions.py`)

Listing status comes from OpenSanctions (free for non-commercial use; data CC BY-NC 4.0).
`sft sanctions update` downloads the daily maritime file and keeps vessels with a
**direct** sanction listing as the watchlist (vessels only *linked* to sanctioned parties
are excluded but still reported by `sft sanctions check`). Matches are by IMO; name-only
matches are marked "VERIFY" because ship names are not unique. With an API key, ships not
matched by IMO are also sent to the OpenSanctions matching API (threshold 0.8).
Every match used in the report is checked against the original source list (EU Official
Journal, UK Sanctions List) linked from the OpenSanctions profile.

### 3b. Draught snapshots from Danish AIS (`src/sft/draught.py`)

Draught (how deep the hull sits) is broadcast in AIS. GFW's public API does not
provide it, so we use the Danish Maritime Authority's free daily AIS files
(<http://web.ais.dk/aisdata/>), which cover the Danish straits that Baltic tankers
pass through. Each passage gives one snapshot (median reported draught that day).
Candidate days are suggested from GFW events (calls near Denmark; 1–4 days after
leaving or before reaching an eastern-Baltic port). Draught is entered manually by
crews and can be stale, so it is graded as corroborating evidence only.

### 3c. Evidence ladder for a suspected STS transfer

1. Loitering of plausible length, at near-zero speed, away from port
2. A co-located partner (3a), ideally a listed or tanker-type vessel
3. Cargo logic: loading / discharge pattern only explained by a transfer at sea
4. Draught change consistent with it (3b)
5. Satellite imagery (Sentinel-1 / -2) showing two adjacent hulls
6. Independent reporting or official records

Each case in the report states which rungs were reached, with graded sources.

## 4. Scoring against the control group

_Per-vessel rates (flags per 30 days of AIS coverage), sanctioned vs control._

## 5. Ownership and identity

_How GFW identity history was combined with registry / sanctions records._

## 6. Limitations

- AIS can be spoofed; GFW positions reflect what was broadcast.
- Satellite reception varies by region, so some gaps are technical, not deliberate.
- Encounters require both vessels to transmit; a "dark" STS partner is invisible.
- **GFW encounter events do not include tanker–tanker pairs** (documented pairs: fishing–carrier,
  fishing–support, fishing–bunker, fishing–fishing, tanker–fishing, carrier–bunker, support–bunker).
  Hence the co-located loitering method in 3a.
- **GFW AIS-off (gap) events only exist for gaps ≥ 12 h that start ≥ 50 nm from shore in areas
  of good satellite reception.** Narrow waters such as the Gulf of Finland are effectively
  excluded. For EAGLE S (2024–2025) the gaps dataset returned 0 events with intentional-disabling
  set to either true or false.
- GFW port visits can span long periods; a visit whose start and end anchorages differ
  (`port_start_name` ≠ `port_end_name`) should be checked against the raw record.
- Danish AIS only covers Danish waters, so draught snapshots exist only for Baltic transits.
- Identity matching in GFW can merge or split vessels across name / flag changes.
