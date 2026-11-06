.PHONY: lint format mypy fixall test init

lint:
	uv run ruff check --fix app/ tests/

format:
	@output=$$(uv run ruff format app/ tests/ 2>&1); status=$$?; \
	if [ $$status -ne 0 ]; then \
		printf '%s\n' "$$output"; \
		exit $$status; \
	elif printf '%s' "$$output" | grep -q 'reformatted'; then \
		printf '%s\n' "$$output"; \
	elif printf '%s' "$$output" | grep -q 'left unchanged'; then \
		printf '\033[32m%s\033[0m\n' "$$output"; \
	else \
		printf '%s\n' "$$output"; \
	fi

mypy:
	uv run mypy app/ tests/

fixall: lint format mypy

test:
	uv run pytest --cov=app --cov-report=term-missing tests/

shell:
	uv run ipython -i scripts/shell.py

init:
	uv run python scripts/make_init.py ${dir}