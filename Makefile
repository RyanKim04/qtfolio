.PHONY: install dev test simulate-gap

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

# Rewind token_keepalive's last success by 2 days, then restart the backend to
# watch the startup catch-up pass run it immediately (M1 acceptance check).
simulate-gap:
	sqlite3 data/qtfolio.db "UPDATE job_runs SET started_at = datetime(started_at, '-2 days'), finished_at = datetime(finished_at, '-2 days') WHERE job_name = 'token_keepalive';"
	@echo "token_keepalive rewound 2 days; restart the backend and check Settings > Sync."
