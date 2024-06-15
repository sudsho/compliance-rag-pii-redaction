.PHONY: install fmt lint test run api ui ingest audit-verify clean compose-up compose-down

PYTHON ?= python
UVICORN ?= uvicorn

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m spacy download en_core_web_lg

fmt:
	ruff format src tests scripts

lint:
	ruff check src tests scripts

test:
	pytest -q

run: api

api:
	$(UVICORN) src.api.main:app --host 0.0.0.0 --port 8080 --reload

ui:
	streamlit run streamlit_app.py

ingest:
	$(PYTHON) -m scripts.reindex_sample_policies

audit-verify:
	$(PYTHON) -c "from src.audit import AuditLog; ok, at = AuditLog().verify_chain(); print(f'ok={ok} broken_at={at}')"

migrate:
	alembic upgrade head

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down -v

clean:
	rm -rf .chroma audit.db .pytest_cache __pycache__ **/__pycache__
