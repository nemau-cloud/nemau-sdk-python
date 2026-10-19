# Kubernetes

`client.kubernetes` manages Kubernetes clusters and their node pools. Clusters
are addressed by name: every method takes the cluster name.

## Versions and cluster classes

```python
print(client.kubernetes.list_versions())

for cluster_class in client.kubernetes.list_cluster_classes():
    print(cluster_class.name, cluster_class.namespace)
```

## Creating a cluster

A cluster needs a control-plane node count and at least one node pool. The API
refuses a request without them, so both are required arguments. The control
plane has 1 to 9 nodes; use an odd number, since etcd needs a quorum. The
console uses 3 control-plane nodes and one `workers` pool by default:

```python
client.kubernetes.create_cluster_and_wait(
    name="prod",
    kubernetes_version="v1.31.0",
    control_plane_vcpus=2,
    control_plane_ram_mb=4096,
    control_plane_disk_gb=20,
    control_plane_replicas=3,
    node_pools=[
        {
            "name": "workers",
            "class": "default-worker",
            "replicas": 3,
            "vcpus": 2,
            "ram_mb": 4096,
            "disk_gb": 20,
        },
    ],
)
```

`create_cluster` takes the same arguments and returns a `TaskRef`.
Provisioning takes several minutes.

The control-plane size is either `control_plane_flavor_id` or the three
`control_plane_vcpus`, `control_plane_ram_mb` and `control_plane_disk_gb`
values. A partial size is rejected.

Each node pool is a mapping with `name` (a DNS label, unique in the cluster)
and `replicas` (0 to 100). It can also have `class`, a size (`flavor_id`, or
`vcpus`, `ram_mb` and `disk_gb`), `autoscaler_min` and `autoscaler_max`,
`labels` and `taints`. A pool without a size takes `worker_flavor_id`.
`cluster_class` selects a cluster class.

## Reading clusters

```python
for summary in client.kubernetes.list_clusters():
    print(summary.name, summary.status, summary.specs.phase if summary.specs else "")

cluster = client.kubernetes.get_cluster("prod")
```

`list_clusters()` returns summaries. Their `status` is the lowercase lifecycle
status: `active`, `building`, `upgrading`, `deleting` or `error`. `specs`
carries the version, the phase, the readiness and the sizes, as of the last
periodic refresh, so a cluster created a moment ago may not have them all yet.

`get_cluster(name)` returns the topology in `cluster.spec.topology` and the
conditions in `cluster.status.conditions`.

## Kubeconfig

```python
kubeconfig = client.kubernetes.get_kubeconfig("prod")
```

The kubeconfig is returned as YAML. It carries cluster-admin rights, and
reading it is audited. Treat it as a secret.

`rotate_kubeconfig(name)` rotates the kubeconfig and returns the decoded
response as a dictionary. It requires the kubernetes-kubeconfig-rotation
organization feature.

## Scaling

```python
client.kubernetes.scale_control_plane("prod", 5)
client.kubernetes.scale_node_pool("prod", "workers", 6)
client.kubernetes.set_autoscaler("prod", "workers", 3, 10)
```

Keep the control-plane node count odd. `set_autoscaler` takes both bounds:
either 0 < minimum <= maximum, or 0 and 0 to turn the autoscaler off.

## Node pools

```python
client.kubernetes.add_node_pool(
    "prod",
    pool_name="batch",
    node_class="default-worker",
    vcpus=4,
    ram_mb=8192,
    disk_gb=40,
    replicas=2,
)
client.kubernetes.remove_node_pool("prod", "batch")
```

`node_class` is a node class that the cluster class defines. The size is either
`flavor_id`, an organization flavor, or all three of `vcpus`, `ram_mb` and
`disk_gb`. `autoscaler_min` and `autoscaler_max` are optional.

## Upgrades

```python
ref = client.kubernetes.upgrade_cluster("prod", "1.32.0")  # or "v1.32.0"
task = client.wait_for(ref)
print(task.result_str("maintenance_window_id"))
```

One task runs the preflight checks, opens the maintenance window and starts the
upgrade. If a preflight check blocks the upgrade, the request is still
accepted and the task fails, with the blocking checks listed in its message.
The window's id is in the task result as `maintenance_window_id`. The rolling
upgrade continues after the task completes; follow it with `get_cluster`.

`upgrade_cluster` requires the kubernetes-guided-upgrades organization feature,
and the API answers `404` without it. A version that does not look like
`1.32.0` or `v1.32.0` raises `ValueError`. Run the call under an idempotency
key to make it safe to repeat; see
[Idempotency keys](errors-and-retries.md#idempotency-keys).

### Preflight checks

```python
preflight = client.kubernetes.preflight("prod", "1.32.0")
report = preflight.report
if report is not None and not report.ready:
    print(report.blocking_issues)
```

Without a target version, `preflight` checks the health of the cluster only. It
requires the kubernetes-health-preflight organization feature. Each entry of
`report.checks` has a `name`, `passed` and `details`.

### Maintenance windows

```python
import datetime

start = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
window = client.kubernetes.create_maintenance_window(
    "prod",
    name="weekly",
    starts_at=start,
    ends_at=start + datetime.timedelta(hours=4),
    reason="node upgrade",
)
```

The name must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. The window must end
after it starts and last at most 30 days. `starts_at` and `ends_at` accept a
`datetime`, read as UTC when it has no time zone, or a string, which is sent as
is.

## Deleting a cluster

```python
client.wait_for(client.kubernetes.delete_cluster("prod"))
```
