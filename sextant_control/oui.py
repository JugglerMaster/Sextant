"""Backward-compatible import location for the bundled OUI table."""

from sextant_common.oui import (  # noqa: F401
    OUI_VENDORS,
    is_locally_administered,
    lookup_vendor,
    normalize_mac,
)

__all__ = ["OUI_VENDORS", "is_locally_administered", "lookup_vendor", "normalize_mac"]
