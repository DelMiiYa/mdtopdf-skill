.PHONY: check lint typecheck security test

check: lint typecheck security test

lint:
	ruff check .

typecheck:
	mypy src tests

security:
	bandit -r src/

test:
	pytest -v
