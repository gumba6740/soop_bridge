.PHONY: lint format mypy fixall test

lint:
	uv run ruff check --fix app/

format:
	uv run ruff format app/

mypy:
	uv run mypy app/

fixall: lint format mypy

test:
	uv run pytest --cov=app --cov-report=term-missing tests/

shell:
	uv run ipython -i scripts/shell.py