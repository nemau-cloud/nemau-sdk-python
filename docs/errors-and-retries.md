# Errors and retries

The SDK's exceptions derive from `NemauError`:

- `NemauApiError`: the API answered with a status outside the 2xx range.
- `NemauTaskFailedError`: a task waited on with `wait_for` failed or was
  cancelled.

Other exceptions come from the standard library. `ValueError` reports an
invalid argument, raised before any request is sent. `TimeoutError` reports a
`wait_for` deadline that has passed. Network errors from `urllib`, such as
`urllib.error.URLError`, are raised unchanged when no retry is left.

## API errors

```python
from nemau import NemauApiError, is_not_found

try:
    instance = client.compute.get_instance(instance_id)
except NemauApiError as error:
    if is_not_found(error):
        instance = None
    elif error.forbidden:
        ...  # IAM policy, token scope, or a missing organization feature
    else:
        raise
```

A `NemauApiError` has these attributes:

| Attribute | Content |
| --- | --- |
| `status_code` | The HTTP status. |
| `message` | The error text returned by the API, safe to display. Client errors usually carry a specific message, such as "project quota exceeded for instances: 10/10 in use". |
| `reason` | A machine-readable code that some refusals carry, such as `permission_denied` or `subnet_overlap`. Empty otherwise. |
| `code` | A stable code that some refusals carry: `credential_limit`, `organization_read_only` or `invalid_request`. Empty otherwise. |
| `limit`, `scope` | For a `credential_limit` refusal: the cap that was reached, and what it counts. |
| `retry_after` | The seconds the server asked to wait, from `Retry-After` on a `429` or `503`. `None` otherwise. |
| `body` | The beginning of the raw body (4096 characters at most), useful when a proxy answered with HTML. |
| `method`, `path` | The request that failed. |

`retryable` is true for `429` and `5xx`. `not_found`, `unauthorized` and
`forbidden` test for `404`, `401` and `403`. A `403` means an IAM policy, a
token scope, or a missing organization feature.

Branch on `reason` or `code`. The wording of `message` can change.

The `get_*` methods that list and filter, such as `network.get_network` or
`storage.get_volume`, raise the same `404` `NemauApiError` as a direct read when
the id is unknown.

## Error helpers

Each helper takes any exception and returns a boolean.

| Helper | Matches |
| --- | --- |
| `is_not_found` | `404` |
| `is_unauthorized` | `401` |
| `is_forbidden` | `403` |
| `is_rate_limited` | `429`: the write budget is spent. |
| `is_idempotency_key_mismatch` | `422`: the idempotency key was already used with other parameters. |
| `is_subnet_overlap` | `409`: the subnet's range overlaps another subnet of the project. |
| `is_certificate_in_use` | `409`: a listener still serves the certificate. |
| `is_access_key_limit` | `409`: the user already holds the maximum number of active IAM access keys. |
| `is_credential_limit` | The container already holds the maximum number of S3 access keys. |
| `is_organization_read_only` | `403`: the organization is in its read-only phase. |
| `is_resource_in_use` | A `NemauTaskFailedError` whose task failed with `resource_in_use`. |
| `has_reason(error, reason)` | A `NemauApiError` with the given `reason`. |

The reason and code values are exported as constants: `REASON_SUBNET_OVERLAP`,
`REASON_CERTIFICATE_IN_USE`, `REASON_DNS_ZONE_RESERVED`,
`REASON_DNS_ZONE_EXISTS`, `REASON_DNS_ZONE_ALREADY_VERIFIED`,
`REASON_DNS_ZONE_PENDING_VERIFICATION`, `ERROR_CODE_CREDENTIAL_LIMIT`,
`ERROR_CODE_ORGANIZATION_READ_ONLY`, `ERROR_CODE_INVALID_REQUEST` and
`ERROR_CODE_RESOURCE_IN_USE`.

```python
from nemau import REASON_DNS_ZONE_EXISTS, NemauApiError, has_reason

try:
    ref = client.dns.create_zone(name="example.com.", email="hostmaster@example.com")
except NemauApiError as error:
    if not has_reason(error, REASON_DNS_ZONE_EXISTS):
        raise
```

## Failed tasks

`NemauTaskFailedError` carries the task in its `task` attribute. Its message
includes the task id, type and status, and the error text. The platform
replaces internal error details with a generic message, so give the task id to
support when you report a failure.

When a delete is refused because something still depends on the resource (a
volume attached to the instance, a subnet on the network), the task fails with
the code `resource_in_use`. `is_resource_in_use` recognises it, and the error
message names what to remove first. The delete keeps failing until that is
done.

```python
from nemau import NemauTaskFailedError, is_resource_in_use

try:
    client.wait_for(client.compute.delete_instance(instance_id))
except NemauTaskFailedError as error:
    if is_resource_in_use(error):
        ...  # detach the volume named in str(error), then delete again
    else:
        raise
```

## Retries

By default the client makes up to 4 attempts on `429`, `5xx` and network
errors. The wait after the nth failed attempt is a random value between 0 and
`min(0.3 * 2 ** (n - 1), 5)` seconds (exponential backoff with full jitter), so
that many clients do not retry in step.

- Reads (`GET`, `HEAD`) are always retried.
- Writes are retried only when they carry an `Idempotency-Key`. Every queued
  write, one that answers `202` with a task, sends a key and reuses it on each
  retry, so a retry cannot queue the work twice. A write without a key may
  already have been applied, so it is never retried.
- An object upload is sent once.

On a `429` (rate limit reached) or a `503` (service unavailable), the
`Retry-After` header sets the minimum wait before the next attempt. If the
server asks for more than `max_retry_after` seconds (30 by default), the error
is raised at once with `error.retry_after` set.

`RetryPolicy` sets these values. Its defaults are `max_attempts=4`,
`base_delay=0.3`, `max_delay=5.0` and `max_retry_after=30.0`.

```python
from nemau import NemauClient, RetryPolicy

client = NemauClient(retry=RetryPolicy(max_attempts=1))  # no retries
client = NemauClient(retry=RetryPolicy(max_attempts=6, max_retry_after=60))
```

## Rate limits

Writes are rate limited. When the budget is spent, the API answers `429` with a
`Retry-After` header. The client waits and retries as described above, and
raises the error when the attempts are spent or the server asks for a longer
wait than `max_retry_after`.

```python
from nemau import NemauApiError, is_rate_limited

try:
    ref = client.compute.start(instance_id)
except NemauApiError as error:
    if not is_rate_limited(error):
        raise
    print("try again in", error.retry_after, "seconds")
```

## Idempotency keys

Every queued write sends an `Idempotency-Key` header with a new random key, and
reuses it on each retry. To make a whole run safe to repeat, for example a
script that may crash and be restarted, set the key yourself.

The server keeps a key for 24 hours. The same key with the same parameters
answers `202` with the original task id, and `ref.replayed` is `True`. The same
key with different parameters is refused with `422`
(`is_idempotency_key_mismatch`), which is not retried.

```python
with client.idempotency_key("nightly-2026-10-10:web-1"):
    ref = client.compute.create_instance(name="web-1", image_id="ubuntu-24.04", flavor_id=flavor_id)
if ref.replayed:
    print("already queued:", ref.task_id)
```

A key is 1 to 128 characters of letters, digits, `.`, `_`, `:` and `-`. Any
other key raises `ValueError`. Use one block per write: two writes with
different parameters under the same key conflict. The key applies to the
current thread or asyncio task.
