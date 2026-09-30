.PHONY: lint format check fixall test

lint:
	uv run ruff check --fix app/

format:
	uv run ruff format app/

check:
	uv run mypy app/

fixall: lint format check

test:
	uv run pytest --cov=app --cov-report=term-missing tests/

shell:
	uv run ipython -i scripts/shell.py