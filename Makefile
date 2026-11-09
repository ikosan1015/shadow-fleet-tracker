.PHONY: setup fetch clean-data partners follow sanctions draught flags casefile maps report publish all test lint

setup:          ## create .venv and install everything
	uv sync --extra dev

fetch:          ## pull vessel identities + events from GFW into data/raw/
	uv run sft fetch

clean-data:     ## raw JSON -> tidy parquet in data/processed/
	uv run sft clean

partners:       ## other ships loitering at the same place + time -> partners.parquet
	uv run sft partners

follow:         ## port calls of alongside/listed partners around each meeting
	uv run sft follow

sanctions:      ## refresh OpenSanctions vessel list, then check studied ships + partners
	uv run sft sanctions update
	uv run sft sanctions check

draught:        ## OPTIONAL / parked: draught from Danish AIS (large downloads)
	uv run sft draught

flags:          ## apply anomaly rules -> data/processed/flags.parquet
	uv run sft flags

casefile:       ## report/cases/<imo>-<name>.md per vessel
	uv run sft casefile

maps:           ## dashboard: index, case pages, maps -> dashboard/site/
	uv run sft maps

report:         ## flags + casefile + maps (no API calls)
	uv run sft report

publish:        ## copy built maps into dashboard/public/ (what GitHub Pages serves)
	rm -rf dashboard/public && cp -r dashboard/site dashboard/public

all: fetch clean-data partners sanctions follow report

test:
	uv run pytest -q

lint:
	uv run ruff check src tests
