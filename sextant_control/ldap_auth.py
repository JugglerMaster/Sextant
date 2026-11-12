from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any

from .config import LdapSettings


@dataclass(frozen=True)
class LdapIdentity:
    username: str
    display_name: str
    email: str
    role: str


def escape_filter_chars(value: str) -> str:
    """RFC 4515 escaping, independent of ldap3's helper."""
    replacements = {
        "\\": "\\5c",
        "*": "\\2a",
        "(": "\\28",
        ")": "\\29",
        "\x00": "\\00",
    }
    return "".join(replacements.get(char, char) for char in value)


def _attr(attributes: dict[str, Any], name: str) -> Any:
    for key, value in attributes.items():
        if key.casefold() == name.casefold():
            return value
    return None


def _values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _group_matches(member_dn: str, configured: tuple[str, ...]) -> bool:
    member = member_dn.strip().casefold()
    for group in configured:
        candidate = group.strip().casefold()
        if not candidate:
            continue
        if member == candidate or member.startswith(candidate + ","):
            return True
    return False


def role_for_groups(member_of: tuple[str, ...], settings: LdapSettings) -> str:
    if any(_group_matches(dn, settings.admin_groups) for dn in member_of):
        return "admin"
    if any(_group_matches(dn, settings.operator_groups) for dn in member_of):
        return "operator"
    if any(_group_matches(dn, settings.viewer_groups) for dn in member_of):
        return "viewer"
    if settings.default_role in {"admin", "operator", "viewer"}:
        return settings.default_role
    return "viewer"


def authenticate_ldap(
    username: str,
    password: str,
    settings: LdapSettings,
    ldap_module: Any | None = None,
) -> LdapIdentity | None:
    """Authenticate against AD/Samba/OpenLDAP with a service bind + user bind.

    Returns None for invalid credentials. Raises RuntimeError for directory
    connectivity/configuration errors so the API can distinguish an outage from
    a bad password (and log it safely without the password).
    """
    if not username or not password:
        return None

    if ldap_module is None:
        import ldap3 as ldap_module  # local import keeps tests/offline tools light

    try:
        server_kwargs: dict[str, Any] = {
            "port": settings.effective_port,
            "use_ssl": settings.use_ssl,
            "get_info": getattr(ldap_module, "NONE", None),
            "connect_timeout": settings.timeout,
        }
        if settings.use_ssl:
            # Always require a valid server certificate for LDAPS. With an
            # explicit CA bundle use only that bundle; otherwise validate
            # against the operating system trust store.
            tls_kwargs: dict[str, Any] = {"validate": ssl.CERT_REQUIRED}
            if settings.ca_cert:
                tls_kwargs["ca_certs_file"] = settings.ca_cert
            server_kwargs["tls"] = ldap_module.Tls(**tls_kwargs)
        server = ldap_module.Server(settings.server, **server_kwargs)
        service = ldap_module.Connection(
            server,
            user=settings.bind_dn,
            password=settings.bind_password,
            auto_bind=True,
            receive_timeout=settings.timeout,
        )
        if not getattr(service, "bound", False):
            raise RuntimeError("LDAP service bind failed")

        safe_username = escape_filter_chars(username)
        search_filter = settings.user_filter.replace("{username}", safe_username)
        attributes = [
            settings.display_name_attr,
            "cn",
            settings.email_attr,
            settings.username_attr,
            settings.group_attr,
        ]
        service.search(
            search_base=settings.base_dn,
            search_filter=search_filter,
            attributes=list(dict.fromkeys(attributes)),
            size_limit=2,
        )
        entries = list(getattr(service, "entries", []) or [])
        if len(entries) != 1:
            _safe_unbind(service)
            return None

        entry = entries[0]
        attributes = getattr(entry, "entry_attributes_as_dict", {}) or {}
        member_of = _values(_attr(attributes, settings.group_attr))
        display_name = (
            _values(_attr(attributes, settings.display_name_attr))
            or _values(_attr(attributes, "cn"))
            or (username,)
        )[0]
        email = (_values(_attr(attributes, settings.email_attr)) or ("",))[0]

        user_dn = getattr(entry, "entry_dn", None)
        if not user_dn:
            _safe_unbind(service)
            return None

        user_conn = ldap_module.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=False,
            receive_timeout=settings.timeout,
        )
        # auto_bind raises on a bad password; an explicit bind() normally
        # returns False. Connectivity/configuration failures still bubble up
        # as 503.
        try:
            user_bound = user_conn.bind()
        except Exception as exc:
            if "invalidcredential" in type(exc).__name__.casefold():
                user_bound = False
            else:
                raise
        if not user_bound:
            _safe_unbind(user_conn)
            _safe_unbind(service)
            return None
        _safe_unbind(user_conn)
        _safe_unbind(service)

        return LdapIdentity(
            username=username,
            display_name=display_name or username,
            email=email,
            role=role_for_groups(member_of, settings),
        )
    except RuntimeError:
        raise
    except Exception as exc:  # ldap3 raises several transport-specific types
        # Only the exception type is exposed. Do not chain the original
        # exception: a transport/library message must never be able to leak
        # the supplied password into a traceback or service log.
        raise RuntimeError(f"LDAP authentication failed: {type(exc).__name__}") from None


