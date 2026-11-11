from __future__ import annotations

import ssl
import time

import pytest
from starlette.requests import Request

import runner_control.security as security
from runner_control.config import LdapSettings
from runner_control.ldap_auth import LdapIdentity, authenticate_ldap, escape_filter_chars, role_for_groups
from runner_control.security import (
    MAX_SESSION_PAYLOAD_BYTES,
    MAX_SESSION_TOKEN_LENGTH,
    LoginRateLimiter,
    client_ip,
    decode_session,
    hash_password,
    hash_token,
    make_session_payload,
    sign_session,
    token_matches,
    validate_password,
    verify_password,
)
from runner_control.wol import build_magic_packet, normalize_mac, send_magic_packet


def test_password_hashing_and_verification():
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong password", stored)
    assert not verify_password("", stored)
    assert not verify_password("anything", "garbage")
    assert hash_password("same password", salt=b"0" * 16) == hash_password("same password", salt=b"0" * 16)


def test_password_policy():
    assert validate_password("short") is not None
    assert validate_password("correct horse battery staple") is None


def test_session_signing_and_tampering():
    secret = b"a" * 64
    payload = make_session_payload(7, 2, 60, "ses_test")
    value = sign_session(payload, secret)
    decoded = decode_session(value, secret)
    assert decoded["uid"] == 7
    assert decoded["sid"] == "ses_test"
    assert decoded["csrf"] == payload["csrf"]

    assert decode_session(value, b"b" * 64) is None
    body, _signature = value.rsplit(".", 1)
    assert decode_session(body + ".AAAA", secret) is None
    assert decode_session("not a token", secret) is None

    expired = make_session_payload(7, 2, -1, "ses_expired")
    assert decode_session(sign_session(expired, secret), secret) is None
    no_sid = make_session_payload(7, 2, 60, "ses_test")
    no_sid.pop("sid")
    assert decode_session(sign_session(no_sid, secret), secret) is None


def test_token_hash_and_match():
    token = "rc_agent_secret"
    digest = hash_token(token)
    assert token_matches(token, digest)
    assert not token_matches(token + "x", digest)
    assert digest == hash_token(token)


def test_magic_packet():
    packet = build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff")
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    with pytest.raises(ValueError):
        normalize_mac("nope")


def test_send_magic_packet_uses_broadcast():
    class FakeSocket:
        def __init__(self):
            self.options = []
            self.sent = []
            self.closed = False

        def setsockopt(self, *args):
            self.options.append(args)

        def sendto(self, payload, address):
            self.sent.append((payload, address))

        def close(self):
            self.closed = True

    sock = FakeSocket()
    count = send_magic_packet("AA:BB:CC:DD:EE:FF", "192.168.1.255", 9, 3, sock_factory=lambda: sock)
    assert count == 3
    assert len(sock.sent) == 3
    assert sock.sent[0][1] == ("192.168.1.255", 9)
    assert sock.options
    assert sock.closed


def test_ldap_escaping_and_role_mapping():
    assert escape_filter_chars("a*b(c)\\d\x00") == "a\\2ab\\28c\\29\\5cd\\00"
    settings = LdapSettings(
        server="dc.example.com",
        base_dn="DC=example,DC=com",
        bind_dn="CN=svc,DC=example,DC=com",
        bind_password="secret",
        admin_groups=("CN=Ops Admins,OU=Groups,DC=example,DC=com",),
        operator_groups=("CN=Ops,OU=Groups,DC=example,DC=com",),
        default_role="viewer",
    )
    assert role_for_groups(("CN=Ops Admins,OU=Groups,DC=example,DC=com",), settings) == "admin"
    assert role_for_groups(("cn=ops,ou=groups,dc=example,dc=com",), settings) == "operator"
    assert role_for_groups(("CN=Other,OU=Groups,DC=example,DC=com",), settings) == "viewer"


class _Entry:
    def __init__(self, dn: str, attrs: dict):
        self.entry_dn = dn
        self.entry_attributes_as_dict = attrs


