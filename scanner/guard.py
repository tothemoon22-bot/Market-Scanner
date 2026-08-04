"""Startup assertion: this process must never be able to authenticate.

The scanner reads public market data. It holds no credentials, calls no
authenticated endpoint, and places no orders. This module makes that
non-negotiable at runtime rather than by convention: if anything that looks like
a Kalshi credential is present in the environment, the process exits non-zero
before it opens a socket.

That is deliberately stricter than "do not use the credential". A credential
that is merely unused is one refactor away from being used.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

# Names that would let a process authenticate to Kalshi. Presence of any of
# these -- even empty-but-set is fine, only a non-empty value is fatal -- means
# this box is provisioned for something other than read-only scanning.
CREDENTIAL_ENV_NAMES = (
    "KALSHI_KEY_ID",
    "KALSHI_API_KEY",
    "KALSHI_API_SECRET",
    "KALSHI_PRIVATE_KEY",
    "KALSHI_PRIVATE_KEY_PATH",
    "KALSHI_ACCESS_KEY",
    "KALSHI_PASSWORD",
    "KALSHI_EMAIL",
)


def find_credentials(env: Mapping[str, str] | None = None) -> list[str]:
    """Names of credential-shaped variables that are set and non-empty."""
    source = os.environ if env is None else env
    return sorted(name for name in CREDENTIAL_ENV_NAMES if source.get(name, "").strip())


def assert_no_credentials(env: Mapping[str, str] | None = None) -> None:
    """Exit non-zero if this process could authenticate to Kalshi."""
    found = find_credentials(env)
    if not found:
        return
    print(
        "FATAL: Kalshi credentials present in the environment: "
        + ", ".join(found)
        + "\nThe scanner is read-only by construction and must not run on a host "
        "provisioned to trade.\nUnset them, or run the scanner somewhere else.",
        file=sys.stderr,
    )
    raise SystemExit(2)
