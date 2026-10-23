"""OPC UA server setup: NodeSets, security (OPC 40077, chapter 6), users and roles."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

from asyncua import Server, ua
from asyncua.crypto.cert_gen import dump_private_key_as_pem, generate_private_key, generate_self_signed_app_certificate
from asyncua.crypto.uacrypto import check_certificate, load_certificate, load_private_key
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from asyncua.crypto.permission_rules import PermissionRuleset, User, UserRole
from asyncua.server.user_managers import UserManager
from cryptography.x509.oid import ExtendedKeyUsageOID

from .. import __version__
from ..resources import nodeset_dir

log = logging.getLogger(__name__)

NODESETS = [
    "Opc.Ua.Di.NodeSet2.xml",
    "Opc.Ua.PlasticsRubber.GeneralTypes.NodeSet2.xml",
    "Opc.Ua.PlasticsRubber.IMM2MES.NodeSet2.xml",
]
NS_DI = "http://opcfoundation.org/UA/DI/"
NS_GT = "http://opcfoundation.org/UA/PlasticsRubber/GeneralTypes/"
NS_IMM = "http://opcfoundation.org/UA/PlasticsRubber/IMM2MES/"

PROFILE_URIS = {
    "basic": "http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/Basic",
    "jobs": "http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/Jobs",
    "datasets": "http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/ProductionDatasetManagement",
}

USER_RW = "OPC40077"
USER_RO = "OPC40077_read_only"

_WRITE_REQUESTS = {
    ua.NodeId(ua.ObjectIds.WriteRequest_Encoding_DefaultBinary),
    ua.NodeId(ua.ObjectIds.CallRequest_Encoding_DefaultBinary),
    ua.NodeId(ua.ObjectIds.HistoryUpdateRequest_Encoding_DefaultBinary),
}
_ADMIN_REQUESTS = {
    ua.NodeId(ua.ObjectIds.AddNodesRequest_Encoding_DefaultBinary),
    ua.NodeId(ua.ObjectIds.DeleteNodesRequest_Encoding_DefaultBinary),
    ua.NodeId(ua.ObjectIds.AddReferencesRequest_Encoding_DefaultBinary),
    ua.NodeId(ua.ObjectIds.DeleteReferencesRequest_Encoding_DefaultBinary),
    ua.NodeId(ua.ObjectIds.RegisterServerRequest_Encoding_DefaultBinary),
    ua.NodeId(ua.ObjectIds.RegisterServer2Request_Encoding_DefaultBinary),
}


class E77Users(UserManager):
    """Users from OPC 40077 chapter 6. Passwords are chosen by the manufacturer and may be empty;
    an empty configured password accepts any password."""

    def __init__(self, passwords: dict[str, str], allow_anonymous: bool):
        self.passwords = passwords
        self.allow_anonymous = allow_anonymous

    def get_user(self, iserver, username=None, password=None, certificate=None):
        if username is None and certificate is None:
            return User(role=UserRole.User, name="anonymous") if self.allow_anonymous else None
        if certificate is not None and username is None:
            return User(role=UserRole.User, name="certificate")
        if username not in (USER_RW, USER_RO):
            return None
        expected = self.passwords.get(username, "")
        if expected and expected != (password or ""):
            return None
        return User(role=UserRole.User, name=username)


class E77Ruleset(PermissionRuleset):
    """OPC40077 may write and call methods, OPC40077_read_only only reads, browses and subscribes."""

    def check_validity(self, user, action_type_id, body):
        if action_type_id in _ADMIN_REQUESTS:
            return user is not None and user.role == UserRole.Admin
        if action_type_id in _WRITE_REQUESTS and user is not None and user.name == USER_RO:
            return False
        return True


# X.509 limits CommonName to 64 characters (RFC 5280 ub-common-name)
CN_MAX = 64


async def ensure_certificate(pki: Path, app_uri: str, name: str) -> tuple[Path, Path]:
    """Self-signed certificate, regenerated when missing or when the URI or host name changed.

    asyncua's setup_self_signed_certificate puts the application URI into the CommonName,
    which fails for long host names, so the CN carries the machine name instead.
    The application URI stays in subjectAltName as OPC 10000-6 6.2.2 requires.
    """
    pki.mkdir(parents=True, exist_ok=True)
    key_file, cert_file = pki / f"{name}.pem", pki / f"{name}.der"
    host = socket.gethostname()
    if key_file.is_file():
        key = await load_private_key(key_file)
        if cert_file.is_file() and not check_certificate(await load_certificate(cert_file), app_uri, host):
            return key_file, cert_file
    else:
        key = generate_private_key()
        key_file.write_bytes(dump_private_key_as_pem(key))
    cert = generate_self_signed_app_certificate(
        key,
        name[:CN_MAX],
        {"countryName": "CZ", "organizationName": "Euromap 77 Simulator"},
        [x509.UniformResourceIdentifier(app_uri), x509.DNSName(host)],
        extended=[ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH],
        days=365,
    )
    cert_file.write_bytes(cert.public_bytes(encoding=Encoding.DER))
    return key_file, cert_file


async def create_server(
    name: str,
    host: str,
    port: int,
    pki: Path,
    facets: list[str],
    passwords: dict[str, str] | None = None,
    allow_anonymous: bool = True,
    security: bool = True,
) -> Server:
    server = Server(user_manager=E77Users(passwords or {}, allow_anonymous))
    await server.init()
    server.set_endpoint(f"opc.tcp://{host}:{port}/")
    server.set_server_name(f"Euromap 77 Simulator - {name}")
    app_uri = f"urn:e77sim:{socket.gethostname()}:{name}"
    await server.set_application_uri(app_uri)
    await server.set_build_info(
        product_uri="urn:e77sim",
        manufacturer_name="Euromap 77 Simulator",
        product_name="Euromap 77 Simulator",
        software_version=__version__,
        build_number=__version__,
        build_date=_build_date(),
    )
    policies = [ua.SecurityPolicyType.NoSecurity]
    if security:
        key, cert = await ensure_certificate(pki, app_uri, name)
        await server.load_certificate(str(cert))
        await server.load_private_key(str(key))
        policies += [
            ua.SecurityPolicyType.Basic256_Sign,
            ua.SecurityPolicyType.Basic256_SignAndEncrypt,
            ua.SecurityPolicyType.Basic256Sha256_Sign,
            ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
        ]
    server.set_security_policy(policies, permission_ruleset=E77Ruleset())
    tokens = [ua.UserNameIdentityToken, ua.X509IdentityToken]
    if allow_anonymous:
        tokens.insert(0, ua.AnonymousIdentityToken)
    server.set_identity_tokens(tokens)

    for f in NODESETS:
        await server.import_xml(str(nodeset_dir() / f))

    profiles = [PROFILE_URIS["basic"]] + [PROFILE_URIS[f] for f in facets if f in PROFILE_URIS]
    spa = server.get_node(ua.ObjectIds.Server_ServerCapabilities_ServerProfileArray)
    current = await spa.read_value() or []
    await spa.write_value(ua.Variant(list(current) + profiles, ua.VariantType.String))
    return server


def _build_date():
    from datetime import datetime, timezone

    return datetime(2026, 10, 23, tzinfo=timezone.utc)
