"""Ledger durability, and the endpoint that makes it possible.

The scanner exposes the ledgers read-only and unauthenticated; the weekly Action
pulls and commits them. **The direction is the security property** — the box
never holds a repository credential — but an unauthenticated file endpoint is
the kind of thing that ships a directory traversal, so that is the first test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monitor import archive

TRAVERSAL = [
    "../../../etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "....//....//etc/passwd",
    "series_history/../../../etc/passwd",
    "/etc/passwd",
    # The real filename, which is not the allowlist key -- serving it would mean
    # the handler is resolving names against the filesystem.
    "series_history.jsonl",
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from dashboard import app as app_module

    monkeypatch.chdir(tmp_path)
    # Keep the live engine from starting: it would try to load the baseline out
    # of the temporary cwd and sweep the exchange. These tests are about the
    # endpoint, not the scanner.
    monkeypatch.setitem(app_module._runtime, "snapshot_source", "test-fixture")
    ledger = tmp_path / "data" / "monitor"
    ledger.mkdir(parents=True)
    (ledger / "series_history.jsonl").write_text('{"event":"KXGDPYEAR-28"}\n')
    return TestClient(app_module.app)


# ------------------------------------------------------------------ safety ---


@pytest.mark.parametrize("name", TRAVERSAL)
def test_the_ledger_endpoint_serves_only_allowlisted_names(client, name):
    """The name indexes a fixed dict. It is never joined onto a path."""
    resp = client.get(f"/api/ledgers/{name}")
    assert resp.status_code in (404, 307, 405), f"{name!r} returned {resp.status_code}"
    if resp.status_code == 404 and resp.headers.get("content-type", "").startswith(
        "application/json"
    ):
        assert "root:" not in resp.text


def test_the_allowlist_is_a_dict_lookup_not_a_path_join():
    """Asserted on the source, because the safe and unsafe forms look alike."""
    source = Path("dashboard/app.py").read_text()
    handler = source.split("async def api_ledger(")[1].split("\n@app")[0]
    assert "LEDGER_FILES.get(name)" in handler
    for unsafe in ("/ name", '/ f"', "joinpath(name)", "+ name"):
        assert unsafe not in handler, f"{unsafe!r} in the handler is a traversal"


def test_the_endpoint_exposes_no_credential_bearing_file():
    """Every allowlisted path is a monitor ledger under data/."""
    for name, path in archive.LEDGER_FILES.items():
        assert path.suffix == ".jsonl", name
        assert path.parts[:2] == ("data", "monitor"), name


def test_serving_a_ledger_does_not_require_or_read_any_credential():
    source = Path("monitor/archive.py").read_text() + Path("dashboard/app.py").read_text()
    for forbidden in ("KALSHI_", "Authorization", "api_key", "private_key"):
        assert forbidden not in source


# ---------------------------------------------------------------- manifest ---


def test_the_manifest_reports_absence_rather_than_zero(tmp_path):
    m = archive.manifest(root=tmp_path)
    for name, meta in m.items():
        assert meta["present"] is False, name
        assert meta["sha256"] is None, "absent is not an empty file"


def test_the_manifest_digest_matches_the_served_body(client):
    manifest = client.get("/api/ledgers").json()["ledgers"]
    assert manifest["series_history"]["present"]
    body = client.get("/api/ledgers/series_history")
    assert body.status_code == 200
    assert archive.digest(body.text) == manifest["series_history"]["sha256"]
    assert manifest["series_history"]["lines"] == 1


def test_an_absent_ledger_is_404_not_an_empty_body(client):
    resp = client.get("/api/ledgers/band_events")
    assert resp.status_code == 404
    assert "not yet written" in resp.json()["error"]


# ------------------------------------------------------------------- fetch ---


def test_fetch_writes_every_present_ledger_and_reports_the_rest(client, tmp_path, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda **kw: client)
    into = tmp_path / "archive" / "ledgers"
    results = archive.fetch("http://testserver", into)

    assert results["series_history"]["status"] == "written"
    assert (into / "series_history.jsonl").read_text() == '{"event":"KXGDPYEAR-28"}\n'
    assert results["band_events"]["status"] == "absent-on-scanner"
    assert results["band_events"]["written"] is False


def test_an_unchanged_ledger_is_not_rewritten(client, tmp_path, monkeypatch):
    """So the Action can skip an empty commit without diffing."""
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda **kw: client)
    into = tmp_path / "archive" / "ledgers"
    archive.fetch("http://testserver", into)
    again = archive.fetch("http://testserver", into)
    assert again["series_history"]["status"] == "unchanged"
    assert again["series_history"]["written"] is False


def test_a_digest_mismatch_raises_rather_than_writing_a_corrupt_archive(
    client, tmp_path, monkeypatch
):
    """A silent partial archive is worse than a failed one."""
    import httpx

    real_get = client.get

    def tampered(url, *a, **kw):
        resp = real_get(url, *a, **kw)
        if url.endswith("/api/ledgers"):
            body = resp.json()
            body["ledgers"]["series_history"]["sha256"] = "0" * 64
            return httpx.Response(200, json=body, request=resp.request)
        return resp

    monkeypatch.setattr(client, "get", tampered)
    monkeypatch.setattr(httpx, "Client", lambda **kw: client)
    with pytest.raises(ValueError, match="digest mismatch"):
        archive.fetch("http://testserver", tmp_path / "out")
    assert not (tmp_path / "out" / "series_history.jsonl").exists()


def test_the_cli_returns_non_zero_so_the_action_fails_loudly(monkeypatch, capsys):
    monkeypatch.setattr(
        archive, "fetch", lambda *a, **kw: (_ for _ in ()).throw(OSError("unreachable"))
    )
    monkeypatch.setattr("sys.argv", ["archive", "--from", "https://nowhere.invalid"])
    assert archive.main() == 1
    assert "FAILED" in capsys.readouterr().err


# -------------------------------------------------------------- visibility ---


def test_serving_a_ledger_marks_the_archive_as_fetched(client):
    from dashboard.app import state

    state.ledgers_served_at = None
    client.get("/api/ledgers")
    assert state.ledgers_served_at is not None
    assert state.snapshot()["ledger_archive"]["age_seconds"] is not None


def test_a_never_fetched_archive_is_not_run_rather_than_stale():
    from scanner.state import ScannerState

    payload = ScannerState().snapshot()["ledger_archive"]
    assert payload["last_served"] is None
    assert payload["age_seconds"] is None
    assert payload["stale"] is False, "never fetched is not the same as overdue"
    assert "NOT_RUN(" in Path("dashboard/static/app.js").read_text()


def test_a_stale_archive_is_visible_on_the_panel():
    from datetime import timedelta

    from scanner.state import ScannerState, now

    state = ScannerState()
    state.ledgers_served_at = now() - timedelta(days=archive.ARCHIVE_STALE_AFTER_DAYS + 1)
    payload = state.snapshot()["ledger_archive"]
    assert payload["stale"] is True
    assert "archiveRow" in Path("dashboard/static/app.js").read_text()


# --------------------------------------------------------------- workflow ---


WORKFLOW = Path(".github/workflows/archive-ledgers.yml")


def test_the_workflow_pulls_and_never_pushes_from_the_box():
    text = WORKFLOW.read_text()
    assert "monitor.archive --from" in text
    assert "contents: write" in text, "the Action holds the write permission"
    assert "SCANNER_URL" in text
    # A repository variable, not a secret: the endpoint is public by design.
    assert "secrets.SCANNER_URL" not in text


def test_the_archive_is_committed_outside_the_gitignored_tree():
    """Force-adding into data/ survives until someone runs `git clean -X`."""
    assert archive.ARCHIVE_DIR.parts[0] != "data"
    text = WORKFLOW.read_text()
    assert "git add -f" not in text
    assert str(archive.ARCHIVE_DIR) in text
    assert "data/" not in Path(".gitignore").read_text().split("archive")[0].split("\n")[-1]


def test_json_manifest_shape_is_stable_for_the_action(client):
    body = client.get("/api/ledgers").json()
    assert set(body) == {"ledgers", "served_at"}
    for meta in body["ledgers"].values():
        assert set(meta) == {"present", "bytes", "lines", "sha256"}
    json.dumps(body)  # must stay serialisable
