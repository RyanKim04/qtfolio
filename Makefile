.PHONY: install dev test

install:
	cd backend && uv sync
	cd frontend && npm install

dev:
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) & \
	(cd frontend && npm run dev) & \
	wait

test:
	cd backend && uv run pytest
