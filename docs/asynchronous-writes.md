# Asynchronous writes

Creates, deletes, updates and actions answer `202` with a task reference, and
the platform does the work in the background. The methods that send them return
a `TaskRef`. The resource does not exist yet when the reference comes back.

```python
ref = client.network.create_network("front", "web tier")
print(ref.task_id, ref.status)
```

A `TaskRef` has `task_id`, `status`, `queue`, `message` and `replayed`. The last
one is described in [Idempotency keys](errors-and-retries.md#idempotency-keys).

## Waiting for a task

`client.wait_for(ref)` reads the task until it finishes and returns the `Task`.
A failed or cancelled task raises `NemauTaskFailedError`, which carries the
task in its `task` attribute.

```python
task = client.wait_for(ref)
print(task.status, task.resource_id, task.result)
```

`client.wait_for_task(task_id)` does the same from a task id. Right after the
`202`, the task may not be readable yet: the client treats a `404` as "not
finished" and keeps reading.

The client reads the task every `poll_interval` seconds, 2 by default:

```python
client = NemauClient(poll_interval=5)
```

## Deadlines

Without a deadline, `wait_for` waits until the task finishes. `deadline` is a
`time.monotonic()` timestamp, so several waits in a row can share one deadline.
When it passes, `wait_for` raises `TimeoutError`. The task is not cancelled:
the API has no cancellation.

```python
import time

deadline = time.monotonic() + 20 * 60
client.wait_for(subnet_ref, deadline=deadline)
client.wait_for(instance_ref, deadline=deadline)
```

## Getting the id of a new resource

`client.wait_for_resource_id(ref, *keys)` waits for the task, then returns the
id it produced along with the task. The id is the task's `resource_id` when the
platform set one, and otherwise the first non-empty value among the `result`
keys you name.

```python
ref = client.network.create_security_group("ssh", "ssh only")
group_id, task = client.wait_for_resource_id(ref, "security_group_id")
```

If neither holds an id, it raises a `NemauApiError` with status `502`.

## Methods that create and wait

These methods send the write, wait for the task and return the new resource:

| Method | Returns |
| --- | --- |
| `compute.create_instance_and_wait(**kwargs)` | `Instance` |
| `network.create_network_and_wait(name, description="")` | `Network` |
| `storage.create_volume_and_wait(name, size_gb, description="")` | `Volume` |
| `load_balancer.create_and_wait(**kwargs)` | `LoadBalancer` |
| `kubernetes.create_cluster_and_wait(**kwargs)` | `KubernetesCluster` |
| `dns.create_zone_and_wait(**kwargs)` | `DNSZone` |
| `dns.verify_zone_and_wait(zone_id, deadline=None)` | `DNSZone` |

`kwargs` are the arguments of the matching `create_*` method. Apart from
`verify_zone_and_wait`, these methods wait without a deadline. To set one, call
the `create_*` method and `wait_for` yourself.

## Reading tasks

`client.tasks` reads one task, or the tasks recorded for one resource. The API
has no organization-wide task list.

```python
task = client.tasks.get(ref.task_id)
history = client.tasks.list_for_resource(instance_id)
```

A `Task` has `id`, `task_type`, `status`, `progress`, `resource_id`,
`resource_type`, `result`, `error`, `error_code`, `created_at` and
`completed_at`. `task.terminal` and `task.succeeded` test the status, and
`task.result_str(*keys)` reads a string from `result`, trying each key in
order. The statuses are in `TaskStatus`: `pending`, `running`, `retrying`,
`completed`, `failed` and `cancelled`.

The platform replaces internal error details with a generic message. The
message of `NemauTaskFailedError` includes the task id: give it to support when
you report a failure.

## Synchronous writes

A few writes complete within the request. They return their result directly and
there is no task to wait for:

- `compute.create_flavor`, `compute.update_flavor`, `compute.delete_flavor`
- `compute.create_keypair`
- `storage.upload_object`, `storage.delete_object`, `storage.copy_object`
- `storage.create_credential`, `storage.revoke_credential`
- `load_balancer.import_certificate`, `load_balancer.delete_certificate`
- `kubernetes.create_maintenance_window`, `kubernetes.rotate_kubeconfig`

These requests carry no idempotency key, so the client does not retry them. See
[Retries](errors-and-retries.md#retries).