class _FakeConnection:
    searches: list[dict] = []
    user_password = "right-password"
    entries: list[_Entry] = []
    bound = True

    def __init__(self, server, user=None, password=None, auto_bind=False, receive_timeout=None):
        self.server = server
        self.user = user
        self.password = password
        self.bound = True
        if user and user.startswith("CN=User"):
            self.bound = password == self.user_password

    def search(self, **kwargs):
        type(self).searches.append(kwargs)
        self.entries = type(self).entries
        return True

    def bind(self):
        return self.bound

    def unbind(self):
        return True


class _FakeServer:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakeTls:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).calls.append(kwargs)


class _FakeLdap:
    NONE = None
    Server = _FakeServer
    Connection = _FakeConnection
    Tls = _FakeTls


def test_ldap_authentication_service_bind_search_and_user_bind():
    _FakeConnection.searches.clear()
    _FakeConnection.entries = [
        _Entry(
            "CN=User One,OU=People,DC=example,DC=com",
            {
                "displayName": "User One",
                "mail": "user@example.com",
                "memberOf": ["CN=Ops Admins,OU=Groups,DC=example,DC=com"],
            },
        )
    ]
    settings = LdapSettings(
        server="dc.example.com",
        base_dn="DC=example,DC=com",
        bind_dn="CN=svc,DC=example,DC=com",
        bind_password="secret",
        admin_groups=("CN=Ops Admins,OU=Groups,DC=example,DC=com",),
        default_role="viewer",
    )
    identity = authenticate_ldap("user*)(uid=*)", "right-password", settings, ldap_module=_FakeLdap)
    assert isinstance(identity, LdapIdentity)
    assert identity.username == "user*)(uid=*)"
    assert identity.role == "admin"
    assert identity.display_name == "User One"
    assert identity.email == "user@example.com"
    assert "\\2a" in _FakeConnection.searches[0]["search_filter"]
    assert "\\28" in _FakeConnection.searches[0]["search_filter"]

    assert authenticate_ldap("user", "wrong-password", settings, ldap_module=_FakeLdap) is None


# --- Password policy -------------------------------------------------------


def test_password_policy_rejects_common_and_trivial_values():
    for password in (
        "password1234",
        "passwordpassword",
        "password!2026",
        "changeme1234",
        "administrator",
        "letmein12345",
        "qwertyuiop12",
        "runnercontrol",
        "runnercontrol123",
        "aaaaaaaaaaaa",
        "abcabcabcabc",
        "123456789012",
        "abcdefghijkl",
        "qwertyuiopas",
        "111111111111",
    ):
        assert validate_password(password) is not None, password
    assert validate_password("correct horse battery staple") is None
    assert validate_password("viewer password 123") is None
    assert validate_password("brand new session password") is None


# --- Session decoding ------------------------------------------------------


def test_decode_session_rejects_malformed_claims():
    secret = b"c" * 64
    now = int(time.time())

    def signed(**overrides):
        payload = make_session_payload(1, 1, 60, "ses_claim")
        payload.update(overrides)
        return sign_session(payload, secret)

    # Small clock skew is tolerated; a far-future issue time is not.
    assert decode_session(signed(iat=now + 60, exp=now + 120), secret) is not None
    assert decode_session(signed(iat=now + 1000, exp=now + 1060), secret) is None

    # iat/exp must be real integers, not floats or bools.
    assert decode_session(signed(iat=float(now), exp=now + 60), secret) is None
    assert decode_session(signed(iat=now, exp=float(now + 60)), secret) is None
    assert decode_session(signed(iat=True, exp=now + 60), secret) is None
    assert decode_session(signed(exp=True), secret) is None

    # Lifetime must be positive and not absurdly long.
    zero_lifetime = make_session_payload(1, 1, 60, "ses_claim")
    zero_lifetime["exp"] = zero_lifetime["iat"]
    assert decode_session(sign_session(zero_lifetime, secret), secret) is None

    negative_lifetime = make_session_payload(1, 1, 60, "ses_claim")
    negative_lifetime["exp"] = negative_lifetime["iat"] - 1
    assert decode_session(sign_session(negative_lifetime, secret), secret) is None

    absurd_lifetime = make_session_payload(1, 1, 60, "ses_claim")
    absurd_lifetime["exp"] = absurd_lifetime["iat"] + 10 * 365 * 24 * 3600
    assert decode_session(sign_session(absurd_lifetime, secret), secret) is None

    # Encoded token cap.
    huge_token = make_session_payload(1, 1, 60, "ses_claim")
    huge_token["sid"] = "x" * (MAX_SESSION_TOKEN_LENGTH + 100)
    assert decode_session(sign_session(huge_token, secret), secret) is None

    # Decoded payload cap (fits under the encoded cap but expands past it).
    huge_payload = make_session_payload(1, 1, 60, "ses_claim")
    huge_payload["sid"] = "x" * (MAX_SESSION_PAYLOAD_BYTES + 100)
    encoded = sign_session(huge_payload, secret)
    assert len(encoded) <= MAX_SESSION_TOKEN_LENGTH
    assert decode_session(encoded, secret) is None

    # A normally issued cookie still decodes.
    valid = make_session_payload(1, 1, 60, "ses_valid")
    assert decode_session(sign_session(valid, secret), secret)["sid"] == "ses_valid"


