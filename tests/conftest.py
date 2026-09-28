import socket
import threading
import time
import pytest
import uvicorn
from fastapi.testclient import TestClient
from app.config import load_settings
from app.demo import create_demo_app
from app.main import create_app


@pytest.fixture(scope="session")
def mock_services():
    app = create_demo_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def config(tmp_path, mock_services, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-2026!")
    cfg = load_settings("config/demo.toml")
    for k in ("github", "confluence", "llm"):
        cfg[k]["api_url"] = mock_services[0] + "/" + k
    cfg["storage"]["path"] = str(tmp_path / "review.sqlite3")
    return cfg


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app, headers={"X-Review-Request": "1"}) as c:
        assert (
            c.post(
                "/api/login", json={"password": config["auth"]["password"]}
            ).status_code
            == 200
        )
        yield c
        for thread in list(app.state.worker.threads.values()):
            thread.join(timeout=10)


def seed(client):
    r = client.post("/api/demo/seed", json={})
    assert r.status_code == 200, r.text
    return r.json()


def wait_done(client, eid):
    for _ in range(400):
        ev = client.get("/api/evaluations/" + eid).json()
        if ev["status"] != "running":
            return ev
        time.sleep(0.025)
    raise AssertionError("evaluation timed out")


def run_demo(client):
    ev = seed(client)
    r = client.post(
        f"/api/evaluations/{ev['id']}/start", json={"revision": ev["revision"]}
    )
    assert r.status_code == 200, r.text
    ev = wait_done(client, ev["id"])
    assert ev["status"] == "completed", ev.get("error")
    return ev
