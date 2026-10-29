from __future__ import annotations

from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.handlers import default_handlers
from drama_studio.worker import Worker


def make_client(tmp_path):
    app = create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects")
    return app, TestClient(app)


def test_health(tmp_path):
    _app, client = make_client(tmp_path)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert {engine["name"] for engine in body["engines"]} >= {
        "fake",
        "breeze",
        "chatterbox",
    }
    assert isinstance(body["engines"][0]["languages"], list)


def test_create_get_list_cancel(tmp_path):
    _app, client = make_client(tmp_path)
    created = client.post("/api/jobs", json={"type": "demo.echo", "payload": {"x": 1}})
    assert created.status_code == 201
    job = created.json()
    assert job["status"] == "queued"

    fetched = client.get(f"/api/jobs/{job['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["payload"] == {"x": 1}

    listed = client.get("/api/jobs")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    cancelled = client.post(f"/api/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_missing_job_404(tmp_path):
    _, client = make_client(tmp_path)
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_sse_terminal_job(tmp_path):
    app, client = make_client(tmp_path)
    job = client.post("/api/jobs", json={"type": "demo.echo", "payload": {"x": 1}}).json()
    queue = app.state.queue
    Worker(queue, default_handlers(), worker_id="test").run_once()

    with client.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: job" in body
    assert '"status": "succeeded"' in body