# --- Client IP -------------------------------------------------------------


def _request(forwarded: str | None = None, client: tuple[str, int] | None = ("10.0.0.5", 12345)) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode("latin-1"))]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": client,
            "server": ("testserver", 80),
        }
    )


def test_client_ip_only_trusts_valid_forwarded_addresses():
    peer = "10.0.0.5"
    assert client_ip(_request("203.0.113.9"), trust_proxy=True) == "203.0.113.9"
    # The rightmost hop is the one appended by the nearest trusted proxy; a
    # client-supplied leftmost value must not win.
    assert client_ip(_request("203.0.113.9, 10.0.0.1"), trust_proxy=True) == "10.0.0.1"
    assert client_ip(_request("198.51.100.7, 203.0.113.9"), trust_proxy=True) == "203.0.113.9"
    assert client_ip(_request("203.0.113.9:8443"), trust_proxy=True) == "203.0.113.9"
    assert client_ip(_request("2001:db8::1"), trust_proxy=True) == "2001:db8::1"
    assert client_ip(_request("[2001:db8::1]:443"), trust_proxy=True) == "2001:db8::1"

    assert client_ip(_request("203.0.113.9"), trust_proxy=False) == peer
    assert client_ip(_request("garbage"), trust_proxy=True) == peer
    assert client_ip(_request(""), trust_proxy=True) == peer
    assert client_ip(_request("not-an-ip, 203.0.113.9"), trust_proxy=True) == "203.0.113.9"
    assert client_ip(_request("not-an-ip, also-bad"), trust_proxy=True) == peer
    assert client_ip(_request(client=None)) == "unknown"


# --- Login rate limiter ----------------------------------------------------


def _fake_clock(monkeypatch, start: float = 1000.0) -> list[float]:
    clock = [start]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    return clock


def test_login_rate_limiter_locks_and_unlocks(monkeypatch):
    clock = _fake_clock(monkeypatch)
    limiter = LoginRateLimiter(attempts=3, window_seconds=60, lock_seconds=30, sweep_interval=1)
    key = "192.0.2.1:admin"

    assert limiter.retry_after(key) == 0
    limiter.record_failure(key)
    limiter.record_failure(key)
    assert limiter.retry_after(key) == 0
    limiter.record_failure(key)
    assert limiter.retry_after(key) > 0
    assert key in limiter._locked_until

    clock[0] += 31
    assert limiter.retry_after(key) == 0
    assert key not in limiter._locked_until
    assert key not in limiter._failures


def test_login_rate_limiter_prunes_expired_entries(monkeypatch):
    clock = _fake_clock(monkeypatch)
    limiter = LoginRateLimiter(attempts=5, window_seconds=30, lock_seconds=30, max_keys=100, sweep_interval=5)
    limiter.record_failure("old:user")
    assert "old:user" in limiter._failures

    clock[0] += 100
    limiter.record_failure("new:user")
    assert "old:user" not in limiter._failures
    assert "old:user" not in limiter._last_seen
    assert "new:user" in limiter._failures


