# shadow-fleet-tracker

Reconstructing the voyages of 3–5 sanctioned tankers from the EU/UK shadow-fleet lists, using AIS-based events from the Global Fishing Watch API, plus public records on ownership, flag and insurer changes.

**Outputs:** notebook(s) · map visualisations · 3–5 page write-up with sources graded for confidence · public dashboard

> 🚧 Work in progress. The plan below doubles as my progress tracker.

---

## Plan & progress

### Phase 0: Setup (day 1–2)
- [ ] Register for a Global Fishing Watch API token (non-commercial use)
- [ ] Store the token in `.env` (gitignored). Commit only `.env.example`
- [ ] Read the [GFW data caveats](https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats) and note the event definitions in `METHODOLOGY.md`
  - GFW labels every event as *apparent*, not certain. Use the same language in the write-up
  - Encounter: two vessels apparently within ~500 m for at least 2 h
  - Loitering: a single vessel behaving like a possible encounter. It can also mean maintenance or waiting for a berth
- [ ] Set up the environment (`pyproject.toml`, pinned dependencies)

### Phase 1: Vessel selection (week 1)
- [ ] Pull the current EU list (Official Journal annex) and the UK Sanctions List (specified ships)
- [ ] Pick 3–5 sanctioned tankers. Criteria, recorded in `data/vessels.csv`:
  - has an IMO number and good AIS coverage in GFW
  - a mix of EU-only, UK-only and dual-listed ships
  - a mix of early and recent designations, to allow before/after comparison
- [ ] Pick 3–5 **non-sanctioned control tankers** of similar size and age. Without a baseline, I can't show that a pattern is unusual

### Phase 2: Data pipeline (week 1–2)
- [ ] Use the Vessels API to resolve IMO numbers to GFW vessel IDs. Note ships with multiple IDs, since name and flag changes are evidence in themselves
- [ ] Use the Events API to pull events for each vessel:
  - [ ] port visits (`public-global-port-visits-events:latest`)
  - [ ] AIS gaps (`public-global-gaps-events:latest`)
  - [ ] encounters (`public-global-encounters-events:latest`)
  - [ ] loitering (`public-global-loitering-events:latest`)
- [ ] Cache raw responses in `data/raw/` with the query date, and never edit raw data
- [ ] Add a cleaning step that writes tidy parquet files to `data/processed/`
- [ ] Check the GFW terms before committing any data extracts. The default is to commit code plus a re-fetch script

### Phase 3: Anomaly flags (week 2–3)
- [ ] Start with transparent, auditable rules:
  - [ ] AIS gaps longer than X h that start or end near known STS hotspots (Laconian Gulf, off Ceuta, Gulf of Finland approaches, off Malaysia)
  - [ ] gaps where the distance or time before and after implies a suspicious route
  - [ ] encounters with other listed vessels
  - [ ] port calls at Russian oil terminals (Primorsk, Ust-Luga, Novorossiysk) followed by gaps
  - [ ] loitering in open water, away from ports
- [ ] Score sanctioned tankers against the control group
- [ ] (Optional) Run an Isolation Forest on per-voyage features, clearly labelled as exploratory
- [ ] Write unit tests for the flag rules using hand-made toy tracks

### Phase 4: Ownership, flag & insurer history (week 3–4)
- [ ] Build a timeline per ship: name, flag, registered owner, manager, P&I insurer, class society
- [ ] Sources: GFW identity history, EU Official Journal, UK Sanctions List, OpenSanctions, IMO GISIS, Equasis (cite it, don't republish its tables), class withdrawal notices, investigative reporting
- [ ] Build a network graph (ship → owner → manager → address) and look for shared addresses and shell companies

### Phase 5: Write-up, dashboard, release (week 4–6)
- [ ] Grade every source with the Admiralty Code (source reliability A–F, information credibility 1–6) in `SOURCES.md`
- [ ] Write a 3–5 page report: summary, method, one section per tanker (the strongest 2–3, with the rest in an appendix), limitations, sources
- [ ] Use careful wording: *apparent*, *consistent with*, *could not verify*. Don't name crew or private individuals unless they appear in official designations
- [ ] Build maps (Folium / kepler.gl) and timelines (Plotly)
- [ ] Deploy the dashboard as a static site on GitHub Pages
- [ ] Tag `v1.0`, archive it on Zenodo for a DOI, and add `CITATION.cff`

---

## Repo structure

```
shadow-fleet-tracker/
├── README.md            # findings first (once done), then how to reproduce
├── METHODOLOGY.md       # event definitions, thresholds, control group, limits
├── SOURCES.md           # every source, graded A1–F6
├── pyproject.toml
├── .env.example         # GFW_TOKEN=
├── src/sft/             # fetch.py, clean.py, flags.py, ownership.py, maps.py
├── notebooks/
│   ├── 01_select_vessels.ipynb
│   ├── 02_events_eda.ipynb
│   ├── 03_anomaly_flags.ipynb
│   └── 04_ownership.ipynb
├── data/                # raw/ (gitignored), processed/, vessels.csv
├── tests/
├── report/              # Quarto source → PDF + HTML
└── dashboard/           # static site → GitHub Pages
```

## Working practices

- **Issues as a research log:** one issue per tanker and per open question. Close each with a comment stating what I found
- **Project board:** Backlog → Investigating → Verified → Written up
- **Small, descriptive commits**, so the history works as an audit trail
- **Notebooks tell the story, `src/` holds the logic.** Keep notebook outputs saved so charts render on GitHub
- **GitHub Actions:** (1) tests and linting on every push, (2) rebuild and deploy the report and dashboard to Pages
- **Licences:** MIT for code, CC BY 4.0 for the report text. Third-party data stays under its own terms

## Reproduce

Requires [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
cp .env.example .env   # paste your GFW token
make setup             # creates .venv with all dependencies
# edit data/vessels.csv with your IMOs (group = sanctioned | control)
make all               # fetch -> clean -> partners -> flags -> maps
make test              # unit tests on hand-made toy data (no API needed)
```

| Command | What it does | Output |
|---|---|---|
| `make fetch` | GFW identity search + 4 event types per IMO | `data/raw/<date>/` (gitignored) |
| `make clean-data` | raw JSON → tidy tables | `data/processed/events.parquet`, `identities.parquet` |
| `make partners` | other ships loitering at the same place and time (inferred meetings) | `partners.parquet` |
| `uv run sft draught --suggest` | list days each ship was probably in Danish waters | (prints) |
| `make draught` | draught snapshots from Danish AIS (≈0.5–1 GB download per day, deleted after filtering) | `draught_passages.csv`, `draught_flags.parquet` |
| `make flags` | anomaly rules from `src/sft/flags.py` (uses partners + draught if present) | `flags.parquet`, `flag_summary.csv`, `rules_used.json` |
| `make maps` | one Folium map per vessel + index | `dashboard/site/` |
| `make publish` | copy maps to what GitHub Pages serves | `dashboard/public/` |

Open notebooks with `uv run jupyter lab`.

## Key references

- [GFW API: Data Caveats](https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats)
- [GFW Events API](https://globalfishingwatch.org/our-apis/documentation/docs/v3/events.md)
- [UK Sanctions List](https://www.gov.uk/government/publications/the-uk-sanctions-list)
- EU shadow-fleet list: Annex to Council Regulation (EU) 833/2014, as amended (latest: 21st package, July 2026)
