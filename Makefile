UV ?= uv

.DEFAULT_GOAL := check
.PHONY: check lint type test fmt budget

check: lint type test

lint:
	$(UV) run ruff check .

type:
	$(UV) run mypy src

test:
	$(UV) run pytest -q

fmt:
	$(UV) run ruff format .

# Live probe (spends quota, about $0.006): weekly and after any config change.
budget:
	$(UV) run tokenlab budget
