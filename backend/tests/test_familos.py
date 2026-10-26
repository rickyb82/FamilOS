"""Regression tests for FamilOS file isolation, browser previews and multiwindow saves."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services.chat_service import prepare_user_blocks
from app.tools.code_exec import run_code
from app.tools.documents import read_document
from app.tools.rag_search import search_documents
from app.tools.registry import ToolContext


async def uploaded(client, name="private.txt", content=b"private family document", mime="text/plain"):
    response = await client.post("/api/files", files={"file": (name, content, mime)})
    assert response.status_code == 200, response.text
    return response.json()


async def test_ai_paths_enforce_same_acl_as_download(anna, bob, monkeypatch):
    file = await uploaded(anna)
    assert (await bob.get(file["url"])).status_code == 404
    from app.tools import code_exec, rag_search
    monkeypatch.setattr(code_exec.settings, "sandbox_url", "http://unused")
    monkeypatch.setattr(code_exec.settings, "allow_code_execution", True)
    search = AsyncMock()
    monkeypatch.setattr(rag_search.rag, "search", search)
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == "bob"))).scalar_one()
        ctx = ToolContext(user=user, policy={}, db=db)
        calls = [
            lambda: prepare_user_blocks("read", [file["id"]], user, db),
            lambda: read_document({"file_id": file["id"]}, ctx),
            lambda: run_code({"file_ids": [file["id"]], "code": "print('hello')"}, ctx),
            lambda: search_documents({"file_id": file["id"], "query": "family"}, ctx),
        ]
        for call in calls:
            with pytest.raises(HTTPException) as error:
                await call()
            assert error.value.status_code == 404
        search.assert_not_awaited()
    await anna.post("/api/shares", json={"resource_type": "file", "resource_id": file["id"],
                                        "target_user_id": user.id, "permission": "read"})
    async with SessionLocal() as db:
        blocks = await prepare_user_blocks("read", [file["id"]], user, db)
        assert "private family document" in str(blocks)


async def test_spoofed_pdf_cannot_execute_html(kid):
    file = await uploaded(kid, "document.pdf", b"<script>alert(1)</script>", "text/html")
    response = await kid.get(file["url"])
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert "application/octet-stream" in response.headers["content-type"]
    assert "sandbox" in response.headers["content-security-policy"]


async def test_editor_rejects_stale_save_and_keeps_winning_content(anna):
    file = await uploaded(anna, "multiwindow.txt", b"initial")
    url = f"/api/files/wordcraft-save?file_id={file['id']}"
    missing = await anna.post(url, content=b"missing", headers={"X-Filename": file["filename"]})
    assert missing.status_code == 428
    results = await asyncio.gather(*[
        anna.post(url, content=body, headers={"X-Filename": file["filename"], "If-Match": file["sha256"]})
        for body in (b"window A", b"window B")
    ])
    assert sorted(r.status_code for r in results) == [200, 412]
    winner = next(i for i, r in enumerate(results) if r.status_code == 200)
    assert (await anna.get(file["url"])).content == (b"window A", b"window B")[winner]
    version = results[winner].json()["sha256"]
    again = await anna.post(url, content=b"next save", headers={"X-Filename": file["filename"], "If-Match": version})
    assert again.status_code == 200
    assert (await anna.get(file["url"])).content == b"next save"


async def test_explicit_rag_file_does_not_expand_scope(anna, monkeypatch):
    file = await uploaded(anna)
    from app.tools import rag_search
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(rag_search.rag, "search", search)
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == "anna"))).scalar_one()
        ctx = ToolContext(user=user, policy={}, db=db, conversation_id="other-chat", project_id="other-project")
        await search_documents({"file_id": file["id"], "query": "family"}, ctx)
    assert search.await_args.kwargs["project_ids"] is None
    assert search.await_args.kwargs["conversation_ids"] is None
    assert search.await_args.kwargs["file_ids"] == [file["id"]]


async def test_metadata_preserves_permissions(anna, bob):
    file = await uploaded(anna)
    response = await anna.get(f"/api/files/{file['id']}/metadata")
    assert response.status_code == 200
    assert response.json()["sha256"] == file["sha256"]
    assert response.json()["access"] == "owner"
    assert (await bob.get(f"/api/files/{file['id']}/metadata")).status_code == 404


async def test_legacy_editor_images_use_independent_window_revisions(anna):
    file = await uploaded(anna, "legacy.txt", b"initial")
    root = f"/api/files/wordcraft-save?file_id={file['id']}&base_revision={file['sha256']}"
    first = root + "&edit_session=" + "a" * 32
    second = root + "&edit_session=" + "b" * 32
    headers = {"X-Filename": "legacy.txt"}
    assert (await anna.post(first, content=b"first window", headers=headers)).status_code == 200
    assert (await anna.post(first, content=b"first window again", headers=headers)).status_code == 200
    assert (await anna.post(second, content=b"stale second window", headers=headers)).status_code == 412
    assert (await anna.get(file["url"])).content == b"first window again"


async def test_family_catalog_excludes_telecom(admin, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "org_mode", "0")
    response = await admin.get("/api/admin/apps")
    assert response.status_code == 200
    ids = {a["id"] for a in response.json()}
    assert {"wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "mailcraft"} <= ids
    assert not ids & {"covercraft", "radiocraft", "netcraft", "starlink", "intranet", "connectcraft"}


async def test_all_supported_providers_can_be_registered(monkeypatch):
    from app.config import get_settings
    from app.providers import registry
    settings = get_settings()
    with monkeypatch.context() as patch:
        for key, value in {"openai_api_key": "test-key", "anthropic_api_key": "test-key", "gemini_api_key": "test-key",
                           "ollama_url": "http://local-test:11434", "custom_llm_url": "https://llm.example.com/v1",
                           "custom_llm_label": "Server di prova", "enable_mock_provider": False}.items():
            patch.setattr(settings, key, value)
        registry.reload_providers()
        providers = {p["id"]: p for p in registry.available_providers()}
        assert set(providers) == {"openai", "anthropic", "gemini", "ollama", "custom"}
        assert providers["custom"]["label"] == "Server di prova"
        for pid in providers:
            assert registry.get_provider(pid).id == pid
        for provider in ("auto", "mock", "comfyui"):
            with pytest.raises(HTTPException) as error:
                registry.get_provider(provider)
            assert error.value.status_code == 400
        assert registry.is_local("ollama") and not registry.is_local("custom") and not registry.is_local("anthropic")
    registry.reload_providers()


async def test_unconfigured_provider_reports_missing_configuration(monkeypatch):
    from app.config import get_settings
    from app.providers import registry
    settings = get_settings()
    with monkeypatch.context() as patch:
        patch.setattr(settings, "gemini_api_key", "")
        registry.reload_providers()
        with pytest.raises(HTTPException) as error:
            registry.get_provider("gemini")
        assert error.value.status_code == 503
    registry.reload_providers()


async def test_provider_cards_cover_every_supported_provider(admin):
    response = await admin.get("/api/admin/providers")
    assert response.status_code == 200
    cards = {p["id"]: p for p in response.json()["providers"]}
    assert set(cards) == {"openai", "anthropic", "gemini", "ollama", "custom"}
    assert set(cards["custom"]["extra_states"]) == {"custom_llm_key", "custom_llm_label"}
    assert cards["custom"]["extra_states"]["custom_llm_key"]["secret"] is True
    assert (await admin.put("/api/admin/providers", json={"values": {"comfyui_url": "http://x"}})).status_code == 400
    assert (await admin.put("/api/admin/providers", json={"values": {"embedding_provider": "gemini"}})).status_code == 400
    assert (await admin.post("/api/admin/providers/comfyui/test")).status_code == 404


async def test_unknown_provider_does_not_fall_back_to_another(admin):
    assert (await admin.post("/api/chat/conversations", json={"provider": "auto"})).status_code == 400
    response = await admin.post("/api/chat/conversations", json={"provider": "gemini"})
    assert response.status_code in (400, 503)


async def test_admin_costs_include_budgets_for_cloud_providers(admin):
    costs = await admin.get("/api/admin/costs")
    assert costs.status_code == 200
    assert set(costs.json()["settings"]) == {"pricing_json", "budget_openai", "budget_anthropic", "budget_gemini", "openai_admin_key"}
    assert {p["provider"] for p in costs.json()["providers"]} == {"openai"}   # gli altri compaiono quando configurati
    assert (await admin.get("/api/admin/audit")).status_code == 200
    assert (await admin.get("/api/admin/costs/users")).status_code == 200


def test_provider_configuration_fields_exist():
    from app.config import Settings
    settings = Settings(anthropic_api_key="k", gemini_api_key="k", custom_llm_url="http://server:8000")
    assert {"anthropic_api_key", "gemini_api_key", "custom_llm_url", "custom_llm_key", "custom_llm_model",
            "custom_llm_label"} <= type(settings).model_fields.keys()
    assert settings.custom_llm_url == "http://server:8000"


async def test_family_migration_removes_departments_and_preserves_files(admin, anna, kid):
    from app.models import Folder, FileAsset, Chunk
    from app.services import workspace
    folders = (await admin.get("/api/folders")).json()
    root = next(f for f in folders if f["kind"] == "org")
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
        legacy = Folder(family_id=user.family_id,owner_id=user.id,parent_id=root["id"],name="Commerciale",kind="dept",dept="commerciale")
        db.add(legacy); await db.commit()
        legacy_id = legacy.id
    file = await uploaded(admin, "familiare.txt", b"documento da conservare")
    assert (await admin.patch(f"/api/files/{file['id']}",json={"folder_id":legacy_id})).status_code == 200
    folders = (await admin.get("/api/folders")).json()
    assert legacy_id not in {f["id"] for f in folders}
    assert not any(f["dept"] in {"tecnico","commerciale","amministrativo"} for f in folders)
    metadata = (await admin.get(f"/api/files/{file['id']}/metadata")).json()
    assert metadata["folder_id"] == root["id"]
    assert (await admin.get(file["url"])).content == b"documento da conservare"
    assert (await anna.get(file["url"])).status_code == 200
    assert (await kid.get(file["url"])).status_code == 404
    assert len((await admin.get("/api/folders")).json()) == len(folders)
