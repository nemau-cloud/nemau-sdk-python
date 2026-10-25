# Networking

`client.network` manages networks, subnets, network interfaces, floating IPs
and security groups. Load balancers have their own service, described in
[Load balancers](load-balancers.md).

## Networks

```python
network = client.network.create_network_and_wait("front", "web tier")

for item in client.network.list_networks():
    print(item.id, item.name, item.status)

network = client.network.get_network(network.id)
```

The API has no read for a single network, so `get_network` lists the networks
and filters them. An unknown id raises the same `404` `NemauApiError` as a
direct read would. `get_subnet`, `get_floating_ip` and `get_security_group`
work the same way.

`delete_network(network_id)` returns a `TaskRef`. Delete the instances on a
network before the network itself.

## Subnets

```python
ref = client.network.create_subnet(
    network.id,
    name="front-v4",
    cidr="10.42.0.0/24",
    dns_servers=["192.0.2.53"],
    allocation_pools=[{"start": "10.42.0.10", "end": "10.42.0.200"}],
)
client.wait_for(ref)
```

`cidr` is required for an IPv4 subnet. When `gateway_ip` is left out, the
platform chooses the gateway; `gateway_ip=""` creates a subnet without one.

An IPv4 range must not overlap any other subnet of the project, on this network
or another. All the subnets of a project are routed together, whatever their
network. The API refuses an overlapping range with a `409` that
`is_subnet_overlap` recognises, and the message names the conflicting subnet,
its range and its network.

```python
from nemau import NemauApiError, is_subnet_overlap

try:
    ref = client.network.create_subnet(network.id, name="back", cidr="10.42.0.0/24")
except NemauApiError as error:
    if not is_subnet_overlap(error):
        raise
    print("range taken:", error.message)
```

The check runs when the request is received. If someone creates a conflicting
subnet between the request and the task, the task fails instead, with a
`NemauTaskFailedError` carrying the same message and no reason.

The other subnet methods are `list_subnets()`, `get_subnet(subnet_id)` and
`delete_subnet(subnet_id)`.

## IPv6

IPv6 support consists of two independent features. Both are unavailable in a
region without an IPv6 prefix, and `get_capabilities()` says which ones the
region offers.

```python
from nemau import IPV6

capabilities = client.network.get_capabilities()
if capabilities.ipv6_subnets:
    client.network.create_subnet(network.id, name="front-v6", ip_version=IPV6)
if capabilities.ipv6_floating_ips:
    client.network.allocate_floating_ip(ip_version=IPV6)
```

### Dual-stack networks

For an IPv6 subnet, leave out `cidr`, `gateway_ip` and `allocation_pools`. The
platform assigns a /64 from the region's routed prefix and uses its first
address as the gateway. The network must already have an IPv4 subnet, and can
have at most one IPv6 subnet. An IPv6 subnet is not checked for overlap, since
its range is assigned by the platform.

Instances created on the network afterwards get a public IPv6 address, listed
after the IPv4 address in `addresses`. They must carry at least one security
group.

### IPv6 floating IPs

An IPv6 floating IP is translated to the private IPv4 address of its target on
the region's exit node. The guest needs no IPv6 configuration, and sees IPv6
clients as addresses of the translation range. A target holds at most one IPv6
floating IP. In a region that does not offer IPv6 floating IPs, the allocation
is refused with a `400`.

## Network interfaces

`list_network_interfaces()` returns `NetworkInterface` objects with `id`,
`slot`, `network_id`, `instance_id`, `instance_name`, `mac_address`, `status`
and `addresses`. Each address has a `subnet_id` and an `address`.

## Floating IPs

```python
from nemau import TARGET_INSTANCE

client.wait_for(client.network.allocate_floating_ip(description="web-1"))

for fip in client.network.list_floating_ips():
    print(fip.id, fip.address, fip.ip_version, fip.target_type, fip.target_id)

client.wait_for(client.network.associate_floating_ip(fip_id, TARGET_INSTANCE, instance_id))
```

`allocate_floating_ip` takes an optional `external_network_id`; when it is
empty, the region's external network is used. Without `ip_version`, the address
is IPv4.

`associate_floating_ip` takes `TARGET_INSTANCE` or `TARGET_LOAD_BALANCER` as
the target type. `disassociate_floating_ip(fip_id)` detaches the address and
keeps the allocation, and `release_floating_ip(fip_id)` releases it.

## Security groups

```python
ref = client.network.create_security_group("ssh", "ssh only")
group_id, _ = client.wait_for_resource_id(ref, "security_group_id")

ref = client.network.create_security_group_rule(
    group_id,
    direction="ingress",
    protocol="tcp",
    port_range_min=22,
    port_range_max=22,
    remote_ip_prefix="0.0.0.0/0",
)
client.wait_for(ref)
```

`direction` is `ingress` or `egress`. Leave the ports at zero for a protocol
that has none. A rule cannot be edited: delete it with
`delete_security_group_rule(group_id, rule_id)` and create a new one.

`list_security_groups()` returns each group with its `rules`.
`delete_security_group(group_id)` deletes a group. To change the groups of an
instance, use `compute.set_security_groups`.

### Egress filtering

New security groups filter outbound traffic. Each one starts with two egress
rules that allow all outbound traffic, one per address family (`0.0.0.0/0` and
`::/0`). Remove or narrow them to restrict what instances can reach. An
instance filters its outbound traffic only when all of its groups do, so a
single older group lets all of its outbound traffic through.

Groups created before egress filtering existed report
`egress_filtering == False`, and their egress rules have no effect.
`enable_security_group_egress_filtering` converts one. It adds the two default
rules, then recomputes the outbound policy of the instances that carry the
group.

```python
ref = client.network.enable_security_group_egress_filtering(group_id)
client.wait_for(ref)
```

Traffic is unchanged when the task completes, since the new rules allow
everything. The conversion cannot be undone. Running it again on a converted
group succeeds and changes nothing.

## Updates

`update_network`, `update_subnet`, `update_security_group` and
`update_floating_ip` change names and descriptions. See
[Updating resources](updating-resources.md).
