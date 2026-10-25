# Updating resources

Names, descriptions and some settings can be changed in place. The `update_*`
methods send a `PATCH` that carries only the fields you pass, and return a
`TaskRef` like other writes.

```python
ref = client.network.update_network(network_id, description="front segment")
client.wait_for(ref)
```

The update methods take keyword arguments:

- An argument you omit, or pass as `None`, is left out of the request, and the
  field keeps its value. A caller working from a stale copy of a resource
  cannot overwrite a field it did not pass.
- An empty string clears a text field: `description=""`.
- A call without any field raises `ValueError` before any request is sent.

## Update methods

| Method | Fields |
| --- | --- |
| `compute.update_instance(instance_id, ...)` | `name`, `description`, `backup_schedule`, `backup_hour`, `backup_weekday`, `backup_retention_days` |
| `network.update_network(network_id, ...)` | `name`, `description` |
| `network.update_subnet(subnet_id, ...)` | `name` |
| `network.update_security_group(group_id, ...)` | `name`, `description` |
| `network.update_floating_ip(floating_ip_id, ...)` | `description` |
| `storage.update_volume(volume_id, ...)` | `name`, `description` |
| `storage.update_container(name, ...)` | `versioning`, `retention_days` |
| `load_balancer.update_load_balancer(lb_id, ...)` | `name`, `description` |
| `load_balancer.update_listener(listener_id, ...)` | `name`, `description`, `connection_limit`, `tls_certificate_ref` |
| `load_balancer.update_pool(pool_id, ...)` | `name`, `description`, `lb_algorithm` |
| `load_balancer.update_pool_member(pool_id, member_id, ...)` | `name`, `weight` |
| `load_balancer.update_health_monitor(pool_id, monitor_id, ...)` | `name`, `delay`, `timeout`, `max_retries`, `expected_codes` |
| `dns.update_zone(zone_id, ...)` | `description`, `email`, `ttl` |

## What an update leaves in place

- An instance keeps running when it is renamed. The name is a label, and
  nothing addresses the instance by it.
- A network is not rebuilt. Its id and its segment stay the same, and the
  instances attached to it are not affected.
- Security group rules cannot be edited. Create a new rule and delete the old
  one.
- The address of a floating IP cannot change: the address is the allocation.
- A volume's size changes with `storage.extend_volume`, which only grows it.
- A container keeps its name. Its retention can be extended, not shortened.
- Changing `tls_certificate_ref` on a `TERMINATED_HTTPS` listener renews its
  certificate without downtime.
- Changing a pool's algorithm rewrites the appliance configuration in place.
  Established connections are not dropped.
- A DNS zone keeps its name, which is its identity.

## Other ways to change a resource

Some changes have their own methods:

- `compute.resize` changes an instance's size, and `compute.set_security_groups`
  replaces its security groups. See [Compute](compute.md).
- `compute.update_flavor` is synchronous and takes the complete flavor: `name`,
  `vcpus`, `ram_mb` and `disk_gb`.
- `dns.update_record_set` changes the records, TTL or description of a record
  set. See [DNS](dns.md#record-sets).
- `kubernetes.scale_control_plane`, `kubernetes.scale_node_pool` and
  `kubernetes.set_autoscaler` resize a cluster. See [Kubernetes](kubernetes.md).
