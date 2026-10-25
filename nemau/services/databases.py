"""Managed PostgreSQL: create, read, change, resize, restore, rotate, delete.

The client checks the values it can know, mirroring the API's ranges. The API
judges every value again, and its answer wins. Flavors and versions are not
checked here: the region's offer is the API's to know.

A create, a password rotation and a restore answer with the password once.
Those three calls send no ``Idempotency-Key``, which the API refuses on them:
a repeat call makes a new password, so read the database back or rotate
instead of retrying.
"""

from __future__ import annotations

import builtins
import re
import urllib.parse
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from nemau.client import _unwrap
from nemau.models import Database, DatabaseCredentials, DatabaseFlavor, TaskRef
from nemau.services import Service

STORAGE_MIN_GB = 10
STORAGE_MAX_GB = 2000
RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 35
MAX_SECURITY_GROUPS = 5
MAX_DESCRIPTION_BYTES = 256
APPLY_AT_VALUES = ("now", "maintenance_window")

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ADMIN_USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
#: Unicode control characters (Cc): C0, DEL and C1.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class _Unset:
    """Marks an argument left out, so that ``None`` can mean "clear" where it applies."""

    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _refuse(message: str) -> ValueError:
    return ValueError(f"nemau: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        raise _refuse("database name must match ^[a-z][a-z0-9-]{0,62}$")


def _check_description(description: str) -> None:
    if len(description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        raise _refuse("database description is too long")
    if _CONTROL.search(description):
        raise _refuse("database description holds no control characters")


def _check_storage(gb: Any) -> None:
    if not _is_int(gb) or not STORAGE_MIN_GB <= gb <= STORAGE_MAX_GB:
        raise _refuse(f"storage_gb must be between {STORAGE_MIN_GB} and {STORAGE_MAX_GB}")


def _check_retention(days: Any) -> None:
    if not _is_int(days) or not RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS:
        raise _refuse(
            f"backup_retention_days must be between {RETENTION_MIN_DAYS} and {RETENTION_MAX_DAYS}"
        )


def _check_window(window: Any) -> dict[str, int]:
    if not isinstance(window, dict) or set(window) != {"weekday", "hour"}:
        raise _refuse("maintenance_window needs weekday and hour, and nothing else")
    weekday, hour = window["weekday"], window["hour"]
    if not (_is_int(weekday) and 0 <= weekday <= 6 and _is_int(hour) and 0 <= hour <= 23):
        raise _refuse("maintenance_window needs weekday 0-6 and hour 0-23 (UTC)")
    return {"weekday": weekday, "hour": hour}


def _check_subnet(subnet_id: str) -> None:
    if not _UUID.fullmatch(subnet_id):
        raise _refuse("subnet_id must be a UUID")


def _check_security_groups(ids: Sequence[str] | None) -> None:
    if ids is None:
        return
    if len(ids) > MAX_SECURITY_GROUPS:
        raise _refuse(f"security_group_ids accepts at most {MAX_SECURITY_GROUPS} groups")
    if not all(_UUID.fullmatch(i) for i in ids):
        raise _refuse("security_group_ids must hold UUIDs")


def _check_admin_username(name: str) -> None:
    if not _ADMIN_USERNAME.fullmatch(name):
        raise _refuse("admin_username must match ^[a-z][a-z0-9_]{2,31}$")
    if name == "postgres" or name.startswith("pg_") or name.startswith("nemau"):
        raise _refuse("admin_username is reserved")


def _restore_time(target: datetime | str) -> str:
    """The restore point, as RFC 3339 in UTC; refused when not a timestamp or in the future."""
    if isinstance(target, datetime):
        if target.tzinfo is None:
            raise _refuse("target_time must carry a timezone")
        moment = target.astimezone(timezone.utc)
    else:
        if not _RFC3339.fullmatch(target):
            raise _refuse("target_time must be an RFC 3339 timestamp")
        try:
            # Go accepts any count of fractional digits; Python 3.9 takes 3 or 6 only.
            text = re.sub(r"(\.\d{1,6})\d*", lambda m: m.group(1).ljust(7, "0"), target)
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _refuse("target_time must be an RFC 3339 timestamp") from exc
        moment = moment.astimezone(timezone.utc)
    if moment > datetime.now(timezone.utc):
        raise _refuse("target_time must not be in the future")
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _issued(payload: Any) -> DatabaseCredentials:
    """The 202 that issues a password. Its error names no part of the answer."""
    credentials = DatabaseCredentials.from_dict(payload or {})
    if not credentials.password:
        raise RuntimeError("nemau: the API answered without a database password")
    return credentials


class DatabaseService(Service):
    """Managed PostgreSQL databases."""

    def list(self) -> builtins.list[Database]:
        payload = self._client.request("GET", "/databases")
        return [Database.from_dict(item) for item in _unwrap(payload, "databases")]

    def get(self, database_id: str) -> Database:
        return Database.from_dict(
            self._client.request("GET", f"/databases/{_quote(database_id)}") or {}
        )

    def ca_certificate(self) -> str:
        """The PEM of the region's database CA: the ``sslrootcert`` of ``sslmode=verify-full``."""
        payload = self._client.request("GET", "/databases/ca-certificate")
        return str((payload or {}).get("ca_certificate_pem") or "")

    def flavors(self) -> builtins.list[DatabaseFlavor]:
        """The sizes the region offers. Pass one of their ``name`` values as ``flavor``."""
        payload = self._client.request("GET", "/database-flavors")
        return [DatabaseFlavor.from_dict(item) for item in _unwrap(payload, "flavors")]

    def versions(self) -> builtins.list[str]:
        """The PostgreSQL majors the region admits, such as ``"17"``."""
        payload = self._client.request("GET", "/database-versions")
        return [str(item) for item in _unwrap(payload, "versions")]

    def create(
        self,
        *,
        name: str,
        version: str,
        flavor: str,
        storage_gb: int,
        subnet_id: str,
        admin_username: str,
        description: str = "",
        security_group_ids: Sequence[str] | None = None,
        backup_retention_days: int | None = None,
        maintenance_window: dict[str, int] | None = None,
    ) -> DatabaseCredentials:
        """Queues the creation.

        The returned password is the administrator's: store it now, it is not readable again.
        """
        _check_name(name)
        _check_description(description)
        _check_storage(storage_gb)
        _check_subnet(subnet_id)
        _check_security_groups(security_group_ids)
        _check_admin_username(admin_username)
        body: dict[str, Any] = {
            "name": name,
            "version": version,
            "flavor": flavor,
            "storage_gb": storage_gb,
            "subnet_id": subnet_id,
            "admin_username": admin_username,
        }
        if description:
            body["description"] = description
        if security_group_ids is not None:
            body["security_group_ids"] = list(security_group_ids)
        if backup_retention_days is not None:
            _check_retention(backup_retention_days)
            body["backup_retention_days"] = backup_retention_days
        if maintenance_window is not None:
            body["maintenance_window"] = _check_window(maintenance_window)
        return _issued(self._client.request("POST", "/databases", json=body))

    def update(
        self,
        database_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        backup_retention_days: int | None = None,
        maintenance_window: dict[str, int] | None | _Unset = _UNSET,
    ) -> TaskRef:
        """Changes the fields given.

        ``None`` leaves a field unchanged, except ``maintenance_window``: there,
        ``None`` clears the window, and leaving it out keeps the window as it is.
        """
        body: dict[str, Any] = {}
        if name is not None:
            _check_name(name)
            body["name"] = name
        if description is not None:
            _check_description(description)
            body["description"] = description
        if backup_retention_days is not None:
            _check_retention(backup_retention_days)
            body["backup_retention_days"] = backup_retention_days
        if maintenance_window is None:
            body["maintenance_window"] = None
        elif not isinstance(maintenance_window, _Unset):
            body["maintenance_window"] = _check_window(maintenance_window)
        if not body:
            raise _refuse("database.update was given no field to change")
        return self._client.submit("PATCH", f"/databases/{_quote(database_id)}", json=body)

    def restart(self, database_id: str) -> TaskRef:
        """Queues a reboot of the appliance.

        The database stays active, and the restart is not billed.
        """
        return self._client.submit("POST", f"/databases/{_quote(database_id)}/restart")

    def resize(
        self,
        database_id: str,
        *,
        flavor: str = "",
        storage_gb: int | None = None,
        apply_at: str = "",
    ) -> TaskRef:
        """Moves to a larger flavor, or grows the storage.

        Set ``flavor``, ``storage_gb``, or both.
        """
        if not flavor and storage_gb is None:
            raise _refuse("database.resize needs a flavor, a storage_gb, or both")
        body: dict[str, Any] = {}
        if flavor:
            body["flavor"] = flavor
        if storage_gb is not None:
            _check_storage(storage_gb)
            body["storage_gb"] = storage_gb
        if apply_at:
            if apply_at not in APPLY_AT_VALUES:
                raise _refuse("apply_at must be now or maintenance_window")
            body["apply_at"] = apply_at
        return self._client.submit("POST", f"/databases/{_quote(database_id)}/resize", json=body)

    def rotate_password(self, database_id: str) -> DatabaseCredentials:
        """Issues a new administrator password.

        The old one stays valid until the task applies the new one.
        """
        return _issued(
            self._client.request("POST", f"/databases/{_quote(database_id)}/rotate-password")
        )

    def restore(
        self,
        source_id: str,
        *,
        name: str,
        target_time: datetime | str,
        flavor: str,
        storage_gb: int,
        subnet_id: str,
        admin_username: str,
        description: str = "",
        security_group_ids: Sequence[str] | None = None,
    ) -> DatabaseCredentials:
        """Restores the source at ``target_time`` into a NEW database.

        The password of the new database is returned once.

        The source is never changed. ``target_time`` is an aware ``datetime``,
        or an RFC 3339 string, and must not be in the future.
        """
        restore_at = _restore_time(target_time)
        _check_name(name)
        _check_description(description)
        _check_storage(storage_gb)
        _check_subnet(subnet_id)
        _check_security_groups(security_group_ids)
        _check_admin_username(admin_username)
        body: dict[str, Any] = {
            "name": name,
            "target_time": restore_at,
            "flavor": flavor,
            "storage_gb": storage_gb,
            "subnet_id": subnet_id,
            "admin_username": admin_username,
        }
        if description:
            body["description"] = description
        if security_group_ids is not None:
            body["security_group_ids"] = list(security_group_ids)
        return _issued(
            self._client.request("POST", f"/databases/{_quote(source_id)}/restore", json=body)
        )

    def delete(self, database_id: str, *, purge_backups: bool = False) -> TaskRef:
        """Deletes the database.

        Its backups survive for their retention unless ``purge_backups`` is set.
        """
        path = f"/databases/{_quote(database_id)}"
        if purge_backups:
            return self._client.submit("DELETE", path, query={"purge_backups": "true"})
        return self._client.submit("DELETE", path)
