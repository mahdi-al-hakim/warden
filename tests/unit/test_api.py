"""HTTP tests for the API: start, stream, approve, revoke, and the read-only endpoints.

These use an async client over the ASGI app so the run's background task shares the test's event
loop. The sync TestClient cancels background tasks between calls, so it cannot drive this app.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from httpx import ASGITransport

from warden.api.app import create_app
from warden.config import Settings


def _app(**overrides):
    base = {"mode": "replay", "demo_step_delay_seconds": 0.0, "approval_timeout_seconds": 10.0}
    base.update(overrides)
    return create_app(Settings(**base))


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _poll(client, run_id, predicate, tries: int = 1000):
    snap = {}
    for _ in range(tries):
        snap = (await client.get(f"/api/sessions/{run_id}")).json()
        if predicate(snap):
            return snap
        await asyncio.sleep(0.005)
    return snap


async def _approve_when_pending(client, run_id) -> None:
    snap = await _poll(client, run_id, lambda s: s["pending_action_ids"] or s["done"])
    if snap.get("pending_action_ids"):
        await client.post(
            f"/api/sessions/{run_id}/approve",
            json={"action_id": snap["pending_action_ids"][0], "approved": True},
        )


async def test_read_only_endpoints():
    async with _client(_app()) as client:
        assert (await client.get("/healthz")).json()["status"] == "ok"
        config = (await client.get("/api/config")).json()
        assert config["mode"] == "replay"
        assert config["live_ready"] is False
        # With no GitHub token configured, the repository tool is the synthetic one.
        assert config["repo_backend"] == {"backend": "synthetic"}
        policy = (await client.get("/api/policy")).json()
        assert len(policy["rules"]) >= 3
        assert policy["ruleset_version"]


def test_config_reports_github_backend_when_configured(monkeypatch):
    # Reading config never touches the network; it only reflects the env, so this is safe offline.
    monkeypatch.setenv("WARDEN_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("WARDEN_GITHUB_ORG", "throwaway-org")
    monkeypatch.setenv("WARDEN_GITHUB_ALLOW_WRITES", "false")
    from fastapi.testclient import TestClient

    cfg = TestClient(create_app(Settings(mode="replay"))).get("/api/config").json()
    assert cfg["repo_backend"]["backend"] == "github"
    assert cfg["repo_backend"]["org"] == "throwaway-org"
    assert cfg["repo_backend"]["writes_enabled"] is False


async def test_full_run_with_approval_completes():
    async with _client(_app()) as client:
        start = (await client.post("/api/sessions", json={})).json()
        run_id = start["run_id"]
        assert start["source"] == "authored script"
        await _approve_when_pending(client, run_id)
        snap = await _poll(client, run_id, lambda s: s["done"])

        assert snap["status"] == "completed"
        types = [e["type"] for e in snap["events"]]
        assert "approval.requested" in types
        assert "session.completed" in types
        executed = [e for e in snap["events"] if e["type"] == "action.executed"]
        assert len(executed) >= 6
        denied = [
            e for e in snap["events"]
            if e["type"] == "action.blocked" and e["data"].get("outcome") == "denied"
        ]
        assert denied, "the privileged over-reach should be denied"


async def test_revoke_stops_the_run():
    # A small step delay guarantees the revoke lands before the first action executes.
    async with _client(_app(demo_step_delay_seconds=0.1)) as client:
        run_id = (await client.post("/api/sessions", json={})).json()["run_id"]
        assert (await client.post(f"/api/sessions/{run_id}/revoke", json={})).json()["revoked"]
        snap = await _poll(client, run_id, lambda s: s["done"])

        assert snap["status"] == "revoked"
        assert not any(e["type"] == "action.executed" for e in snap["events"])


async def test_sse_stream_follows_a_run_to_completion():
    async with _client(_app()) as client:
        run_id = (await client.post("/api/sessions", json={})).json()["run_id"]
        approver = asyncio.create_task(_approve_when_pending(client, run_id))
        seen: list[str] = []
        async with client.stream("GET", f"/api/sessions/{run_id}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[len("data:"):].strip())
                    seen.append(event["type"])
                    if event["type"] == "session.completed":
                        break
        await approver
        assert seen[0] == "session.started"
        assert "approval.requested" in seen
        assert seen[-1] == "session.completed"


async def test_benchmark_endpoint_shows_the_fix():
    async with _client(_app()) as client:
        report = (await client.get("/api/benchmark")).json()
        rows = {r["label"]: r for r in report["rows"]}
        nofix = rows["Offline validator, no signal"]
        fix = rows["Offline validator, CAEP-style signal"]
        assert fix["max_seconds"] < nofix["median_seconds"]


async def test_unknown_run_is_404():
    async with _client(_app()) as client:
        assert (await client.get("/api/sessions/nope")).status_code == 404
        with pytest.raises(httpx.HTTPStatusError):
            r = await client.post("/api/sessions/nope/approve", json={"action_id": "x"})
            r.raise_for_status()


async def test_an_approval_without_a_decision_is_rejected():
    # Leaving out "approved" must not count as approving the step.
    async with _client(_app()) as client:
        run_id = (await client.post("/api/sessions", json={})).json()["run_id"]
        snap = await _poll(client, run_id, lambda s: s["pending_action_ids"])
        resp = await client.post(f"/api/sessions/{run_id}/approve",
                                 json={"action_id": snap["pending_action_ids"][0]})
        assert resp.status_code == 422
        assert (await client.get(f"/api/sessions/{run_id}")).json()["pending_action_ids"]


async def test_the_number_of_runs_in_progress_is_capped():
    async with _client(_app(approval_timeout_seconds=30.0)) as client:
        codes = [(await client.post("/api/sessions", json={})).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 200, 429]


async def test_a_finished_run_cannot_be_revoked_and_its_audit_is_readable():
    async with _client(_app()) as client:
        run_id = (await client.post("/api/sessions", json={})).json()["run_id"]
        await _approve_when_pending(client, run_id)
        await _poll(client, run_id, lambda s: s["done"])
        assert (await client.post(f"/api/sessions/{run_id}/revoke", json={})).status_code == 409
        audit = (await client.get(f"/api/sessions/{run_id}/audit")).json()
        assert audit["verified"] is True
        assert audit["entries"] == len(audit["events"]) > 0


async def test_the_stream_resumes_after_the_last_event_id():
    async with _client(_app()) as client:
        run_id = (await client.post("/api/sessions", json={})).json()["run_id"]
        await _approve_when_pending(client, run_id)
        await _poll(client, run_id, lambda s: s["done"])
        total = len((await client.get(f"/api/sessions/{run_id}")).json()["events"])

        async def ids(headers):
            async with client.stream("GET", f"/api/sessions/{run_id}/events",
                                     headers=headers) as resp:
                text = "".join([chunk async for chunk in resp.aiter_text()])
            return [int(line[4:]) for line in text.splitlines() if line.startswith("id: ")]

        assert await ids({}) == list(range(total))
        assert await ids({"Last-Event-ID": "4"}) == list(range(5, total))


def test_config_names_the_replay_source():
    from fastapi.testclient import TestClient

    cfg = TestClient(_app()).get("/api/config").json()
    assert cfg["replay_source"] == "authored script, no model"
