# Models

The SDK decodes responses into dataclasses defined in `nemau.models`. They are
also exported from the `nemau` package. Every model is built with `from_dict`.

## Attribute names

Attribute names are snake_case, even where the API uses camelCase:
`task.task_type` for `taskType`, `cluster.status.control_plane_ready` for
`controlPlaneReady`. The `loadbalancer_id` key of listeners and pools becomes
`load_balancer_id`.

`class` is a reserved word in Python, so the node class of a Kubernetes node
pool is `node_class`. When you pass a node pool to `kubernetes.create_cluster`
as a mapping, the key is still `"class"`.

## Unknown fields

A key that the SDK does not know is kept in `extra` and does not raise an
error. A field added to the API can be read this way before the SDK supports
it.

```python
network = client.network.get_network(network_id)
print(network.extra.get("new_field"))
```

`extra` is left out of `repr()` and of equality comparisons.

## Missing values

Fields that the API leaves out keep their default: an empty string, `0`,
`False`, an empty list, or `None` for optional values. Some fields are only set
in a given state. For example, `Snapshot.size_bytes` and `monthly_cost` are
`None` until the snapshot is active, and an empty `Instance.addresses` means
that the platform could not report the addresses.

## Secrets

`IssuedCredential`, returned by `storage.create_credential`, prints
`[redacted]` in place of `secret_access_key` in `repr()` and `str()`. Read the
attribute to get the value.

`CreatedKeypair`, returned by `compute.create_keypair`, has no such protection:
its `repr()` includes `private_key` when the platform generated the key. Do not
log it.

## Building models

`from_dict` and `list_from` build models from your own data, for example in
tests:

```python
from nemau import Instance

instance = Instance.from_dict({"id": "inst-1", "name": "web-1", "status": "running"})
instances = Instance.list_from([{"id": "inst-1"}, {"id": "inst-2"}])
```

Nested objects are decoded too: `Instance.security_groups` is a list of `Ref`,
and `LoadBalancer.listeners` a list of `Listener`.
