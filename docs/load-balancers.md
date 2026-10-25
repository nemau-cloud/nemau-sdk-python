# Load balancers

`client.load_balancer` manages load balancers, their listeners, pools, members
and health monitors, and the TLS certificates that HTTPS listeners serve.

## Creating a load balancer

```python
lb = client.load_balancer.create_and_wait(
    name="web",
    vip_subnet_id=subnet_id,
    flavor="lb-ha",
)
print(lb.id, lb.vip_address, lb.provisioning_status)
```

`vip_subnet_id` is required: the VIP is allocated in that subnet. `create`
returns a `TaskRef`, and `create_and_wait` waits and returns the
`LoadBalancer`. `list()`, `get(lb_id)` and `delete(lb_id)` list, read and
delete load balancers.

To reach the load balancer from outside, associate a floating IP with it:

```python
from nemau import TARGET_LOAD_BALANCER

client.network.associate_floating_ip(fip_id, TARGET_LOAD_BALANCER, lb.id)
```

## Tiers

`flavor` picks the tier. When it is empty, the tier is `lb-small`, with one
appliance. `lb-ha`, `lb-medium` and `lb-large` run two appliances on two hosts,
active and standby behind the same VIP. The tier is fixed at creation.

`list_flavors()` returns the tiers as `LoadBalancerFlavor` objects with `name`,
`appliances`, `vcpus` and `ram_mb` (for each appliance), `high_availability`,
`hourly_price`, `monthly_price` and `currency`. A high-availability tier is
billed as one load balancer.

On a high-availability tier, `lb.high_availability` reports which appliance is
active. It is `None` on a single-appliance tier.

- `status` is `REDUNDANT` (one active, one standby, on two hosts), `DEGRADED` or
  `UNKNOWN`.
- `reason` says why a pair is `DEGRADED`: `SPLIT_BRAIN`, `NO_ACTIVE_APPLIANCE`,
  `APPLIANCE_UNAVAILABLE` or `SINGLE_HOST`.
- `appliances` lists each appliance with its `index`, its `state` (`ACTIVE`
  holds the VIP, `STANDBY`, `DOWN` or `UNKNOWN`) and its `address`.

Members receive traffic from the appliances' own addresses
(`high_availability.appliances[].address`). Health checks and proxied
connections leave from these addresses, so the security groups of the members
must allow both of them.

## Protocols

There is no engine to choose. Every new load balancer runs in automatic mode
and accepts HTTP, HTTPS, TCP and UDP listeners together. The read-only
`provider` field is `auto` on these. Older load balancers may show `haproxy`
(no UDP listeners) or `nginx` (UDP listeners only).

## Listeners, pools and members

```python
balancers = client.load_balancer

client.wait_for(balancers.create_listener(lb.id, name="http", protocol="HTTP", protocol_port=80))
listener = next(item for item in balancers.list_listeners(lb.id) if item.name == "http")

client.wait_for(
    balancers.create_pool(
        lb.id,
        name="web",
        protocol="HTTP",
        lb_algorithm="ROUND_ROBIN",
        listener_id=listener.id,
    )
)
pool = next(item for item in balancers.list_pools(lb.id) if item.name == "web")

client.wait_for(
    balancers.add_pool_member(
        pool.id,
        name="web-1",
        address="10.42.0.11",
        protocol_port=8080,
        subnet_id=subnet_id,
    )
)
```

Listeners and pools are listed per load balancer: `list_listeners(lb_id)` and
`list_pools(lb_id)` require its id, and there is no project-wide list.

`create_listener` also accepts `description`, `default_pool_id`,
`connection_limit`, `tls_certificate_ref` and `admin_state_up`. `create_pool`
accepts `description`, `session_persistence` (a mapping with `type` and
`cookie_name`) and `admin_state_up`. When `admin_state_up` is left out, the API
sets it to true.

A member's `weight` is 1 when it is left at zero. `list_pool_members(pool_id)`
lists the members, and `remove_pool_member(pool_id, member_id)` removes one.

`delete_listener(listener_id)` deletes a listener, and `delete_pool(pool_id)`
deletes a pool.

## Health monitors

```python
ref = client.load_balancer.create_health_monitor(
    pool.id,
    monitor_type="HTTP",
    delay=5,
    timeout=3,
    max_retries=3,
    url_path="/healthz",
    expected_codes="200",
)
```

`monitor_type` is `HTTP`, `HTTPS`, `TCP` or `UDP_CONNECT`. A UDP pool reports
`UDP_CONNECT`: failures there are detected passively, without an active probe.
`http_method`, `url_path` and `expected_codes` are optional.

`list_pool_health_monitors(pool_id)` lists the monitors, and
`delete_health_monitor(pool_id, monitor_id)` deletes one.

## TLS certificates

A `TERMINATED_HTTPS` listener terminates TLS on the load balancer with a
certificate imported into the project. The import is synchronous, and the
private key is never returned:

```python
with open("fullchain.pem") as cert_file, open("privkey.pem") as key_file:
    cert = client.load_balancer.import_certificate(
        name="www",
        certificate=cert_file.read(),  # leaf first, chain optional
        private_key=key_file.read(),
    )

client.load_balancer.create_listener(
    lb.id,
    name="https",
    protocol="TERMINATED_HTTPS",
    protocol_port=443,
    tls_certificate_ref=cert.id,
)
```

The certificate and key are PEM-encoded. `certificate` holds the leaf,
optionally followed by its chain, and the `chain` argument can add
intermediates. The key is RSA 2048 to 4096 bits, or ECDSA P-256 or P-384,
without a passphrase. `tls_certificate_ref` is required for `TERMINATED_HTTPS`
and refused for the other protocols.

`list_certificates()` and `get_certificate(cert_id)` return `Certificate`
objects with `fingerprint_sha256`, `subject`, `domains`, `issuer`,
`not_before`, `not_after`, `key_algorithm`, `chain_length`, and `used_by`, the
listeners that serve the certificate.

A certificate cannot be modified. To renew it, import the new certificate,
point the listener at it, then delete the old one:

```python
client.wait_for(client.load_balancer.update_listener(listener_id, tls_certificate_ref=new_cert.id))
client.load_balancer.delete_certificate(old_cert.id)
```

Deleting a certificate that a listener still serves fails with a `409`, which
`is_certificate_in_use` recognises. Other certificate errors carry a
`certificate_*` `reason` on the `NemauApiError`.

## Updates

`update_load_balancer`, `update_listener`, `update_pool`, `update_pool_member`
and `update_health_monitor` change settings in place. See
[Updating resources](updating-resources.md).
