# DNS

`client.dns` manages authoritative DNS zones and their record sets. A new zone
is served only after its owner proves control of the domain.

## Creating and verifying a zone

```python
zone = client.dns.create_zone_and_wait(name="example.com.", email="hostmaster@example.com")
print(zone.status)  # PENDING_VERIFICATION
print(zone.verification.name, zone.verification.type, zone.verification.value)
```

`email` is the SOA contact. `ttl` is optional, and the server uses 300 when it
is zero.

A new zone has the status `PENDING_VERIFICATION` (`DNS_ZONE_STATUS_PENDING_VERIFICATION`),
and `verification` names the TXT record that proves control of the domain:
`name` is fully qualified, such as `_nemau-challenge.example.com.`, and `value`
is the exact text the record must hold. Publish that record at the current DNS
host of the domain, then ask for the check:

```python
zone = client.dns.verify_zone_and_wait(zone.id)
print(zone.status)  # ACTIVE
```

The platform looks up the TXT record and activates the zone if it holds the
expected value. Its status becomes `ACTIVE` (`DNS_ZONE_STATUS_ACTIVE`) and
`verification` becomes `None`. While the record is absent or wrong, the task
fails, `verify_zone_and_wait` raises `NemauTaskFailedError`, and the zone stays
pending. A record published a moment ago may not have reached the domain's
nameservers yet, so the check may need another try.

`verify_zone(zone_id)` returns the `TaskRef` without waiting.
`verify_zone_and_wait` accepts a `deadline`; see
[Deadlines](asynchronous-writes.md#deadlines).

Zones are addressed by `id`. `name` is the canonical zone name, such as
`example.com.`, and is not an identifier. `get_zone(zone_id)` and
`delete_zone(zone_id)` take the id, and `list_zones()` lists the zones of the
project.

### Refusals

| Reason | Status | Meaning |
| --- | --- | --- |
| `REASON_DNS_ZONE_RESERVED` | `400` | The name is under a domain the platform keeps. |
| `REASON_DNS_ZONE_EXISTS` | `409` | The zone is already active on the platform, or already claimed in this project. |
| `REASON_DNS_ZONE_ALREADY_VERIFIED` | `409` | `verify_zone` on a zone that is already active. |
| `REASON_DNS_ZONE_PENDING_VERIFICATION` | `409` | A record-set write on a zone whose ownership is not proven yet. |

```python
from nemau import REASON_DNS_ZONE_ALREADY_VERIFIED, NemauApiError, has_reason

try:
    client.dns.verify_zone_and_wait(zone.id)
except NemauApiError as error:
    if not has_reason(error, REASON_DNS_ZONE_ALREADY_VERIFIED):
        raise
```

## Record sets

```python
ref = client.dns.create_record_set(
    zone.id,
    name="www",
    record_type="A",
    records=["203.0.113.7"],
    ttl=300,
)
client.wait_for(ref)

for record_set in client.dns.list_record_sets(zone.id):
    print(record_set.id, record_set.name, record_set.type, record_set.records)
```

The zone must be active. A zone pending verification has no record sets, so
its list is empty.

A record set's id has the form `canonical.name./TYPE`, for example
`www.example.com./A`. `get_record_set(zone_id, record_set_id)` and
`delete_record_set(zone_id, record_set_id)` take it.

`update_record_set` changes the records, the `ttl` or the `description` of a
record set:

```python
client.dns.update_record_set(zone.id, "www.example.com./A", records=["203.0.113.8"])
```

Arguments left at their default (`records=None`, `ttl=0`, `description=""`)
are not sent.

## Updates

`update_zone(zone_id, description=..., email=..., ttl=...)` changes the
description, the SOA contact or the default TTL of a zone. Its name cannot
change. See [Updating resources](updating-resources.md).
