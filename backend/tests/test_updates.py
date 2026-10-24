import importlib.util
from pathlib import Path
import tarfile
import time

import pytest
from fastapi import HTTPException

from app.api import updates

SPEC = importlib.util.spec_from_file_location("familos_updater", Path(__file__).resolve().parents[2] / "extras/updater/agent.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def release(tag, **kw):
    return {"id": 1, "tag_name": tag, "draft": False, "prerelease": False, **kw}


@pytest.mark.parametrize("channel,expected", [("stable", "v1.1.0"), ("experimental", "v1.2.0-rc.1")])
def test_channels_select_published_releases(channel, expected):
    releases = [release("v1.0.0"), release("v1.1.0"), release("v1.2.0-rc.1", prerelease=True), release("v9.0.0", draft=True), release("main")]
    assert agent.select_release(releases, channel)["tag_name"] == expected


def test_stable_rejects_mislabelled_prerelease():
    assert agent.select_release([release("v1.2.0-rc.1")], "stable") is None
    assert agent.select_release([release("v1.2.0", prerelease=True)], "stable") is None


@pytest.mark.parametrize("value", ["main", "v1.0.0;id", "--help", "../v1.0.0", "v01.0.0"])
def test_invalid_version(value):
    with pytest.raises(ValueError):
        agent.version(value)


@pytest.mark.parametrize("value", ["https://evil.test/a/b", "../x", "a/..", "a/b;id", "a/b/c", "git@evil.test:a/b"])
def test_invalid_source(value):
    with pytest.raises(ValueError):
        agent.repository(value)


def test_fork_origin():
    assert agent.repository("git@github.com:family/FamilOS.git") == "family/FamilOS"
    assert agent.repository("https://github.com/family/FamilOS.git") == "family/FamilOS"


@pytest.fixture
def updater(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "VERSION").write_text("1.0.0")
    (root / ".env").write_text("EXISTING=value\n")
    return agent.Updater({"root": str(root), "state": str(tmp_path / "state"), "repositories": {name: "family/" + name for name in ("core", *agent.APPS)}})


def set_offer(updater, experimental=True):
    offer = {"id": "a" * 32, "release_id": 1, "channel": "experimental" if experimental else "stable", "target": "core", "tag": "v1.1.0-rc.1" if experimental else "v1.1.0", "sha": "b" * 40, "created": time.time(), "newer": True, "experimental": experimental}
    updater.state["offers"] = {"core": offer}
    body = {"action": "install", "target": "core", "channel": offer["channel"], "offer_id": offer["id"], "accept_experimental": True, "confirm_maintenance": True}
    return offer, body


def test_experimental_needs_explicit_consent(updater):
    _, body = set_offer(updater)
    body["accept_experimental"] = False
    with pytest.raises(ValueError, match="esplicitamente"):
        updater.validate_offer(body)


@pytest.mark.parametrize("change", ["expired", "wrong_channel", "wrong_id", "downgrade"])
def test_stale_or_invalid_offer(updater, change):
    offer, body = set_offer(updater)
    if change == "expired": offer["created"] -= 901
    if change == "wrong_channel": body["channel"] = "stable"
    if change == "wrong_id": body["offer_id"] = "c" * 32
    if change == "downgrade": offer["newer"] = False
    with pytest.raises(ValueError):
        updater.validate_offer(body)


def test_moved_tag_rejected(updater, monkeypatch):
    offer, body = set_offer(updater)
    monkeypatch.setattr(updater, "github", lambda target, path: release(offer["tag"], prerelease=True) if path.startswith("releases/") else {"sha": "c" * 40})
    with pytest.raises(ValueError, match="spostato"):
        updater.validate_offer(body)


def test_release_reclassified_requires_new_check(updater, monkeypatch):
    offer, body = set_offer(updater, experimental=False)
    monkeypatch.setattr(updater, "github", lambda *args: release(offer["tag"], prerelease=True))
    with pytest.raises(ValueError, match="cambiata"):
        updater.validate_offer(body)


def test_valid_offer(updater, monkeypatch):
    offer, body = set_offer(updater)
    monkeypatch.setattr(updater, "github", lambda target, path: release(offer["tag"], prerelease=True) if path.startswith("releases/") else {"sha": offer["sha"]})
    assert updater.validate_offer(body) == offer


@pytest.mark.parametrize("body", [
    {"action": "shell", "target": "core"},
    {"action": "check", "target": "../app", "channel": "stable"},
    {"action": "check", "target": "core", "channel": "main"},
    {"action": "check", "target": "core", "channel": "stable", "command": "id"},
    {"action": "install", "target": "core", "channel": "stable"},
    {"action": "disable", "target": "core", "confirm_maintenance": True},
])
def test_protocol_rejects_untrusted_commands(updater, body):
    with pytest.raises(ValueError):
        updater.submit(body)


def test_recovery_blocks_new_jobs(updater):
    updater.state["job"] = {"status": "recovery_required"}
    with pytest.raises(ValueError):
        updater.submit({"action": "check", "target": "core", "channel": "stable"})


def test_backup_never_silently_skips_user_symlinks():
    item = tarfile.TarInfo("data/documents/link")
    item.type = tarfile.SYMTYPE
    with pytest.raises(ValueError): agent.backup_filter(item)
    item.name = "private/updater-socket/control.sock"
    assert agent.backup_filter(item) is None


@pytest.mark.asyncio
async def test_disconnected_status(admin, monkeypatch):
    async def missing(*args): raise HTTPException(503, "offline")
    monkeypatch.setattr(updates, "agent_request", missing)
    result = await admin.get("/api/admin/updates")
    assert result.status_code == 200
    assert result.json()["connected"] is False
    assert result.json()["channel"] == "stable"


@pytest.mark.asyncio
async def test_check_channel_validation_and_bridge(admin, monkeypatch):
    calls = []
    async def request(*args):
        calls.append(args)
        return {"status": "running"}
    monkeypatch.setattr(updates, "agent_request", request)
    result = await admin.post("/api/admin/updates/check", json={"target": "core", "channel": "experimental"})
    assert result.status_code == 200
    assert calls[0][2]["channel"] == "experimental"
    result = await admin.post("/api/admin/updates/check", json={"channel": "main"})
    assert result.status_code == 422
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_install_requires_confirmation(admin, monkeypatch):
    async def forbidden(*args): pytest.fail("No host request before confirmation")
    monkeypatch.setattr(updates, "agent_request", forbidden)
    result = await admin.post("/api/admin/updates/install", json={"offer_id": "a" * 32})
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_non_admin_cannot_check(anna, kid, monkeypatch):
    async def forbidden(*args): pytest.fail("No host request for non-admins")
    monkeypatch.setattr(updates, "agent_request", forbidden)
    for client in (anna, kid):
        assert (await client.get("/api/admin/updates")).status_code == 403
        assert (await client.post("/api/admin/updates/check", json={})).status_code == 403


@pytest.mark.parametrize("failure", [None, "build", "backup", "health"])
def test_update_lifecycle_preserves_data_and_stops_on_failure(updater, monkeypatch, failure):
    (updater.root / "data").mkdir()
    (updater.root / "data/test.txt").write_text("family data")
    old_env = (updater.root / ".env").read_text()
    offer, body = set_offer(updater)
    body["target"] = "gridcraft"
    updater.state["job"] = {"id": "d" * 32, "status": "running"}
    monkeypatch.setattr(updater, "validate_offer", lambda _: offer)
    commands = []
    def run(*args, **kw):
        if args[:3] == ("git", "status", "--porcelain"): return ""
        if args[0] == "git": return "e" * 40
        return "sha256:" + "f" * 64
    def compose(*args):
        commands.append(args)
        if args[:2] == ("ps", "--services"): return "backend\nfrontend\ncaddy"
        if args[:2] == ("ps", "-q"): return "a" * 64
        if failure == "build" and args[0] == "build": raise RuntimeError("build failed")
        if failure == "health" and args[:3] == ("exec", "-T", "backend"): raise RuntimeError("health failed")
        return ""
    monkeypatch.setattr(updater, "run", run)
    monkeypatch.setattr(updater, "compose", compose)
    monkeypatch.setattr(agent.time, "sleep", lambda _: None)
    if failure == "backup":
        monkeypatch.setattr(agent.tarfile, "open", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    updater.work(body)
    assert (updater.root / "data/test.txt").read_text() == "family data"
    job = updater.state["job"]
    if failure == "build":
        assert job["status"] == "failed"
        assert (updater.root / ".env").read_text() == old_env
        assert not any(c[0] in ("up", "stop") for c in commands)
    elif failure in ("backup", "health"):
        assert job["status"] == "recovery_required"
        assert commands[-1][0] == "stop"
        assert not any(c[0] == "up" and c[-1] == "caddy" for c in commands)
        if failure == "backup": assert not any(c[0] == "up" for c in commands)
    else:
        assert job["status"] == "complete"
        with tarfile.open(job["backup"]) as archive:
            assert archive.extractfile(".env").read().decode() == old_env
            assert archive.extractfile("data/test.txt").read() == b"family data"
        stop_index = next(i for i, c in enumerate(commands) if c[0] == "stop")
        up_index = next(i for i, c in enumerate(commands) if c[0] == "up")
        assert stop_index < up_index
        assert updater.state["installed"]["gridcraft"]["enabled"] is True


def test_old_admin_session_cannot_install():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    ctx = SimpleNamespace(session=SimpleNamespace(created_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
    with pytest.raises(HTTPException) as error:
        updates.recent_admin(ctx, True)
    assert error.value.status_code == 403