def test_login_rate_limiter_bounds_tracked_keys(monkeypatch):
    clock = _fake_clock(monkeypatch)
    limiter = LoginRateLimiter(
        attempts=5,
        window_seconds=300,
        lock_seconds=300,
        max_keys=5,
        sweep_interval=10_000,
    )
    for index in range(50):
        clock[0] += 1
        limiter.record_failure(f"198.51.100.{index}:user")

    assert len(limiter._failures) + len(limiter._locked_until) <= 5
    assert len(limiter._last_seen) <= 5
    assert "198.51.100.49:user" in limiter._failures
    assert "198.51.100.0:user" not in limiter._failures


def test_login_rate_limiter_success_clears_state():
    limiter = LoginRateLimiter(attempts=3, window_seconds=60, lock_seconds=30)
    key = "192.0.2.2:admin"
    limiter.record_failure(key)
    limiter.record_failure(key)
    assert key in limiter._failures
    limiter.record_success(key)
    assert key not in limiter._failures
    assert key not in limiter._locked_until
    assert key not in limiter._last_seen
    assert limiter.retry_after(key) == 0


# --- LDAP TLS and transport errors ----------------------------------------


def _ldap_settings(**overrides) -> LdapSettings:
    values = {
        "server": "dc.example.com",
        "base_dn": "DC=example,DC=com",
        "bind_dn": "CN=svc,DC=example,DC=com",
        "bind_password": "secret",
        "admin_groups": ("CN=Ops Admins,OU=Groups,DC=example,DC=com",),
        "default_role": "viewer",
    }
    values.update(overrides)
    return LdapSettings(**values)


def test_ldap_tls_requires_system_ca_validation_by_default():
    _FakeTls.calls.clear()
    _FakeConnection.entries = []
    settings = _ldap_settings(use_ssl=True)
    assert authenticate_ldap("user", "right-password", settings, ldap_module=_FakeLdap) is None
    assert _FakeTls.calls
    assert _FakeTls.calls[0]["validate"] == ssl.CERT_REQUIRED
    assert "ca_certs_file" not in _FakeTls.calls[0]


def test_ldap_tls_uses_explicit_ca_bundle():
    _FakeTls.calls.clear()
    _FakeConnection.entries = []
    settings = _ldap_settings(use_ssl=True, ca_cert="/etc/ssl/certs/private-ca.pem")
    assert authenticate_ldap("user", "right-password", settings, ldap_module=_FakeLdap) is None
    assert _FakeTls.calls[0]["validate"] == ssl.CERT_REQUIRED
    assert _FakeTls.calls[0]["ca_certs_file"] == "/etc/ssl/certs/private-ca.pem"


def test_ldap_filter_escaping_covers_rfc4515_metacharacters():
    assert escape_filter_chars("\x00*()\\") == "\\00\\2a\\28\\29\\5c"
    assert escape_filter_chars("\\2a") == "\\5c2a"

    _FakeConnection.searches.clear()
    _FakeConnection.entries = []
    settings = _ldap_settings()
    assert authenticate_ldap("evil*)(uid=*", "irrelevant", settings, ldap_module=_FakeLdap) is None
    assert _FakeConnection.searches[0]["search_filter"] == "(sAMAccountName=evil\\2a\\29\\28uid=\\2a)"


class _OutageConnection:
    def __init__(self, *args, **kwargs):
        raise ConnectionError("directory unavailable")


class _OutageLdap:
    NONE = None
    Server = _FakeServer
    Connection = _OutageConnection
    Tls = _FakeTls


def test_ldap_transport_error_raises_runtime_error_without_password():
    password = "Unique-Transport-Password-42"
    with pytest.raises(RuntimeError) as error:
        authenticate_ldap("user", password, _ldap_settings(), ldap_module=_OutageLdap)
    assert "ConnectionError" in str(error.value)
    assert password not in str(error.value)
    assert error.value.__cause__ is None
