"""Server certificate generation."""

import socket

from asyncua.crypto.uacrypto import check_certificate, load_certificate
from cryptography.x509.oid import NameOID

from e77sim.opcua.server import ensure_certificate


async def test_long_host_name(tmp_path, monkeypatch):
    # A Windows host name in a domain plus the machine name gives an application URI over 64 characters
    host = "nb203.workstations.example-company.local"
    monkeypatch.setattr(socket, "gethostname", lambda: host)
    name = "IMM_ZHAFIR_ZE1200_2026_0042"
    app_uri = f"urn:e77sim:{host}:{name}"
    assert len(app_uri) > 64

    key, cert_file = await ensure_certificate(tmp_path, app_uri, name)
    cert = await load_certificate(cert_file)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == name
    assert not check_certificate(cert, app_uri, host)

    # A valid certificate is reused, a changed URI regenerates it
    before = cert_file.read_bytes()
    await ensure_certificate(tmp_path, app_uri, name)
    assert cert_file.read_bytes() == before
    await ensure_certificate(tmp_path, app_uri + "x", name)
    assert cert_file.read_bytes() != before
