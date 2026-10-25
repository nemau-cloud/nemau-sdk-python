"""/compute: instances, lifecycle, snapshots and restore, images, flavors, keypairs."""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from typing import Any

from nemau.client import _unwrap
from nemau.models import (
    CreatedKeypair,
    Flavor,
    Image,
    Instance,
    Keypair,
    Snapshot,
    Task,
    TaskRef,
)
from nemau.services import Service
from nemau.services.update import patch


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


#: The windows the API serves metrics for.
METRIC_PERIODS = ("1h", "6h", "24h", "7d")

#: The scheduled-backup policies an instance can carry.
BACKUP_SCHEDULES = ("none", "daily", "weekly")

#: The longest a scheduled backup can be kept, in days.
MAX_BACKUP_RETENTION_DAYS = 35


def _check_int(name: str, value: Any, low: int, high: int) -> None:
    """Refuses a value that is not an int in ``low``..``high``.

    ``bool`` is an ``int`` subclass in Python, so ``True`` is refused explicitly:
    a caller passing a flag by mistake gets an error, not hour 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"nemau: {name} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"nemau: {name} must be between {low} and {high}")


#: Largest ``user_data`` the API accepts, in UTF-8 bytes.
USER_DATA_MAX_BYTES = 16 * 1024


def _check_user_data(user_data: object) -> None:
    """Client-side comfort check, run before any request. The API is the
    authority; the error never includes the document itself."""
    if not isinstance(user_data, str):
        raise ValueError("nemau: user_data must be a string")
    if "\x00" in user_data:
        raise ValueError("nemau: user_data must not contain NUL")
    try:
        size = len(user_data.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError(
            "nemau: user_data must be well-formed Unicode (no lone surrogates)"
        ) from None
    if size > USER_DATA_MAX_BYTES:
        raise ValueError(f"nemau: user_data is {size} bytes; the limit is {USER_DATA_MAX_BYTES}")


class ComputeService(Service):
    """Compute instances and their catalogue."""

    # -- instances --------------------------------------------------- #

    def list_instances(self) -> list[Instance]:
        payload = self._client.request("GET", "/compute/instances")
        return [Instance.from_dict(item) for item in _unwrap(payload, "instances")]

    def get_instance(self, instance_id: str) -> Instance:
        """Raises a 404 :class:`~nemau.errors.NemauApiError` when the id is
        unknown to the project."""
        payload = self._client.request("GET", f"/compute/instances/{_quote(instance_id)}")
        return Instance.from_dict((payload or {}).get("instance") or {})

    def create_instance(
        self,
        *,
        name: str,
        image_id: str,
        vcpus: int = 0,
        ram_mb: int = 0,
        disk_gb: int = 0,
        flavor_id: str = "",
        service_level: str = "",
        description: str = "",
        network_id: str = "",
        security_groups: Sequence[str] | None = None,
        key_name: str = "",
        user_data: str | None = None,
    ) -> TaskRef:
        """Queues the creation of an instance.

        The size is either ``flavor_id`` or all three of
        ``vcpus``/``ram_mb``/``disk_gb``: the API rejects a partial custom size.
        ``flavor_id`` additionally requires the compute-flavors organization
        feature.

        ``user_data`` is a cloud-init document, at most
        :data:`USER_DATA_MAX_BYTES` UTF-8 bytes, without NUL. It is sent only
        when given (``""`` is sent as an empty document), and it is checked
        before the request goes out.
        """
        body: dict[str, Any] = {"name": name, "image_id": image_id}
        if description:
            body["description"] = description
        if flavor_id:
            body["flavor_id"] = flavor_id
        if vcpus:
            body["vcpus"] = vcpus
        if ram_mb:
            body["ram_mb"] = ram_mb
        if disk_gb:
            body["disk_gb"] = disk_gb
        if service_level:
            body["service_level"] = service_level
        if network_id:
            body["network_id"] = network_id
        if security_groups:
            body["security_groups"] = list(security_groups)
        if key_name:
            body["key_name"] = key_name
        if user_data is not None:
            _check_user_data(user_data)
            body["user_data"] = user_data
        return self._client.submit("POST", "/compute/instances", json=body)

    def create_instance_and_wait(self, **kwargs: Any) -> Instance:
        """Creates an instance and waits for it to be built."""
        ref = self.create_instance(**kwargs)
        instance_id, _ = self._client.wait_for_resource_id(ref, "instance_id")
        return self.get_instance(instance_id)

    def delete_instance(self, instance_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/compute/instances/{_quote(instance_id)}")

    def start(self, instance_id: str) -> TaskRef:
        return self._action(instance_id, "start")

    def stop(self, instance_id: str) -> TaskRef:
        return self._action(instance_id, "stop")

    def reboot(self, instance_id: str, reboot_type: str = "") -> TaskRef:
        """``reboot_type`` is ``SOFT`` (the default) or ``HARD``."""
        return self._action(instance_id, "reboot", {"type": reboot_type} if reboot_type else None)

    def pause(self, instance_id: str) -> TaskRef:
        return self._action(instance_id, "pause")

    def unpause(self, instance_id: str) -> TaskRef:
        return self._action(instance_id, "unpause")

    def suspend(self, instance_id: str) -> TaskRef:
        return self._action(instance_id, "suspend")

    def resume(self, instance_id: str) -> TaskRef:
        return self._action(instance_id, "resume")

    def resize(self, instance_id: str, *, vcpus: int, ram_mb: int, disk_gb: int) -> TaskRef:
        """All three values are required and bounded by the API:
        1-64 vCPUs, 1024-262144 MB, 10-5000 GB."""
        return self._action(
            instance_id, "resize", {"vcpus": vcpus, "ram_mb": ram_mb, "disk_gb": disk_gb}
        )

    def set_security_groups(self, instance_id: str, group_ids: Sequence[str]) -> TaskRef:
        """Replaces an instance's security groups with ``group_ids``.

        An empty list is accepted: it removes all filtering from the instance
        without deleting it.
        """
        return self._action(
            instance_id, "security-groups", {"security_groups": list(group_ids or [])}
        )

    def _action(self, instance_id: str, action: str, body: Any = None) -> TaskRef:
        """Every lifecycle action is a PUT."""
        return self._client.submit(
            "PUT", f"/compute/instances/{_quote(instance_id)}/{action}", json=body
        )

    def instance_metrics(self, instance_id: str, period: str = "") -> dict[str, Any]:
        """``period`` is 1h (the default), 6h, 24h or 7d; anything else raises
        ``ValueError`` here, where the server would answer 400."""
        if period and period not in METRIC_PERIODS:
            raise ValueError(f"nemau: period must be one of {', '.join(METRIC_PERIODS)}")
        payload = self._client.request(
            "GET",
            f"/compute/instances/{_quote(instance_id)}/metrics",
            query={"period": period} if period else None,
        )
        return payload if isinstance(payload, dict) else {}

    # -- snapshots --------------------------------------------------- #

    def list_snapshots(self, instance_id: str = "") -> list[Snapshot]:
        payload = self._client.request(
            "GET", "/compute/snapshots", query={"instance_id": instance_id} if instance_id else None
        )
        return [Snapshot.from_dict(item) for item in _unwrap(payload, "snapshots")]

    def create_snapshot(self, instance_id: str, name: str) -> TaskRef:
        """Queues a snapshot of the instance's system disk.

        The task keeps running until the upload to the object store ends
        (minutes, or hours for a large disk), and the snapshot is listed as
        ``creating`` until then.
        """
        return self._client.submit(
            "POST", f"/compute/instances/{_quote(instance_id)}/snapshot", json={"name": name}
        )

    def delete_snapshot(self, snapshot_id: str) -> TaskRef:
        """A snapshot being created or restored cannot be deleted (409)."""
        return self._client.submit("DELETE", f"/compute/snapshots/{_quote(snapshot_id)}")

    def restore_snapshot(self, snapshot_id: str) -> TaskRef:
        """Restores a snapshot onto the instance it was taken from.

        The data is restored beside the running instance first, then its system
        disk is swapped: the instance stops only for the swap and restarts if it
        was running. Everything written since the snapshot is lost; addresses,
        security groups and attached volumes are kept, and the disk keeps its
        current size. Only snapshots with ``restorable`` set can be restored.
        """
        return self._client.submit("POST", f"/compute/snapshots/{_quote(snapshot_id)}/restore")

    # -- catalogue --------------------------------------------------- #

    def list_images(self) -> list[Image]:
        payload = self._client.request("GET", "/compute/images")
        return [Image.from_dict(item) for item in _unwrap(payload, "images")]

    def list_flavors(self) -> list[Flavor]:
        """Requires the compute-flavors organization feature, else 403."""
        payload = self._client.request("GET", "/compute/flavors")
        return [Flavor.from_dict(item) for item in _unwrap(payload, "flavors")]

    def create_flavor(
        self, *, name: str, vcpus: int, ram_mb: int, disk_gb: int, service_level: str = ""
    ) -> Flavor:
        """Synchronous, unlike most writes."""
        body: dict[str, Any] = {
            "name": name,
            "vcpus": vcpus,
            "ram_mb": ram_mb,
            "disk_gb": disk_gb,
        }
        if service_level:
            body["service_level"] = service_level
        payload = self._client.request("POST", "/compute/flavors", json=body)
        return Flavor.from_dict((payload or {}).get("flavor") or {})

    def update_flavor(
        self,
        flavor_id: str,
        *,
        name: str,
        vcpus: int,
        ram_mb: int,
        disk_gb: int,
        service_level: str = "",
    ) -> Flavor:
        body: dict[str, Any] = {
            "name": name,
            "vcpus": vcpus,
            "ram_mb": ram_mb,
            "disk_gb": disk_gb,
        }
        if service_level:
            body["service_level"] = service_level
        payload = self._client.request("PATCH", f"/compute/flavors/{_quote(flavor_id)}", json=body)
        return Flavor.from_dict((payload or {}).get("flavor") or {})

    def delete_flavor(self, flavor_id: str) -> None:
        self._client.request("DELETE", f"/compute/flavors/{_quote(flavor_id)}")

    def get_pricing(self) -> dict[str, Any]:
        payload = self._client.request("GET", "/compute/pricing")
        pricing = (payload or {}).get("pricing")
        return pricing if isinstance(pricing, dict) else {}

    # -- keypairs ------------------------------------------------------ #

    def list_keypairs(self) -> list[Keypair]:
        payload = self._client.request("GET", "/compute/keypairs")
        return [Keypair.from_dict(item) for item in _unwrap(payload, "keypairs")]

    def create_keypair(self, name: str, public_key: str = "") -> CreatedKeypair:
        """Registers a public key, or generates one when ``public_key`` is empty.

        Synchronous: the response carries the result instead of a task
        reference, because a generated private key is returned only once.
        """
        body: dict[str, Any] = {"name": name}
        if public_key:
            body["public_key"] = public_key
        payload = self._client.request("POST", "/compute/keypairs", json=body)
        task = Task.from_dict(payload or {})
        return CreatedKeypair(
            name=name,
            public_key=task.result_str("public_key"),
            private_key=task.result_str("private_key"),
            task=task,
        )

    def delete_keypair(self, name: str) -> TaskRef:
        """Deletes a keypair by name, not by id.

        A name containing a slash cannot be addressed here because the API
        routes on the decoded path, so such a keypair can be created but not
        deleted.
        """
        return self._client.submit("DELETE", f"/compute/keypairs/{_quote(name)}")

    def update_instance(
        self,
        instance_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        backup_schedule: str | None = None,
        backup_hour: int | None = None,
        backup_weekday: int | None = None,
        backup_retention_days: int | None = None,
    ) -> TaskRef:
        """Changes an instance's name, description or scheduled-backup policy.

        The instance keeps running: the name is only a label, and nothing
        addresses the instance by it.

        Scheduled backups (UTC):

        - ``backup_schedule``: ``"none"``, ``"daily"`` or ``"weekly"``.
        - ``backup_hour``: 0 to 23, when the backup starts. The server default is 2.
        - ``backup_weekday``: 0 (Sunday) to 6 (Saturday). Read only for
          ``"weekly"``. The server default is 0.
        - ``backup_retention_days``: 1 to 35. The server requires it whenever the
          schedule, after this update, is ``"daily"`` or ``"weekly"``. The check
          runs on the merged state, so it is the server that refuses a schedule
          set without a retention, not this method.

        Each value is checked here, before any request, and an omitted argument
        is left out of the request.
        """
        if backup_schedule is not None and backup_schedule not in BACKUP_SCHEDULES:
            raise ValueError(f"nemau: backup_schedule must be one of {', '.join(BACKUP_SCHEDULES)}")
        if backup_hour is not None:
            _check_int("backup_hour", backup_hour, 0, 23)
        if backup_weekday is not None:
            _check_int("backup_weekday", backup_weekday, 0, 6)
        if backup_retention_days is not None:
            _check_int("backup_retention_days", backup_retention_days, 1, MAX_BACKUP_RETENTION_DAYS)
        return patch(
            self._client,
            "update_instance",
            f"/compute/instances/{_quote(instance_id)}",
            {
                "name": name,
                "description": description,
                "backup_schedule": backup_schedule,
                "backup_hour": backup_hour,
                "backup_weekday": backup_weekday,
                "backup_retention_days": backup_retention_days,
            },
        )


__all__ = ["ComputeService"]
