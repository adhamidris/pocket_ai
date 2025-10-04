from httpx import AsyncClient
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    app = create_app()

    async with AsyncClient(app=app, base_url="http://testserver") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "environment" in payload
