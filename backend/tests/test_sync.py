"""Craft Sync: collegamento dei dispositivi, cursore dei cambiamenti, contenuto con If-Match, cartelle, visibilita'."""
from __future__ import annotations

import hashlib

from conftest import make_client


async def upload(browser, name: str, data: bytes, folder_id: str | None = None) -> dict:
    files = {"file": (name, data, "application/octet-stream")}
    form = {"folder_id": folder_id} if folder_id else {}
    r = await browser.post("/api/files", files=files, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def items(changes: dict, type_: str | None = None, deleted: bool | None = None) -> list[dict]:
    out = changes["items"]
    if type_:   # un tipo senza `deleted` = solo gli elementi vivi di quel tipo (le lapidi hanno lo stesso `type`)
        out = [i for i in out if i["type"] == type_ and (deleted is not None or not i.get("deleted"))]
    if deleted is not None:
        out = [i for i in out if bool(i.get("deleted")) == deleted]
    return out


# ---------------------------------------------------------------- dispositivi ----
async def test_pairing_flow_and_revoke(admin, device):
    r = await admin.post("/api/sync/devices/pairing-code")
    assert r.status_code == 200
    code = r.json()["code"]
    assert len(code) == 9 and code[4] == "-"

    async with make_client() as anon:
        r = await anon.post("/api/sync/pair", json={"code": code.lower().replace("-", " "), "name": "Portatile", "platform": "macos"})
        assert r.status_code == 200, r.text
        body = r.json()
        token = body["token"]
        assert token.startswith("cs_") and body["user"]["username"] == "admin" and body["server"]["name"] == "Famiglia Test"
        # il codice vale una volta sola
        r = await anon.post("/api/sync/pair", json={"code": code, "name": "Altro"})
        assert r.status_code == 400
        # senza token, o con un token inventato, niente
        assert (await anon.get("/api/sync/me")).status_code == 401
    async with make_client(token="cs_" + "x" * 40) as bad:
        assert (await bad.get("/api/sync/me")).status_code == 401

    async with make_client(token=token) as dev:
        r = await dev.get("/api/sync/me")
        assert r.status_code == 200 and r.json()["device"]["name"] == "Portatile"

    r = await admin.get("/api/sync/devices")
    mine = [d for d in r.json() if d["name"] == "Portatile"]
    assert len(mine) == 1 and mine[0]["platform"] == "macos" and not mine[0]["revoked"]

    r = await admin.patch(f"/api/sync/devices/{mine[0]['id']}", json={"name": "MacBook"})
    assert r.status_code == 200 and r.json()["name"] == "MacBook"

    r = await admin.delete(f"/api/sync/devices/{mine[0]['id']}")
    assert r.status_code == 200
    async with make_client(token=token) as dev:
        assert (await dev.get("/api/sync/me")).status_code == 401
        assert (await dev.get("/api/sync/changes")).status_code == 401


async def test_children_cannot_pair_and_codes_expire_on_replacement(kid, anna):
    assert (await kid.post("/api/sync/devices/pairing-code")).status_code == 403
    first = (await anna.post("/api/sync/devices/pairing-code")).json()["code"]
    second = (await anna.post("/api/sync/devices/pairing-code")).json()["code"]
    async with make_client() as anon:
        assert (await anon.post("/api/sync/pair", json={"code": first, "name": "x"})).status_code == 400
        assert (await anon.post("/api/sync/pair", json={"code": second, "name": "x"})).status_code == 200


async def test_pairing_is_rate_limited_per_ip(seeded):
    from app.api.sync import pair_limiter
    pair_limiter.hits.clear()
    async with make_client() as anon:
        codes = [(await anon.post("/api/sync/pair", json={"code": "AAAA-AAAA", "name": "x"})).status_code for _ in range(11)]
    assert codes[:10] == [400] * 10 and codes[10] == 429
    pair_limiter.hits.clear()


async def test_devices_are_per_user_unless_admin(anna, admin, device):
    await device(anna, name="PC Anna")
    names = {d["name"] for d in (await admin.get("/api/sync/devices")).json()}
    assert "PC Anna" not in names
    everyone = (await admin.get("/api/sync/devices?all=1")).json()
    mine = [d for d in everyone if d["name"] == "PC Anna"]
    assert mine and mine[0]["user_name"] == "Anna"
    # Anna non vede quelli degli altri nemmeno chiedendo all=1
    assert all(d["name"] == "PC Anna" or d["user_id"] == mine[0]["user_id"] for d in (await anna.get("/api/sync/devices?all=1")).json())


# ---------------------------------------------------------------- cambiamenti ----
async def test_changes_follow_upload_rename_delete(anna, device):
    token, _ = await device(anna)
    async with make_client(token=token) as dev:
        base = (await dev.get("/api/sync/changes")).json()
        cursor = base["cursor"]
        assert base["since"] == 0 and not base["more"]

        data = b"ciao mondo\n"
        f = await upload(anna, "note.txt", data)
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        got = [i for i in items(ch, "file") if i["id"] == f["id"]]
        assert len(got) == 1
        assert got[0]["filename"] == "note.txt" and got[0]["sha256"] == hashlib.sha256(data).hexdigest()
        assert got[0]["access"] == "owner" and got[0]["size"] == len(data) and got[0]["seq"] > cursor
        assert ch["cursor"] >= got[0]["seq"]
        cursor = ch["cursor"]

        # niente di nuovo: il cursore non torna indietro e la lista e' vuota
        again = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        assert again["items"] == [] and again["cursor"] >= cursor

        r = await anna.patch(f"/api/files/{f['id']}", json={"filename": "appunti"})
        assert r.status_code == 200
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        got = [i for i in items(ch, "file") if i["id"] == f["id"]]
        assert len(got) == 1 and got[0]["filename"] == "appunti.txt"
        assert got[0]["sha256"] == hashlib.sha256(data).hexdigest()   # il contenuto non e' cambiato
        cursor = ch["cursor"]

        assert (await anna.delete(f"/api/files/{f['id']}")).status_code == 200
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        tomb = [i for i in items(ch, deleted=True) if i["id"] == f["id"]]
        assert len(tomb) == 1 and tomb[0]["type"] == "file"
        # e l'intero stato (since=0) non lo contiene piu' come file vivo
        full = (await dev.get("/api/sync/changes")).json()
        assert f["id"] not in {i["id"] for i in items(full, "file")}


async def test_pre_existing_rows_get_a_seq_and_a_hash(anna, device):
    """File nati prima di Craft Sync (seq NULL, sha vuoto) compaiono lo stesso, con l'impronta calcolata al volo."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models import FileAsset

    f = await upload(anna, "vecchio.txt", b"vecchio contenuto")
    async with SessionLocal() as db:
        await db.execute(update(FileAsset).where(FileAsset.id == f["id"]).values(seq=None, sha256="", updated_at=None))
        await db.commit()
    token, _ = await device(anna)
    async with make_client(token=token) as dev:
        full = (await dev.get("/api/sync/changes")).json()
        got = [i for i in items(full, "file") if i["id"] == f["id"]]
        assert got and got[0]["seq"] and got[0]["sha256"] == hashlib.sha256(b"vecchio contenuto").hexdigest()


# ---------------------------------------------------------------- contenuto ----
async def test_content_round_trip_with_if_match_and_versions(anna, device):
    token, info = await device(anna, name="Studio")
    v1 = b"prima versione"
    f = await upload(anna, "doc.txt", v1)
    sha1 = hashlib.sha256(v1).hexdigest()
    async with make_client(token=token) as dev:
        r = await dev.get(f"/api/sync/files/{f['id']}/content")
        assert r.status_code == 200 and r.content == v1 and r.headers["etag"] == f'"{sha1}"'

        v2 = b"seconda versione, dal computer"
        # If-Match obbligatorio
        assert (await dev.put(f"/api/sync/files/{f['id']}/content", content=v2)).status_code == 428
        # If-Match sbagliato: 412 con lo stato attuale del server
        r = await dev.put(f"/api/sync/files/{f['id']}/content", content=v2, headers={"If-Match": "0" * 64})
        assert r.status_code == 412 and r.json()["file"]["sha256"] == sha1
        assert (await dev.get(f"/api/sync/files/{f['id']}/content")).content == v1
        # If-Match giusto: scrive, nuova impronta, nuovo seq
        r = await dev.put(f"/api/sync/files/{f['id']}/content", content=v2, headers={"If-Match": f'"{sha1}"'})
        assert r.status_code == 200, r.text
        sha2 = hashlib.sha256(v2).hexdigest()
        assert r.json()["sha256"] == sha2 and r.json()["size"] == len(v2)
        assert (await dev.get(f"/api/sync/files/{f['id']}/content")).content == v2

        # la versione precedente e' in cronologia, firmata dal dispositivo
        vers = (await dev.get(f"/api/sync/files/{f['id']}/versions")).json()
        assert len(vers) == 1 and "Craft Sync" in vers[0]["note"] and "Studio" in vers[0]["note"]
        assert (await dev.get(f"/api/sync/files/{f['id']}/versions/{vers[0]['id']}")).content == v1
        # lo stesso contenuto di nuovo: nessuna versione in piu'
        r = await dev.put(f"/api/sync/files/{f['id']}/content", content=v2, headers={"If-Match": sha2})
        assert r.status_code == 200
        assert len((await dev.get(f"/api/sync/files/{f['id']}/versions")).json()) == 1
        # * forza la scrittura
        r = await dev.put(f"/api/sync/files/{f['id']}/content", content=b"terza", headers={"If-Match": "*"})
        assert r.status_code == 200 and r.json()["sha256"] == hashlib.sha256(b"terza").hexdigest()

    # il browser vede la nuova versione e la stessa cronologia: la terza scrittura, dello stesso dispositivo entro cinque
    # minuti, si accorpa alla versione gia' messa da parte (come i salvataggi ravvicinati dell'editor)
    assert (await anna.get(f"/api/files/{f['id']}")).content == b"terza"
    assert len((await anna.get(f"/api/files/{f['id']}/versions")).json()) == 1


async def test_browser_save_is_seen_by_the_device(anna, device):
    """Salvataggio dall'editor nel browser (wordcraft-save): il dispositivo trova il file cambiato nel prossimo /changes."""
    token, _ = await device(anna)
    f = await upload(anna, "testo.txt", b"uno")
    async with make_client(token=token) as dev:
        cursor = (await dev.get("/api/sync/changes")).json()["cursor"]
        r = await anna.post(f"/api/files/wordcraft-save?file_id={f['id']}", content=b"due", headers={"X-Filename": "testo.txt", "If-Match": f["sha256"]})
        assert r.status_code == 200, r.text
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        got = [i for i in items(ch, "file") if i["id"] == f["id"]]
        assert len(got) == 1 and got[0]["sha256"] == hashlib.sha256(b"due").hexdigest()


# ---------------------------------------------------------------- file e cartelle dal computer ----
async def test_create_rename_move_delete_from_device(anna, device):
    token, _ = await device(anna)
    async with make_client(token=token) as dev:
        r = await dev.post("/api/sync/files?filename=relazione.txt", content=b"testo")
        assert r.status_code == 201, r.text
        a = r.json()
        assert a["filename"] == "relazione.txt" and a["folder_id"] is None and a["sha256"] == hashlib.sha256(b"testo").hexdigest()
        # stesso nome: 409 con il file esistente, oppure rinomina automatica
        r = await dev.post("/api/sync/files?filename=relazione.txt", content=b"altro")
        assert r.status_code == 409 and r.json()["file"]["id"] == a["id"]
        r = await dev.post("/api/sync/files?filename=relazione.txt&on_conflict=rename", content=b"altro")
        assert r.status_code == 201 and r.json()["filename"] == "relazione 2.txt"
        # nome mancante o vuoto
        assert (await dev.post("/api/sync/files", content=b"x")).status_code == 400

        r = await dev.post("/api/sync/folders", json={"name": "Progetti"})
        assert r.status_code == 201, r.text
        folder = r.json()
        assert folder["type"] == "folder" and folder["can_write"] and folder["seq"]
        r = await dev.post(f"/api/sync/files?filename=piano.txt&folder_id={folder['id']}", content=b"piano")
        assert r.status_code == 201 and r.json()["folder_id"] == folder["id"]
        piano = r.json()

        cursor = (await dev.get("/api/sync/changes")).json()["cursor"]
        r = await dev.patch(f"/api/sync/files/{a['id']}", json={"filename": "relazione finale", "folder_id": folder["id"]})
        assert r.status_code == 200 and r.json()["filename"] == "relazione finale.txt" and r.json()["folder_id"] == folder["id"]
        r = await dev.patch(f"/api/sync/folders/{folder['id']}", json={"name": "Progetti 2026"})
        assert r.status_code == 200 and r.json()["name"] == "Progetti 2026"
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        assert {i["id"] for i in items(ch)} >= {a["id"], folder["id"]}

        # il browser vede la stessa cosa
        names = {f["filename"]: f["folder_id"] for f in (await anna.get("/api/files")).json()}
        assert names["relazione finale.txt"] == folder["id"] and names["piano.txt"] == folder["id"]

        cursor = ch["cursor"]
        assert (await dev.delete(f"/api/sync/folders/{folder['id']}")).status_code == 200
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        tombs = {i["id"] for i in items(ch, deleted=True)}
        assert folder["id"] in tombs
        moved = {i["id"]: i for i in items(ch, "file")}
        assert moved[a["id"]]["folder_id"] is None and moved[piano["id"]]["folder_id"] is None   # risaliti alla radice

        assert (await dev.delete(f"/api/sync/files/{piano['id']}")).status_code == 200
        assert (await dev.get(f"/api/sync/files/{piano['id']}/content")).status_code == 404


# ---------------------------------------------------------------- organizzazione ----
async def test_family_archive_visibility_and_read_only(admin, anna, bob, device):
    folders = (await admin.get("/api/folders")).json()   # crea l'archivio dell'organizzazione
    tecnico = next(f for f in folders if f["kind"] == "org")
    org_root = next(f for f in folders if f["kind"] == "org")
    f = await upload(admin, "procedura.txt", b"procedura tecnica", tecnico["id"])

    t_anna, _ = await device(anna)
    t_bob, _ = await device(bob)
    async with make_client(token=t_anna) as dev:
        full = (await dev.get("/api/sync/changes")).json()
        got = [i for i in items(full, "file") if i["id"] == f["id"]]
        assert got and got[0]["access"] == "read"   # del reparto, ma la scrive solo l'amministratore
        tree = (await dev.get("/api/sync/tree")).json()["folders"]
        kinds = {x["id"]: x for x in tree}
        assert kinds[tecnico["id"]]["folder_kind"] == "org" and not kinds[tecnico["id"]]["can_write"]
        assert kinds[org_root["id"]]["folder_kind"] == "org"
        assert (await dev.get(f"/api/sync/files/{f['id']}/content")).content == b"procedura tecnica"
        r = await dev.put(f"/api/sync/files/{f['id']}/content", content=b"x", headers={"If-Match": "*"})
        assert r.status_code == 403
        assert (await dev.post(f"/api/sync/files?filename=nuovo.txt&folder_id={tecnico['id']}", content=b"x")).status_code == 403
    async with make_client(token=t_bob) as dev:
        full = (await dev.get("/api/sync/changes")).json()
        assert f["id"] not in {i["id"] for i in items(full, "file")}   # Bob non ha il software Archivio
        assert tecnico["id"] not in {x["id"] for x in (await dev.get("/api/sync/tree")).json()["folders"]}
        assert (await dev.get(f"/api/sync/files/{f['id']}/content")).status_code == 404

    # l'amministratore scrive nell'archivio, e Anna lo vede cambiare
    t_admin, _ = await device(admin)
    async with make_client(token=t_anna) as anna_dev, make_client(token=t_admin) as admin_dev:
        cursor = (await anna_dev.get("/api/sync/changes")).json()["cursor"]
        sha = hashlib.sha256(b"procedura tecnica").hexdigest()
        r = await admin_dev.put(f"/api/sync/files/{f['id']}/content", content=b"procedura 2", headers={"If-Match": sha})
        assert r.status_code == 200 and r.json()["access"] == "write"
        ch = (await anna_dev.get(f"/api/sync/changes?since={cursor}")).json()
        got = [i for i in items(ch, "file") if i["id"] == f["id"]]
        assert got and got[0]["sha256"] == hashlib.sha256(b"procedura 2").hexdigest()
        # eliminazione dall'archivio: la lapide arriva anche a chi la vedeva
        cursor = ch["cursor"]
        assert (await admin.delete(f"/api/files/{f['id']}")).status_code == 200
        ch = (await anna_dev.get(f"/api/sync/changes?since={cursor}")).json()
        assert f["id"] in {i["id"] for i in items(ch, deleted=True)}


async def test_changes_pagination(anna, device):
    token, _ = await device(anna)
    for i in range(5):
        await upload(anna, f"p{i}.txt", f"p{i}".encode())
    async with make_client(token=token) as dev:
        seen: list[str] = []
        since, more, rounds = 0, True, 0
        while more and rounds < 50:
            ch = (await dev.get(f"/api/sync/changes?since={since}&limit=2")).json()
            assert len(ch["items"]) <= 2
            seen += [i["id"] for i in ch["items"]]
            since, more, rounds = ch["cursor"], ch["more"], rounds + 1
        assert len(seen) == len(set(seen)) and rounds >= 3
        full = (await dev.get("/api/sync/changes")).json()
        assert set(seen) == {i["id"] for i in full["items"]}


# ---------------------------------------------------------------- correzioni della revisione ----
async def test_put_content_runs_the_antivirus(anna, device, monkeypatch):
    """Una sovrascrittura dal computer passa dal controllo antivirus come un caricamento, e un file rifiutato resta intatto."""
    from app.api import files as files_api
    token, _ = await device(anna)
    f = await upload(anna, "pulito.txt", b"pulito")
    monkeypatch.setattr(files_api.settings, "upload_scan_cmd", "false")   # ogni file e' "infetto"
    monkeypatch.setattr(files_api.settings, "upload_scan_fail_open", False)
    async with make_client(token=token) as dev:
        r = await dev.put(f"/api/sync/files/{f['id']}/content", content=b"cattivo", headers={"If-Match": "*"})
        assert r.status_code == 422
        assert (await dev.get(f"/api/sync/files/{f['id']}/content")).content == b"pulito"
        assert len((await dev.get(f"/api/sync/files/{f['id']}/versions")).json()) == 0
    monkeypatch.setattr(files_api.settings, "upload_scan_cmd", "")
    async with make_client(token=token) as dev:
        assert (await dev.put(f"/api/sync/files/{f['id']}/content", content=b"buono", headers={"If-Match": "*"})).status_code == 200
    import glob
    assert not glob.glob(str(files_api.settings.uploads_dir / "*.craftsync-*.tmp"))


async def test_file_leaving_the_perimeter_leaves_a_tombstone(admin, anna, bob, device):
    """Spostato in un altro reparto o nelle cartelle personali dell'admin, il file sparisce dal PC di chi non lo vede piu'."""
    folders = (await admin.get("/api/folders")).json()
    tecnico = next(f for f in folders if f["kind"] == "org")
    commerciale = (await admin.post("/api/folders", json={"name": "Privati admin"})).json()
    f = await upload(admin, "segreto.txt", b"solo tecnici", tecnico["id"])
    t_anna, _ = await device(anna)
    t_bob, _ = await device(bob)
    async with make_client(token=t_anna) as dev, make_client(token=t_bob) as bob_dev:
        cursor = (await dev.get("/api/sync/changes")).json()["cursor"]
        bob_cursor = (await bob_dev.get("/api/sync/changes")).json()["cursor"]
        assert (await admin.patch(f"/api/files/{f['id']}", json={"folder_id": commerciale["id"]})).status_code == 200
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        assert f["id"] in {i["id"] for i in items(ch, deleted=True)}
        assert f["id"] not in {i["id"] for i in items(ch, "file")}
        # Bob non ha l'Archivio: nemmeno le lapidi dell'archivio lo raggiungono
        ch = (await bob_dev.get(f"/api/sync/changes?since={bob_cursor}")).json()
        assert f["id"] not in {i["id"] for i in ch["items"]}
        # torna in Tecnico: Anna riceve la riga viva (la lapide dello spostamento precedente e' nel passato)
        cursor = (await dev.get("/api/sync/changes")).json()["cursor"]
        assert (await admin.patch(f"/api/files/{f['id']}", json={"folder_id": tecnico["id"]})).status_code == 200
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        assert f["id"] in {i["id"] for i in items(ch, "file")} and f["id"] not in {i["id"] for i in items(ch, deleted=True)}
        # spostato fra due cartelle che Anna vede entrambe: solo la riga viva, mai una lapide nella stessa risposta
        sub = (await admin.post("/api/folders", json={"name": "Sotto", "parent_id": tecnico["id"]})).json()
        cursor = ch["cursor"]
        assert (await admin.patch(f"/api/files/{f['id']}", json={"folder_id": sub["id"]})).status_code == 200
        ch = (await dev.get(f"/api/sync/changes?since={cursor}")).json()
        assert f["id"] in {i["id"] for i in items(ch, "file")} and f["id"] not in {i["id"] for i in items(ch, deleted=True)}


async def test_changes_rejects_absurd_cursors(anna, device):
    token, _ = await device(anna)
    async with make_client(token=token) as dev:
        assert (await dev.get("/api/sync/changes?since=99999999999999999999999")).status_code == 422
        assert (await dev.get("/api/sync/changes?since=-1")).status_code == 422
        assert (await dev.get("/api/sync/changes?limit=0")).status_code == 422
        assert (await dev.get("/api/sync/changes?since=0&limit=1")).status_code == 200


async def test_restore_sets_the_hash_at_once(anna, device):
    token, _ = await device(anna)
    f = await upload(anna, "storia.txt", b"uno")
    async with make_client(token=token) as dev:
        assert (await dev.put(f"/api/sync/files/{f['id']}/content", content=b"due", headers={"If-Match": "*"})).status_code == 200
        v = (await dev.get(f"/api/sync/files/{f['id']}/versions")).json()[0]
    r = await anna.post(f"/api/files/{f['id']}/versions/{v['id']}/restore")
    assert r.status_code == 200
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import FileAsset
    async with SessionLocal() as db:
        row = (await db.execute(select(FileAsset.sha256).where(FileAsset.id == f["id"]))).scalar_one()
    assert row == hashlib.sha256(b"uno").hexdigest()


# ---------------------------------------------------------------- collegamento con le credenziali ----
async def sync_login(**body) -> tuple[int, dict]:
    """`POST /api/sync/login` come farebbe l'agente, con i limiti azzerati (tutti i test arrivano dallo stesso IP)."""
    from app.api.sync import login_limiter
    login_limiter.hits.clear()
    payload = {"email": "anna@test.local", "password": "x", "name": "PC credenziali", "platform": "windows", "app_version": "0.2"}
    payload.update(body)
    async with make_client() as anon:
        r = await anon.post("/api/sync/login", json=payload)
    return r.status_code, r.json()


async def _set_user(username: str, **fields) -> str:
    from app.db import SessionLocal
    from app.models import User
    from sqlalchemy import select
    async with SessionLocal() as db:
        u = (await db.execute(select(User).where(User.username == username))).scalar_one()
        for k, v in fields.items():
            setattr(u, k, v)
        await db.commit()
        return u.id


async def test_login_with_credentials_pairs_a_device(anna):
    status, body = await sync_login()
    assert status == 200, body
    assert body["token"].startswith("cs_") and body["user"]["username"] == "anna" and body["server"]["name"] == "Famiglia Test"
    assert body["device"]["name"] == "PC credenziali" and body["device"]["platform"] == "windows"
    async with make_client(token=body["token"]) as dev:
        r = await dev.get("/api/sync/me")
        assert r.status_code == 200 and r.json()["device"]["id"] == body["device"]["id"]
    mine = [d for d in (await anna.get("/api/sync/devices")).json() if d["id"] == body["device"]["id"]]
    assert len(mine) == 1 and mine[0]["app_version"] == "0.2" and not mine[0]["revoked"]
    # l'email non distingue maiuscole e minuscole, come nel login del browser
    status, _ = await sync_login(email="Anna@Test.local")
    assert status == 200


async def test_login_refuses_wrong_password_children_and_unknown(seeded):
    from app.db import SessionLocal
    from app.models import User
    from sqlalchemy import select
    await _set_user("anna", failed_logins=0, locked_until=None)
    status, body = await sync_login(password="wrong")
    assert status == 401 and body["detail"] == "Credenziali non valide"
    async with SessionLocal() as db:
        u = (await db.execute(select(User).where(User.username == "anna"))).scalar_one()
        assert u.failed_logins == 1
    await _set_user("anna", failed_logins=0)
    assert (await sync_login(email="kid@test.local", password="1234"))[0] == 401
    assert (await sync_login(email="nobody@test.local"))[0] == 401
    # account bloccato: 401 anche con la password giusta
    from datetime import datetime, timedelta, timezone
    await _set_user("anna", locked_until=datetime.now(timezone.utc) + timedelta(minutes=5))
    try:
        assert (await sync_login())[0] == 401
    finally:
        await _set_user("anna", locked_until=None)


async def test_login_is_rate_limited_per_ip_and_per_email(seeded):
    from app.api.sync import login_limiter
    login_limiter.hits.clear()
    async with make_client() as anon:
        codes = [(await anon.post("/api/sync/login", json={"email": "nobody@test.local", "password": "x"})).status_code
                 for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    # stessa email da un altro indirizzo: il conteggio per email la ferma comunque
    login_limiter.hits.pop("testclient", None)
    for k in list(login_limiter.hits):
        if not k.startswith("login:"):
            login_limiter.hits.pop(k)
    async with make_client() as anon:
        r = await anon.post("/api/sync/login", json={"email": "nobody@test.local", "password": "x"}, headers={"X-Real-IP": "10.9.9.9"})
    assert r.status_code == 429
    login_limiter.hits.clear()


async def test_login_requires_the_totp_code_when_enrolled(anna):
    import time
    from app.security import totp as totp_svc
    from app.services.settings_store import encrypt
    secret = totp_svc.new_secret()
    await _set_user("anna", totp_secret=encrypt(secret))
    try:
        status, body = await sync_login()
        assert status == 401 and body["detail"] == {"code": "totp_required", "message": "Inserisci il codice dell'app authenticator"}
        status, body = await sync_login(totp="000000")
        assert status == 401 and body["detail"]["code"] == "totp_invalid"
        code = totp_svc._code(secret, int(time.time() // totp_svc.PERIOD))
        status, body = await sync_login(totp=code, name="PC con TOTP")
        assert status == 200, body
        async with make_client(token=body["token"]) as dev:
            assert (await dev.get("/api/sync/me")).status_code == 200
    finally:
        await _set_user("anna", totp_secret=None)


async def test_login_refuses_passkey_only_accounts(bob):
    from app.db import SessionLocal
    from app.models import WebAuthnCredential
    bob_id = await _set_user("bob")
    async with SessionLocal() as db:
        cred = WebAuthnCredential(user_id=bob_id, credential_id=b"cred-bob", public_key=b"pk", name="Chiave di prova")
        db.add(cred)
        await db.commit()
        cred_id = cred.id
    try:
        status, body = await sync_login(email="bob@test.local")
        assert status == 403 and body["detail"]["code"] == "passkey_only"
        assert "Impostazioni" in body["detail"]["message"]
        # il codice di Impostazioni resta la via per chi ha solo la passkey
        r = await bob.post("/api/sync/devices/pairing-code")
        assert r.status_code == 200
        from app.api.sync import pair_limiter
        pair_limiter.hits.clear()
        async with make_client() as anon:
            r = await anon.post("/api/sync/pair", json={"code": r.json()["code"], "name": "PC Bob"})
        assert r.status_code == 200
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(WebAuthnCredential, cred_id))
            await db.commit()
