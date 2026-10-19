"""Credentials accepted by the Nemau Cloud API.

An IAM access key, a service-account token, or a console session token.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

ENV_ACCESS_KEY_ID = "NEMAU_ACCESS_KEY_ID"
ENV_ACCESS_KEY_SECRET = "NEMAU_ACCESS_KEY_SECRET"
ENV_TOKEN = "NEMAU_TOKEN"
ENV_API_URL = "NEMAU_API_URL"
ENV_PROJECT = "NEMAU_PROJECT_ID"


@runtime_checkable
class Credential(Protocol):
    """Sets the authentication headers on an outgoing request."""

    def apply(self, headers: dict[str, str]) -> None: ...

    def describe(self) -> str:
        """Redacted identity, for diagnostics. Never reveals the secret."""
        ...


class AccessKey:
    """An IAM user's long-lived programmatic credential.

    Long-lived and not subject to CSRF checks, so it is the credential to use
    in scripts and CI jobs. Without a selected project, it acts on the owning
    user's active project.
    """

    __slots__ = ("id", "_secret")

    def __init__(self, key_id: str, secret: str) -> None:
        self.id = key_id
        self._secret = secret

    def apply(self, headers: dict[str, str]) -> None:
        headers["X-Access-Key-Id"] = self.id
        headers["X-Access-Key-Secret"] = self._secret

    def describe(self) -> str:
        return f"access-key {self.id}"

    def __repr__(self) -> str:
        # Keeps the secret out of tracebacks and debug logs.
        return f"AccessKey(id={self.id!r}, secret=***)"


class BearerToken:
    """A service-account token.

    Obtained from ``POST /api/v1/iam/service-accounts/token``. It carries the
    account's pinned project and expires after 15 minutes. This class holds a
    fixed token and does not renew it: a long-running process must obtain a
    new token and build a new credential.
    """

    __slots__ = ("_token",)

    def __init__(self, token: str) -> None:
        self._token = token

    def apply(self, headers: dict[str, str]) -> None:
        headers["Authorization"] = f"Bearer {self._token}"

    def describe(self) -> str:
        return "service-account token"

    def __repr__(self) -> str:
        return "BearerToken(token=***)"


class SessionToken:
    """A human console session.

    Writes require a CSRF token, so in practice this credential is read-only.
    """

    __slots__ = ("_token",)

    def __init__(self, token: str) -> None:
        self._token = token

    def apply(self, headers: dict[str, str]) -> None:
        headers["x-session-token"] = self._token

    def describe(self) -> str:
        return "console session"

    def __repr__(self) -> str:
        return "SessionToken(token=***)"


def credential_from_env(env: Mapping[str, str] | None = None) -> Credential | None:
    """Builds a credential from the environment, or returns ``None``.

    When both are set, the access-key pair takes precedence over
    ``NEMAU_TOKEN``, so a stale token left in a shell does not replace the
    long-lived key.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    key_id = source.get(ENV_ACCESS_KEY_ID, "").strip()
    secret = source.get(ENV_ACCESS_KEY_SECRET, "").strip()
    if key_id and secret:
        return AccessKey(key_id, secret)

    token = source.get(ENV_TOKEN, "").strip()
    if token:
        return BearerToken(token)

    return None
