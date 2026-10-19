"""/iam: the organization's users and their access keys, read-only.

These endpoints serve the web console. They require a console session
(:class:`~nemau.auth.SessionToken`) and their writes require a CSRF token, so
this service only reads. An access key or a bearer token is refused with 401.
"""

from __future__ import annotations

import urllib.parse

from nemau.client import _unwrap
from nemau.models import AccessKeyLimits, IamAccessKey, IamUserPage
from nemau.services import Service


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class IamService(Service):
    """Users and access keys."""

    def list_users(self, *, page: int = 1, limit: int = 20) -> IamUserPage:
        """One page of the organization's users (``limit`` 1 to 100). Owner only.

        This is ``/iam/users``. The older ``/account/iam/users`` is deprecated
        and goes away on 2027-04-10.
        """
        if page < 1:
            raise ValueError("nemau: page starts at 1")
        if limit < 1 or limit > 100:
            raise ValueError("nemau: limit must be between 1 and 100")
        payload = self._client.request("GET", "/iam/users", query={"page": page, "limit": limit})
        return IamUserPage.from_dict(payload or {})

    def list_access_keys(self, user_id: str) -> list[IamAccessKey]:
        """A user's access keys. Members see their own; owners see anyone's."""
        if not user_id:
            raise ValueError("nemau: user_id is required")
        payload = self._client.request("GET", f"/iam/users/{_quote(user_id)}/access-keys")
        return IamAccessKey.list_from(_unwrap(payload, "accessKeys"))

    def access_key_limits(self, user_id: str = "") -> AccessKeyLimits:
        """The cap on active access keys, and how many the user holds.

        Without ``user_id`` it is the caller's own count; naming someone else
        is for owners (403 otherwise). Creating or reactivating a key beyond
        the cap is refused with a 409 (``is_access_key_limit``).
        """
        payload = self._client.request(
            "GET", "/iam/access-keys/limits", query={"userId": user_id} if user_id else None
        )
        return AccessKeyLimits.from_dict(payload or {})
