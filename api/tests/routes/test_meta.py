from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.routes.conftest import FakeRedis


def test_meta_returns_five_preset_motions_and_versions(client: TestClient) -> None:
    resp = client.get("/v1/meta")
    assert resp.status_code == 200
    body = resp.json()

    assert body["api_version"] == "v1"
    assert body["platform_constraints_version"] == "v1"
    assert body["models"] == {
        "image": "gpt-image-2",
        "video": "veo-3.1",
        "reconciler": "gpt-5-mini",
    }

    motions = body["preset_motions"]
    assert len(motions) == 5
    types = [m["type"] for m in motions]
    assert set(types) == {
        "preset_wave",
        "preset_nod",
        "preset_gesture",
        "preset_happy",
        "preset_idle",
    }
    for m in motions:
        assert m["display_name_zh"]
        assert m["display_name_en"]
        assert isinstance(m["default_duration_ms"], int)
        assert m["default_duration_ms"] > 0

    assert body["degraded_services"] == []


def test_meta_does_not_require_auth(client: TestClient) -> None:
    # No Authorization header attached; must succeed.
    resp = client.get("/v1/meta")
    assert resp.status_code == 200


async def test_meta_surfaces_degraded_entry_from_redis(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    await fake_redis.set(
        "degraded:gpt-image-2",
        json.dumps(
            {
                "reason": "CIRCUIT_OPEN",
                "retry_at": "2026-04-23T11:00:00Z",
                "message": "暫停 5 分鐘",
            }
        ),
    )

    resp = client.get("/v1/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded_services"] == [
        {
            "service": "gpt-image-2",
            "reason": "CIRCUIT_OPEN",
            "retry_at": "2026-04-23T11:00:00Z",
            "message": "暫停 5 分鐘",
        }
    ]


async def test_meta_sorts_multiple_degraded_entries(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    await fake_redis.set(
        "degraded:veo-3.1",
        json.dumps({"reason": "RATE_LIMITED", "message": "rate limited"}),
    )
    await fake_redis.set(
        "degraded:gpt-image-2",
        json.dumps({"reason": "CIRCUIT_OPEN", "message": "circuit open"}),
    )

    resp = client.get("/v1/meta")
    services = [e["service"] for e in resp.json()["degraded_services"]]
    assert services == ["gpt-image-2", "veo-3.1"]


async def test_meta_skips_malformed_degraded_value(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    # Non-JSON value at the key — must not 500 the endpoint.
    await fake_redis.set("degraded:broken", "not-json{")
    await fake_redis.set(
        "degraded:gpt-image-2",
        json.dumps({"reason": "CIRCUIT_OPEN"}),
    )

    resp = client.get("/v1/meta")
    assert resp.status_code == 200
    services = [e["service"] for e in resp.json()["degraded_services"]]
    assert services == ["gpt-image-2"]


async def test_meta_drops_unknown_fields_from_degraded_payload(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    # Writer leaks an extra field — must be stripped before surfacing.
    await fake_redis.set(
        "degraded:reconciler",
        json.dumps(
            {
                "reason": "CIRCUIT_OPEN",
                "message": "down",
                "leaked_internal_debug": "secret",
            }
        ),
    )

    resp = client.get("/v1/meta")
    entry = resp.json()["degraded_services"][0]
    assert entry == {
        "service": "reconciler",
        "reason": "CIRCUIT_OPEN",
        "retry_at": None,
        "message": "down",
    }
    assert "leaked_internal_debug" not in entry


async def test_meta_drops_non_string_field_values_without_500(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    """A payload whose `reason` is a nested object used to pass `isinstance(dict)`
    and then 500 the endpoint at Pydantic validation time. The loader now
    drops individual type-invalid fields and keeps the rest of the entry.
    """
    await fake_redis.set(
        "degraded:gpt-image-2",
        json.dumps(
            {
                "reason": {"code": "OPEN"},  # wrong type — must be dropped
                "message": "down",
            }
        ),
    )

    resp = client.get("/v1/meta")
    assert resp.status_code == 200
    entry = resp.json()["degraded_services"][0]
    assert entry["service"] == "gpt-image-2"
    assert entry["reason"] is None  # dropped
    assert entry["message"] == "down"


async def test_meta_survives_redis_outage_returning_empty_degraded(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    """A Redis outage during the scan_iter must not 500 /v1/meta — the
    endpoint also serves static metadata (models, preset_motions, versions),
    which the Frontend still needs during infra incidents.
    """

    # Monkey-patch scan_iter on the existing fake to raise.
    async def _exploding_scan_iter(*_args: object, **_kwargs: object):
        raise RuntimeError("redis connection refused")
        yield  # pragma: no cover — unreachable; satisfies async-generator shape

    fake_redis.scan_iter = _exploding_scan_iter  # type: ignore[method-assign]

    resp = client.get("/v1/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded_services"] == []
    # Static metadata still served.
    assert body["api_version"] == "v1"
    assert len(body["preset_motions"]) == 5
    assert body["models"]["image"] == "gpt-image-2"
