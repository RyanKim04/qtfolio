"""The API answers only local pages: a foreign Origin (a website POSTing a form)
or a foreign Host (DNS rebinding) is refused before any route runs."""

from httpx import ASGITransport, AsyncClient

from app.main import app


async def _post(base_url, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client:
        return await client.post("/api/import/activities", headers=headers or {})


async def test_local_requests_reach_the_route():
    # 422 = the route's own validation (no file) — the guards let it through.
    assert (await _post("http://127.0.0.1:8000")).status_code == 422
    assert (await _post("http://127.0.0.1:8000", {"origin": "http://127.0.0.1:5173"})).status_code == 422
    assert (await _post("http://localhost:5173", {"origin": "http://localhost:5173"})).status_code == 422


async def test_foreign_origin_refused():
    assert (await _post("http://127.0.0.1:8000", {"origin": "https://evil.example"})).status_code == 403
    assert (await _post("http://127.0.0.1:8000", {"origin": "null"})).status_code == 403


async def test_rebound_host_refused():
    assert (await _post("http://evil.example:8000")).status_code == 400
