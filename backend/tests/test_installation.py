"""Installation branding, administrator-only setup and TLS validation."""
import io
from datetime import datetime, timedelta, timezone
from PIL import Image
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest
from app.api import branding, installation
from app.config import get_settings


@pytest.fixture
def isolated_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "directory", lambda: tmp_path / "branding")
    monkeypatch.setattr(installation, "directory", lambda: tmp_path / "installation")
    (tmp_path / "branding").mkdir()
    (tmp_path / "installation").mkdir()
    s = get_settings()
    for key in ("public_host", "webauthn_origin", "webauthn_rp_id", "webauthn_rp_name", "cookie_secure"):
        monkeypatch.setattr(s, key, getattr(s,key))
    return tmp_path


async def test_branding_upload_reencodes_and_updates_manifest(admin, isolated_installation):
    picture = io.BytesIO()
    Image.new("RGB", (80,60), "blue").save(picture,format="JPEG")
    response = await admin.post("/api/admin/branding", data={"name":"Casa Rossi"},
                               files={"logo":("family.jpg",picture.getvalue(),"image/jpeg")})
    assert response.status_code == 200
    assert response.json()["name"] == "Casa Rossi"
    icon = await admin.get(response.json()["logo_url"])
    assert Image.open(io.BytesIO(icon.content)).size == (512,512)
    manifest = await admin.get("/api/branding/manifest.webmanifest")
    assert manifest.json()["name"] == "Casa Rossi"
    assert (await admin.post("/api/admin/branding",data={"name":"x","reset":"true"})).json()["name"] == "FamilOS"


async def test_branding_rejects_active_content(admin, isolated_installation):
    response = await admin.post("/api/admin/branding",data={"name":"Casa"},
                  files={"logo":("logo.svg", b"<svg onload='alert(1)'/>","image/svg+xml")})
    assert response.status_code == 400
    assert branding.identity()["name"] == "FamilOS"


async def test_children_and_adults_cannot_manage_installation(kid, anna, isolated_installation):
    for client in (kid, anna):
        assert (await client.get("/api/admin/installation")).status_code == 403
        assert (await client.put("/api/admin/installation",json={"public_url":"https://example.com"})).status_code == 403
        assert (await client.get("/api/admin/installation/certificate/status")).status_code == 403
        assert (await client.post("/api/admin/branding",data={"name":"Changed"})).status_code == 403


async def test_domain_is_one_source_for_google_and_passkeys(admin, isolated_installation):
    response = await admin.put("/api/admin/installation", json={"public_url":"Casa.Example.com/",
                                "tls_mode":"acme","acme_email":"admin@example.com"})
    assert response.status_code == 200, response.text
    config = response.json()
    assert config["public_url"] == "https://casa.example.com"
    assert config["rp_id"] == "casa.example.com"
    from app.api.google_auth import redirect_uri
    assert redirect_uri() == "https://casa.example.com/api/auth/google/callback"
    assert get_settings().webauthn_rp_id == "casa.example.com"
    env = (isolated_installation / "installation/domain.env").read_text()
    assert "PUBLIC_HOST=casa.example.com" in env
    assert "ACME_CA=https://acme-v02.api.letsencrypt.org/directory" in env
    installation.apply_runtime()
    assert get_settings().webauthn_origin == config["public_url"]


@pytest.mark.parametrize("url",["http://example.com","https://name:secret@example.com","https://example.com/path","https://example.com?x=1","https://example.com/#bad","https://example.com\\nX=1"])
async def test_invalid_public_url_does_not_change_configuration(admin, isolated_installation, url):
    assert (await admin.put("/api/admin/installation",json={"public_url":url,"tls_mode":"internal"})).status_code == 400
    assert not (isolated_installation / "installation/config.json").exists()


def make_certificate(host):
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,host)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]),critical=False).sign(key,hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM),key.private_bytes(serialization.Encoding.PEM,
                       serialization.PrivateFormat.PKCS8,serialization.NoEncryption())


async def test_manual_certificate_matches_key_and_domain(admin, isolated_installation):
    cert, key = make_certificate("casa.example.com")
    def files(key_data=key):
        return {"certificate":("chain.pem",cert,"application/x-pem-file"),
                "private_key":("key.pem",key_data,"application/x-pem-file")}
    wrong_key = make_certificate("casa.example.com")[1]
    assert (await admin.post("/api/admin/installation/certificate",data={"public_url":"https://casa.example.com"},files=files(wrong_key))).status_code == 400
    assert (await admin.post("/api/admin/installation/certificate",data={"public_url":"https://another.example.com"},files=files())).status_code == 400
    response = await admin.post("/api/admin/installation/certificate",data={"public_url":"https://casa.example.com"},files=files())
    assert response.status_code == 200
    assert "private" not in response.text.lower()
    paths = list((isolated_installation / "installation/tls").glob("*/private-key.pem"))
    assert paths and paths[0].stat().st_mode & 0o777 == 0o600
    saved = await admin.put("/api/admin/installation",json={"public_url":"https://casa.example.com","tls_mode":"manual"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["manual_certificate"]["domains"] == ["casa.example.com"]
