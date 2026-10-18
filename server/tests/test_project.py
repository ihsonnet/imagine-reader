"""The project's repository and star count, looked up by the server and shared."""

import httpx

from imagine_reader_server import project


def test_the_star_count_is_asked_for_once_and_shared(client, monkeypatch):
    asked = []

    def fetch(repository):
        asked.append(repository)
        return 42

    monkeypatch.setattr(project, "_fetch", fetch)
    monkeypatch.setattr(project, "_cache", {})
    first = client.get("/api/project").json()
    second = client.get("/api/project").json()
    assert first == second == {"repository": "https://github.com/ihsonnet/imagine-reader", "stars": 42}
    assert asked == ["ihsonnet/imagine-reader"]


def test_an_unreachable_github_leaves_the_count_out(client, monkeypatch):
    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(project.httpx, "get", unreachable)
    monkeypatch.setattr(project, "_cache", {})
    assert client.get("/api/project").json()["stars"] is None
