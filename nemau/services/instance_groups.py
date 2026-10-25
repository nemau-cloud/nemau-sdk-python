"""Instance groups: identical members from a versioned launch template, kept
between a minimum and a maximum, registered in load-balancer pools,
scaled by policies or a schedule.

The client checks the values it can know, mirroring the API's ranges. The API judges
every value again, against the stored group where a field is merged, and its answer wins.
"""

from __future__ import annotations

import builtins
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

from nemau.client import _unwrap
from nemau.models import InstanceGroup, InstanceGroupActivity, InstanceGroupDetail, TaskRef
from nemau.services import Service

MAX_SIZE = 50
MAX_NAME = 56
MAX_POOLS = 4
MAX_SECURITY_GROUPS = 5
MAX_POLICIES = 2
MAX_SCHEDULES = 10
MAX_USER_DATA_BYTES = 16 * 1024
MAX_KEY_NAME_BYTES = 128
MAX_CRON_LENGTH = 128
MAX_DESCRIPTION = 256
SERVICE_LEVELS = ("", "besteffort", "best-effort", "best effort", "99.9", "99.9%")

_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,-]{1,32}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

_TEMPLATE_FIELDS = frozenset(
    {
        "image_id",
        "flavor_id",
        "vcpus",
        "ram_mb",
        "disk_gb",
        "key_name",
        "service_level",
        "user_data",
    }
)
_POOL_FIELDS = frozenset({"pool_id", "protocol_port"})
_POLICY_FIELDS = frozenset(
    {
        "metric",
        "target",
        "window_seconds",
        "scale_out_cooldown_seconds",
        "scale_in_cooldown_seconds",
        "scale_in_window_seconds",
        "max_step_out",
        "max_step_in",
        "scale_in_enabled",
    }
)
_SCHEDULE_FIELDS = frozenset({"cron", "min_size", "max_size", "desired_capacity"})
#: (field, minimum, maximum) of each numeric policy field.
_POLICY_RANGES = (
    ("target", 10, 90),
    ("window_seconds", 180, 900),
    ("scale_out_cooldown_seconds", 60, 3600),
    ("scale_in_cooldown_seconds", 60, 3600),
    ("scale_in_window_seconds", 300, 3600),
    ("max_step_out", 1, 10),
    ("max_step_in", 1, 5),
)


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _refuse(message: str) -> ValueError:
    return ValueError(f"nemau: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _in_range(value: Any, low: int, high: int) -> bool:
    return _is_int(value) and low <= value <= high


def _only(mapping: Mapping[str, Any], allowed: frozenset[str], what: str) -> None:
    # The key is a field name the caller chose, never a value: it is safe to name.
    unknown = set(mapping) - allowed
    if unknown:
        raise _refuse(f"{what} holds a field the API does not know: {sorted(unknown)[0]}")


def _check_description(description: str | None) -> None:
    # Mirrors the API: at most 256 characters, on one line. The API judges it again.
    if description is None:
        return
    if len(description) > MAX_DESCRIPTION or _CONTROL.search(description):
        raise _refuse("description must be at most 256 characters, on one line")


def _check_size(field: str, value: Any) -> None:
    if value is not None and not _in_range(value, 0, MAX_SIZE):
        raise _refuse(f"{field} must be between 0 and {MAX_SIZE}")


def _require_size(field: str, value: Any) -> None:
    if not _in_range(value, 0, MAX_SIZE):
        raise _refuse(f"{field} must be between 0 and {MAX_SIZE}")


def _check_bounds(low: int, high: int, desired: int) -> None:
    if not (low <= desired <= high):
        raise _refuse(
            "min_size, desired_capacity and max_size must satisfy "
            "min_size <= desired_capacity <= max_size"
        )


def _check_security_groups(ids: Sequence[str] | None) -> None:
    if ids is None:
        return
    if len(ids) > MAX_SECURITY_GROUPS:
        raise _refuse(f"security_groups accepts at most {MAX_SECURITY_GROUPS} groups")
    if not all(_UUID.fullmatch(i) for i in ids):
        raise _refuse("security_groups must hold security group ids")


def _check_user_data(document: str) -> None:
    """The API's rules: size, no NUL, not blank, a recognised first line.

    The text is never echoed.
    """
    if len(document.encode("utf-8")) > MAX_USER_DATA_BYTES:
        raise _refuse("user_data must not exceed 16384 bytes")
    if "\x00" in document:
        raise _refuse("user_data must not contain NUL bytes")
    if not document.strip():
        raise _refuse("user_data must not be empty")
    first = document.split("\n", 1)[0].removesuffix("\r")
    if first == "#cloud-config" or first.startswith("#!"):
        return
    if re.match(r"^content-type: multipart/", first, re.IGNORECASE):
        return
    raise _refuse("user_data must start with #cloud-config, #! or a MIME multipart header")


def _check_template(template: Mapping[str, Any], create: bool) -> dict[str, Any]:
    _only(template, _TEMPLATE_FIELDS, "launch_template")
    image_id = template.get("image_id")
    if image_id is None and create:
        raise _refuse("launch_template.image_id is required")
    if image_id is not None and not _IMAGE_REF.fullmatch(str(image_id)):
        raise _refuse("launch_template.image_id is not a valid image id")
    flavor_id = template.get("flavor_id")
    if flavor_id is not None and not _IMAGE_REF.fullmatch(str(flavor_id)):
        raise _refuse("launch_template.flavor_id is not a valid flavor id")
    level = str(template.get("service_level") or "").strip().lower()
    if level not in SERVICE_LEVELS:
        raise _refuse("launch_template.service_level must be 99.9% or best-effort")
    key_name = template.get("key_name")
    if key_name is not None and (
        len(str(key_name).encode("utf-8")) > MAX_KEY_NAME_BYTES or _CONTROL.search(str(key_name))
    ):
        raise _refuse("launch_template.key_name is not valid")
    sized = any(template.get(k) is not None for k in ("vcpus", "ram_mb", "disk_gb"))
    if flavor_id is None and (create or sized):
        vcpus, ram_mb, disk_gb = (
            template.get("vcpus"),
            template.get("ram_mb"),
            template.get("disk_gb"),
        )
        if vcpus is None or ram_mb is None or disk_gb is None:
            raise _refuse("launch_template needs flavor_id or vcpus, ram_mb and disk_gb together")
        if not _in_range(vcpus, 1, 64):
            raise _refuse("launch_template.vcpus must be between 1 and 64")
        if not (_in_range(ram_mb, 1024, 262144) and ram_mb % 256 == 0):
            raise _refuse(
                "launch_template.ram_mb must be a multiple of 256 between 1024 and 262144"
            )
        if not _in_range(disk_gb, 10, 5000):
            raise _refuse("launch_template.disk_gb must be between 10 and 5000")
    if template.get("user_data") is not None:
        _check_user_data(str(template["user_data"]))
    return {k: v for k, v in template.items() if v is not None}


def _check_pools(pools: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    if pools is None:
        return None
    if len(pools) > MAX_POOLS:
        raise _refuse(f"load_balancer_pools accepts at most {MAX_POOLS} pools")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for pool in pools:
        _only(pool, _POOL_FIELDS, "load_balancer_pools")
        pool_id = str(pool.get("pool_id") or "")
        if not _UUID.fullmatch(pool_id):
            raise _refuse("load_balancer_pools pool_id must be a pool id")
        if not _in_range(pool.get("protocol_port"), 1, 65535):
            raise _refuse("load_balancer_pools protocol_port must be between 1 and 65535")
        if pool_id in seen:
            raise _refuse("load_balancer_pools lists a pool twice")
        seen.add(pool_id)
        out.append({"pool_id": pool_id, "protocol_port": pool["protocol_port"]})
    return out


def _check_policies(policies: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    if policies is None:
        return None
    if len(policies) > MAX_POLICIES:
        raise _refuse(f"scaling_policies accepts at most {MAX_POLICIES} policies")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for policy in policies:
        _only(policy, _POLICY_FIELDS, "scaling_policies")
        metric = policy.get("metric")
        if metric == "memory_percent":
            raise _refuse("the memory_percent metric is not available yet")
        if metric != "cpu_percent":
            raise _refuse("scaling_policies metric must be cpu_percent")
        if metric in seen:
            raise _refuse("scaling_policies lists a metric twice")
        seen.add(str(metric))
        if policy.get("target") is None:
            raise _refuse("scaling policy target is required")
        for field, low, high in _POLICY_RANGES:
            value = policy.get(field)
            if value is not None and not _in_range(value, low, high):
                raise _refuse(f"scaling policy {field} must be between {low} and {high}")
        if policy.get("scale_in_enabled") is not None and not isinstance(
            policy["scale_in_enabled"], bool
        ):
            raise _refuse("scaling policy scale_in_enabled must be true or false")
        out.append({k: v for k, v in policy.items() if v is not None})
    return out


def _check_health(health_check: str, grace: int | None, drain: int | None) -> None:
    if health_check == "load_balancer":
        raise _refuse("health_check load_balancer is not available yet")
    if health_check not in ("", "instance"):
        raise _refuse("health_check must be instance")
    if grace is not None and not _in_range(grace, 60, 1800):
        raise _refuse("health_check_grace_seconds must be between 60 and 1800")
    if drain is not None and not _in_range(drain, 0, 300):
        raise _refuse("drain_seconds must be between 0 and 300")


def _check_schedules(schedules: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    if schedules is None:
        return None
    if len(schedules) > MAX_SCHEDULES:
        raise _refuse(f"schedules accepts at most {MAX_SCHEDULES} actions")
    out: list[dict[str, Any]] = []
    for schedule in schedules:
        _only(schedule, _SCHEDULE_FIELDS, "schedules")
        cron = " ".join(str(schedule.get("cron") or "").split())
        fields = cron.split(" ")
        if (
            len(fields) != 5
            or len(cron) > MAX_CRON_LENGTH
            or not all(_CRON_FIELD.fullmatch(f) for f in fields)
        ):
            raise _refuse("schedules cron must be 5 fields, in UTC")
        low, high = schedule.get("min_size"), schedule.get("max_size")
        desired = schedule.get("desired_capacity")
        if low is None and high is None and desired is None:
            raise _refuse("a schedule sets at least one of min_size, max_size, desired_capacity")
        for value in (low, high, desired):
            if value is not None and not _in_range(value, 0, MAX_SIZE):
                raise _refuse(f"schedule sizes must be between 0 and {MAX_SIZE}")
        if low is not None and high is not None and low > high:
            raise _refuse("a schedule's min_size must not exceed its max_size")
        if low is not None and desired is not None and high is not None:
            _check_bounds(low, high, desired)
        out.append(
            {"cron": cron, **{k: v for k, v in schedule.items() if k != "cron" and v is not None}}
        )
    return out


class InstanceGroupService(Service):
    """Instance groups: create, read, change, scale, protect members, delete."""

    def list(self) -> builtins.list[InstanceGroup]:
        payload = self._client.request("GET", "/compute/instance-groups")
        return [InstanceGroup.from_dict(item) for item in _unwrap(payload, "instance_groups")]

    def get(self, group_id: str) -> InstanceGroupDetail:
        """The group with its members and its recent activities."""
        payload = self._client.request("GET", f"/compute/instance-groups/{_quote(group_id)}")
        return InstanceGroupDetail.from_dict((payload or {}).get("instance_group") or {})

    def activities(self, group_id: str) -> builtins.list[InstanceGroupActivity]:
        """The 200 most recent decisions of the group, newest first."""
        payload = self._client.request(
            "GET", f"/compute/instance-groups/{_quote(group_id)}/activities"
        )
        return [InstanceGroupActivity.from_dict(item) for item in _unwrap(payload, "activities")]

    def create(
        self,
        *,
        name: str,
        network_id: str,
        subnet_id: str,
        min_size: int,
        max_size: int,
        launch_template: Mapping[str, Any],
        description: str = "",
        desired_capacity: int | None = None,
        security_groups: Sequence[str] | None = None,
        load_balancer_pools: Sequence[Mapping[str, Any]] | None = None,
        health_check: str = "",
        health_check_grace_seconds: int | None = None,
        drain_seconds: int | None = None,
        scaling_policies: Sequence[Mapping[str, Any]] | None = None,
    ) -> TaskRef:
        """Queues the creation of a group.

        ``launch_template`` is a mapping with ``image_id`` and either ``flavor_id``
        or all of ``vcpus``, ``ram_mb`` and ``disk_gb``; it may also carry
        ``key_name``, ``service_level`` and ``user_data`` (cloud-init, at most 16 KiB).
        ``desired_capacity`` defaults to ``min_size`` on the server.
        """
        if len(name) > MAX_NAME or not _NAME.fullmatch(name):
            raise _refuse(
                "name must be 1 to 56 lowercase letters, digits or hyphens, "
                "starting and ending with a letter or digit"
            )
        if not _UUID.fullmatch(network_id):
            raise _refuse("network_id must be a network id")
        if not _UUID.fullmatch(subnet_id):
            raise _refuse("subnet_id must be a subnet id")
        _require_size("min_size", min_size)
        _require_size("max_size", max_size)
        _check_size("desired_capacity", desired_capacity)
        _check_bounds(
            min_size, max_size, min_size if desired_capacity is None else desired_capacity
        )
        _check_description(description)
        template = _check_template(launch_template, create=True)
        _check_security_groups(security_groups)
        pools = _check_pools(load_balancer_pools)
        _check_health(health_check, health_check_grace_seconds, drain_seconds)
        policies = _check_policies(scaling_policies)

        body: dict[str, Any] = {
            "name": name,
            "network_id": network_id,
            "subnet_id": subnet_id,
            "min_size": min_size,
            "max_size": max_size,
            "launch_template": template,
        }
        if description:
            body["description"] = description
        if desired_capacity is not None:
            body["desired_capacity"] = desired_capacity
        if security_groups is not None:
            body["security_groups"] = list(security_groups)
        if pools is not None:
            body["load_balancer_pools"] = pools
        if health_check:
            body["health_check"] = health_check
        if health_check_grace_seconds is not None:
            body["health_check_grace_seconds"] = health_check_grace_seconds
        if drain_seconds is not None:
            body["drain_seconds"] = drain_seconds
        if policies is not None:
            body["scaling_policies"] = policies
        return self._client.submit("POST", "/compute/instance-groups", json=body)

    def update(
        self,
        group_id: str,
        *,
        description: str | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        max_unavailable_pct: int | None = None,
        max_surge: int | None = None,
        security_groups: Sequence[str] | None = None,
        launch_template: Mapping[str, Any] | None = None,
        load_balancer_pools: Sequence[Mapping[str, Any]] | None = None,
        health_check: str = "",
        health_check_grace_seconds: int | None = None,
        drain_seconds: int | None = None,
        scaling_policies: Sequence[Mapping[str, Any]] | None = None,
        schedules: Sequence[Mapping[str, Any]] | None = None,
    ) -> TaskRef:
        """Changes the fields given.

        A ``launch_template`` is a new version. ``schedules`` replaces the old list,
        and ``[]`` clears it.
        """
        body: dict[str, Any] = {}
        if description is not None:
            _check_description(description)
            body["description"] = description
        _check_size("min_size", min_size)
        _check_size("max_size", max_size)
        if min_size is not None and max_size is not None and min_size > max_size:
            raise _refuse("min_size must not exceed max_size")
        if min_size is not None:
            body["min_size"] = min_size
        if max_size is not None:
            body["max_size"] = max_size
        if max_unavailable_pct is not None:
            if not _in_range(max_unavailable_pct, 0, 50):
                raise _refuse("max_unavailable_pct must be between 0 and 50")
            body["max_unavailable_pct"] = max_unavailable_pct
        if max_surge is not None:
            if not _in_range(max_surge, 1, 10):
                raise _refuse("max_surge must be between 1 and 10")
            body["max_surge"] = max_surge
        _check_security_groups(security_groups)
        if security_groups is not None:
            body["security_groups"] = list(security_groups)
        if launch_template is not None:
            body["launch_template"] = _check_template(launch_template, create=False)
        pools = _check_pools(load_balancer_pools)
        if pools is not None:
            body["load_balancer_pools"] = pools
        _check_health(health_check, health_check_grace_seconds, drain_seconds)
        if health_check:
            body["health_check"] = health_check
        if health_check_grace_seconds is not None:
            body["health_check_grace_seconds"] = health_check_grace_seconds
        if drain_seconds is not None:
            body["drain_seconds"] = drain_seconds
        policies = _check_policies(scaling_policies)
        if policies is not None:
            body["scaling_policies"] = policies
        checked = _check_schedules(schedules)
        if checked is not None:
            body["schedules"] = checked
        if not body:
            raise _refuse("instanceGroup.update was given no field to change")
        return self._client.submit(
            "PATCH", f"/compute/instance-groups/{_quote(group_id)}", json=body
        )

    def set_capacity(self, group_id: str, desired_capacity: int) -> TaskRef:
        """Sets the desired capacity, between the group's min and max."""
        _require_size("desired_capacity", desired_capacity)
        return self._client.submit(
            "PUT",
            f"/compute/instance-groups/{_quote(group_id)}/capacity",
            json={"desired_capacity": desired_capacity},
        )

    def refresh(
        self,
        group_id: str,
        *,
        max_unavailable_pct: int | None = None,
        max_surge: int | None = None,
    ) -> TaskRef:
        """Starts a rolling refresh: members are replaced one by one, within the shares given.

        Each replacement moves a member onto the group's current template
        version. A protected member is not replaced: it is counted in
        ``refresh.skipped_protected``. An absent share takes the group's value.
        """
        body: dict[str, Any] = {}
        if max_unavailable_pct is not None:
            if not _in_range(max_unavailable_pct, 0, 50):
                raise _refuse("max_unavailable_pct must be between 0 and 50")
            body["max_unavailable_pct"] = max_unavailable_pct
        if max_surge is not None:
            if not _in_range(max_surge, 1, 10):
                raise _refuse("max_surge must be between 1 and 10")
            body["max_surge"] = max_surge
        return self._client.submit(
            "POST", f"/compute/instance-groups/{_quote(group_id)}/refresh", json=body
        )

    def cancel_refresh(self, group_id: str) -> TaskRef:
        """Cancels the refresh in progress. The members already replaced stay on the new version."""
        return self._client.submit(
            "POST", f"/compute/instance-groups/{_quote(group_id)}/refresh/cancel"
        )

    def set_member_protection(self, group_id: str, instance_id: str, *, protected: bool) -> TaskRef:
        """Protects one member from scale-in, or lifts the protection.

        The member must belong to the group.
        """
        if not isinstance(protected, bool):
            raise _refuse("protected must be true or false")
        path = (
            f"/compute/instance-groups/{_quote(group_id)}/members/{_quote(instance_id)}/protection"
        )
        return self._client.submit("PUT", path, json={"protected": protected})

    def delete(self, group_id: str, *, delete_instances: bool = False) -> TaskRef:
        """Deletes the group. A group with members is refused unless ``delete_instances`` is set."""
        path = f"/compute/instance-groups/{_quote(group_id)}"
        if delete_instances:
            return self._client.submit("DELETE", path, query={"delete_instances": "true"})
        return self._client.submit("DELETE", path)
