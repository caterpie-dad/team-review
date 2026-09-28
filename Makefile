.PHONY: test lint demo verify

test:
	.venv/bin/pytest -q
lint:
	.venv/bin/ruff check app tests scripts
demo:
	./start.sh demo
verify:
	.venv/bin/python scripts/verify_demo.py