def probe_ldap(
    settings: LdapSettings,
    username: str = "",
    ldap_module: Any | None = None,
) -> dict[str, Any]:
    """Validate the service bind and optionally look up one user.

    This never accepts or returns a user password; it is an admin diagnostic
    for the saved directory configuration.
    """
    if ldap_module is None:
        try:
            import ldap3 as ldap_module
        except ImportError as exc:
            raise RuntimeError(
                "The optional ldap3 dependency is not installed. Install it with "
                "`pip install 'sextant[ldap]'`."
            ) from exc
    try:
        server_kwargs: dict[str, Any] = {
            "port": settings.effective_port,
            "use_ssl": settings.use_ssl,
            "get_info": getattr(ldap_module, "NONE", None),
            "connect_timeout": settings.timeout,
        }
        if settings.use_ssl:
            tls_kwargs: dict[str, Any] = {"validate": ssl.CERT_REQUIRED}
            if settings.ca_cert:
                tls_kwargs["ca_certs_file"] = settings.ca_cert
            server_kwargs["tls"] = ldap_module.Tls(**tls_kwargs)
        server = ldap_module.Server(settings.server, **server_kwargs)
        service = ldap_module.Connection(
            server,
            user=settings.bind_dn,
            password=settings.bind_password,
            auto_bind=True,
            receive_timeout=settings.timeout,
        )
        if not getattr(service, "bound", False):
            raise RuntimeError("LDAP service bind failed")
        if username:
            search_filter = settings.user_filter.replace(
                "{username}", escape_filter_chars(username)
            )
            attributes = [
                settings.display_name_attr,
                "cn",
                settings.email_attr,
                settings.username_attr,
                settings.group_attr,
            ]
        else:
            search_filter = "(objectClass=*)"
            attributes = [settings.username_attr]
        service.search(
            search_base=settings.base_dn,
            search_filter=search_filter,
            attributes=list(dict.fromkeys(attributes)),
            size_limit=5,
        )
        entries = list(getattr(service, "entries", []) or [])
        sample: dict[str, Any] = {}
        for entry in entries[:3]:
            values = getattr(entry, "entry_attributes_as_dict", {}) or {}
            sample[getattr(entry, "entry_dn", "") or "entry"] = {
                key: _values(value)[:3] for key, value in values.items()
            }
        _safe_unbind(service)
        return {
            "bound": True,
            "server": settings.server,
            "port": settings.effective_port,
            "base_dn": settings.base_dn,
            "entries": len(entries),
            "filter": search_filter,
            "sample": sample,
        }
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"LDAP probe failed: {type(exc).__name__}") from None


def _safe_unbind(connection: Any) -> None:
    try:
        connection.unbind()
    except Exception:
        pass
