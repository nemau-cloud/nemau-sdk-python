"""/kubernetes: managed clusters driven by Cluster API.

Clusters are addressed by name, not by id: the name of the Cluster API object
in the project's namespace.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from nemau.client import _unwrap
from nemau.models import (
    ClusterClass,
    KubernetesCluster,
    KubernetesClusterSummary,
    KubernetesPreflight,
    MaintenanceWindow,
    TaskRef,
)
from nemau.services import Service

#: What the API accepts as an upgrade target: ``1.31.0`` or ``v1.31.0``.
_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+$")


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _rfc3339(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KubernetesService(Service):
    """Managed Kubernetes."""

    def list_versions(self) -> list[str]:
        payload = self._client.request("GET", "/kubernetes/versions")
        return [str(item) for item in _unwrap(payload, "versions")]

    def list_cluster_classes(self) -> list[ClusterClass]:
        payload = self._client.request("GET", "/kubernetes/cluster-classes")
        return [ClusterClass.from_dict(item) for item in _unwrap(payload, "cluster_classes")]

    def list_clusters(self) -> list[KubernetesClusterSummary]:
        """The project's clusters, as summaries: the lowercase lifecycle status,
        and the version, phase and readiness as of the last reconcile in
        ``specs``. Use ``get_cluster`` for the topology and the conditions."""
        payload = self._client.request("GET", "/kubernetes/clusters")
        return [KubernetesClusterSummary.from_dict(item) for item in _unwrap(payload, "clusters")]

    def get_cluster(self, name: str) -> KubernetesCluster:
        payload = self._client.request("GET", f"/kubernetes/clusters/{_quote(name)}")
        return KubernetesCluster.from_dict(payload or {})

    def get_kubeconfig(self, name: str) -> str:
        """A cluster's kubeconfig, as YAML.

        Reading one is audited (GET_KUBECONFIG) and it carries cluster-admin
        rights: treat the result as a secret.
        """
        payload = self._client.request("GET", f"/kubernetes/clusters/{_quote(name)}/kubeconfig")
        return str((payload or {}).get("kubeconfig") or "")

    def create_cluster(
        self,
        *,
        name: str,
        kubernetes_version: str,
        control_plane_replicas: int,
        node_pools: Sequence[Mapping[str, Any]],
        control_plane_flavor_id: str = "",
        control_plane_vcpus: int = 0,
        control_plane_ram_mb: int = 0,
        control_plane_disk_gb: int = 0,
        worker_flavor_id: str = "",
        cluster_class: str = "",
    ) -> TaskRef:
        """Queue a cluster creation.

        The control-plane size is either ``control_plane_flavor_id`` or the
        three vcpus/ram/disk values; a partial size is rejected.

        ``control_plane_replicas`` (1 to 9; use an odd number, etcd needs a
        quorum) and at least one node pool are required: the API refuses a
        cluster without them. Each pool is a mapping with ``name`` (a DNS
        label, unique in the cluster) and ``replicas`` (0 to 100), plus
        optionally ``class``, ``flavor_id`` or ``vcpus``/``ram_mb``/``disk_gb``,
        ``autoscaler_min``/``autoscaler_max``, ``labels`` and ``taints``. A pool
        without a size takes ``worker_flavor_id``.

        The console's defaults are 3 control-plane nodes and one pool
        ``{"name": "workers", "class": "default-worker", "replicas": 3,
        "vcpus": 2, "ram_mb": 4096, "disk_gb": 20}``.
        """
        body: dict[str, Any] = {
            "name": name,
            "kubernetes_version": kubernetes_version,
            "control_plane_replicas": control_plane_replicas,
            "node_pools": [dict(pool) for pool in node_pools],
        }
        if control_plane_flavor_id:
            body["control_plane_flavor_id"] = control_plane_flavor_id
        if control_plane_vcpus:
            body["control_plane_vcpus"] = control_plane_vcpus
        if control_plane_ram_mb:
            body["control_plane_ram_mb"] = control_plane_ram_mb
        if control_plane_disk_gb:
            body["control_plane_disk_gb"] = control_plane_disk_gb
        if worker_flavor_id:
            body["worker_flavor_id"] = worker_flavor_id
        if cluster_class:
            body["cluster_class"] = cluster_class
        return self._client.submit("POST", "/kubernetes/clusters", json=body)

    def create_cluster_and_wait(self, **kwargs: Any) -> KubernetesCluster:
        """Provisioning takes several minutes."""
        ref = self.create_cluster(**kwargs)
        self._client.wait_for(ref)
        # By name, not by task id: every other endpoint on a cluster is
        # name-addressed, so the name is the reusable handle.
        return self.get_cluster(str(kwargs["name"]))

    def delete_cluster(self, name: str) -> TaskRef:
        return self._client.submit("DELETE", f"/kubernetes/clusters/{_quote(name)}")

    def scale_control_plane(self, name: str, replicas: int) -> TaskRef:
        """Use an odd number: etcd needs a quorum."""
        return self._client.submit(
            "POST",
            f"/kubernetes/clusters/{_quote(name)}/scale-control-plane",
            json={"replicas": replicas},
        )

    def add_node_pool(
        self,
        cluster: str,
        *,
        pool_name: str,
        node_class: str,
        flavor_id: str = "",
        vcpus: int = 0,
        ram_mb: int = 0,
        disk_gb: int = 0,
        replicas: int = 0,
        autoscaler_min: int = 0,
        autoscaler_max: int = 0,
    ) -> TaskRef:
        """``node_class`` is the node class the ClusterClass defines.

        The size is either ``flavor_id`` (an organization flavor, which then
        sets the node size) or the three vcpus/ram/disk values.
        """
        body: dict[str, Any] = {"pool_name": pool_name, "class": node_class}
        if flavor_id:
            body["flavor_id"] = flavor_id
        if vcpus:
            body["vcpus"] = vcpus
        if ram_mb:
            body["ram_mb"] = ram_mb
        if disk_gb:
            body["disk_gb"] = disk_gb
        if replicas:
            body["replicas"] = replicas
        if autoscaler_min:
            body["autoscaler_min"] = autoscaler_min
        if autoscaler_max:
            body["autoscaler_max"] = autoscaler_max
        return self._client.submit(
            "POST", f"/kubernetes/clusters/{_quote(cluster)}/node-pools", json=body
        )

    def remove_node_pool(self, cluster: str, pool: str) -> TaskRef:
        return self._client.submit(
            "DELETE", f"/kubernetes/clusters/{_quote(cluster)}/node-pools/{_quote(pool)}"
        )

    def scale_node_pool(self, cluster: str, pool: str, replicas: int) -> TaskRef:
        return self._client.submit(
            "POST",
            f"/kubernetes/clusters/{_quote(cluster)}/node-pools/{_quote(pool)}/scale",
            json={"replicas": replicas},
        )

    def set_autoscaler(self, cluster: str, pool: str, minimum: int, maximum: int) -> TaskRef:
        """Both bounds are required: either 0 < minimum <= maximum, or
        minimum = maximum = 0 to turn the autoscaler off."""
        return self._client.submit(
            "POST",
            f"/kubernetes/clusters/{_quote(cluster)}/node-pools/{_quote(pool)}/autoscaler",
            json={"min": minimum, "max": maximum},
        )

    def upgrade_cluster(self, name: str, version: str) -> TaskRef:
        """Queue a guided upgrade of ``name`` to ``version``.

        ``version`` is ``1.31.0`` or ``v1.31.0``. A single task runs the
        preflight checks, opens a maintenance window and starts the upgrade.
        If a preflight check blocks, the request is still accepted and the task
        fails, with the blocking checks listed in its message. When the task
        completes, the rolling upgrade has started but not finished: follow it
        with ``get_cluster``. The window's id is in the task's result as
        ``maintenance_window_id``.

        Pass an ``Idempotency-Key`` (see ``NemauClient.idempotency_key``) to
        make the call safe to repeat. Requires the kubernetes-guided-upgrades
        organization feature (404 without it).
        """
        if not name:
            raise ValueError("nemau: a cluster name is required")
        if not _VERSION_RE.fullmatch(version or ""):
            raise ValueError('nemau: version must look like "1.31.0" or "v1.31.0"')
        return self._client.submit(
            "POST", f"/kubernetes/clusters/{_quote(name)}/upgrade", json={"version": version}
        )

    def preflight(self, name: str, target_version: str = "") -> KubernetesPreflight:
        """Pre-upgrade health checks. Without a target version it checks the
        cluster's health only. Requires the kubernetes-health-preflight
        feature."""
        body: dict[str, Any] = {"target_version": target_version} if target_version else {}
        payload = self._client.request(
            "POST", f"/kubernetes/clusters/{_quote(name)}/preflight", json=body
        )
        return KubernetesPreflight.from_dict(payload if isinstance(payload, dict) else {})

    def create_maintenance_window(
        self,
        cluster: str,
        *,
        name: str,
        starts_at: datetime | str,
        ends_at: datetime | str,
        reason: str = "",
    ) -> MaintenanceWindow:
        """Open a maintenance window.

        ``name`` must match ``^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$``; the window must end
        after it starts and last at most 30 days.
        """
        payload = self._client.request(
            "POST",
            f"/kubernetes/clusters/{_quote(cluster)}/maintenance",
            json={
                "name": name,
                "starts_at": _rfc3339(starts_at),
                "ends_at": _rfc3339(ends_at),
                "reason": reason,
            },
        )
        return MaintenanceWindow.from_dict(payload if isinstance(payload, dict) else {})

    def rotate_kubeconfig(self, name: str) -> dict[str, Any]:
        """Requires the kubernetes-kubeconfig-rotation feature."""
        payload = self._client.request(
            "POST", f"/kubernetes/clusters/{_quote(name)}/kubeconfig/rotate"
        )
        return payload if isinstance(payload, dict) else {}
