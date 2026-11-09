.PHONY: setup fetch clean-data flags maps all test lint

setup:          ## create .venv and install everything
	uv sync --extra dev

fetch:          ## pull vessel identities + events from GFW into data/raw/
	uv run sft fetch

clean-data:     ## raw JSON -> tidy parquet in data/processed/
	uv run sft clean

flags:          ## apply anomaly rules -> data/processed/flags.parquet
	uv run sft flags

maps:           ## build HTML maps into dashboard/site/
	uv run sft maps

publish:        ## copy built maps into dashboard/public/ (what GitHub Pages serves)
	rm -rf dashboard/public && cp -r dashboard/site dashboard/public

all: fetch clean-data flags maps

test:
	uv run pytest -q

lint:
	uv run ruff check src tests
