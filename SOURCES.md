# Sources & confidence grading

Grading uses the Admiralty Code:

- **Source reliability:** A completely reliable · B usually reliable · C fairly reliable · D not usually reliable · E unreliable · F cannot be judged
- **Information credibility:** 1 confirmed by other sources · 2 probably true · 3 possibly true · 4 doubtful · 5 improbable · 6 cannot be judged

| ID | Source | Type | Accessed | Used for | Grade | Notes |
|---|---|---|---|---|---|---|
| S1 | Global Fishing Watch API v3 | AIS-derived events | | voyages, gaps, encounters | B2 | algorithmic, "apparent" events |
| S2 | EU Official Journal — Annex to Reg. (EU) 833/2014 | official list | | listing status / date | A1 | |
| S3 | UK Sanctions List (FCDO) | official list | | listing status / date | A1 | |
| S4 | OpenSanctions (maritime dataset + matching API) | aggregator | | listing status of ships and partners | B2 | CC BY-NC 4.0; confirm each match in the original list |
| S5 | IMO GISIS | registry | | owner / flag history | A2 | |
| S6 | Equasis | registry | | owner, manager, P&I, class | B2 | cite only; do not republish tables |
| S7 | Copernicus Browser (Sentinel-1 / -2 imagery) | satellite imagery | | checking suspected meetings | A2 | free account; links generated in case files |
