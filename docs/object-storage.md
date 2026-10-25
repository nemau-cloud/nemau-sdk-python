# Object storage

Object containers hold objects addressed by key. They are managed with
`client.storage`, which also covers [block volumes](block-storage.md).

## Containers

```python
client.wait_for(client.storage.create_container("reports", versioning=True))

for container in client.storage.list_containers():
    print(container.name, container.count, container.bytes, container.s3_bucket)

container = client.storage.get_container("reports")
```

Containers are addressed by name. `delete_container(name)` returns a
`TaskRef`.

### Object lock

`object_lock` makes stored object versions immutable for `retention_days`, for
example to protect backups against ransomware. Object lock turns on versioning.

```python
from nemau import RETENTION_COMPLIANCE

client.storage.create_container(
    "backups",
    object_lock=True,
    retention_mode=RETENTION_COMPLIANCE,
    retention_days=30,
)
```

Protection can only be set when the container is created. S3 does not allow
object lock to be added to an existing bucket.

- `RETENTION_GOVERNANCE` retention can be lifted by the platform operator.
- `RETENTION_COMPLIANCE` retention cannot be lifted by anyone before it
  expires.

Customer credentials cannot lift either mode. `retention_days` is at most 2555
days, seven years (`nemau.services.storage.MAX_RETENTION_DAYS`). A retention
mode or period without `object_lock` raises `ValueError`.

`Container.protection` reports the protection requested at creation, and is
`None` for a plain container. `container_protection(name)` reads what the
object store enforces, so an audit can compare the two:

```python
protection = client.storage.container_protection("backups")
print(protection.versioning, protection.object_lock, protection.immutable)
print(protection.retention_mode, protection.retention_days)
```

`immutable` is true when object lock is on and versioning is `Enabled`.
Versioning alone does not protect a version from being deleted.

`update_container(name, versioning=..., retention_days=...)` changes the
versioning or the retention. Retention can be extended but not shortened. See
[Updating resources](updating-resources.md).

## Objects

```python
uploaded = client.storage.upload_object("reports", "2026/q3.csv", data, "text/csv")
content = client.storage.download_object("reports", "2026/q3.csv")
client.storage.delete_object("reports", "2026/q3.csv")
```

These calls are synchronous. The data passes through the API, which limits an
object to `MAX_OBJECT_SIZE` (100 MiB). `upload_object` accepts `bytes` or `str`,
raises `ValueError` above that size, and sends the request once without
retrying it. `download_object` returns the whole object in memory.

### Listing objects

`list_objects` returns one page of objects in key order. `limit` is 1 to 1000;
when it is left out, the server returns 100. Pass `next_marker` back unchanged
as `marker` for the next page, and do not build a marker from a key. The last
page has an empty `next_marker`.

```python
page = client.storage.list_objects("reports", prefix="2026/", limit=500)
for obj in page.objects:
    print(obj.name, obj.bytes, obj.content_type, obj.last_modified)
```

`iter_objects` walks every page:

```python
for obj in client.storage.iter_objects("reports", prefix="2026/", page_size=500):
    print(obj.name, obj.bytes)
```

### Copying an object

The copy happens inside the object store. The data passes through neither the
API nor the client, so the 100 MiB limit does not apply. The limit is
`MAX_COPY_SIZE` (5 GiB), the maximum for a single S3 copy, and a larger source
answers `413`. For larger objects, use S3 access keys (see below) with rclone
or the AWS CLI.

```python
copied = client.storage.copy_object("reports", "2026.csv", destination_container="archive")
print(copied.container, copied.key, copied.size, copied.version_id)
```

`destination_container` and `destination_key` default to the source's. Both
containers must belong to the current project. The copy is synchronous: it
exists when the call returns.

The content type and metadata of the source are kept unless `replace_metadata`
is set, and `content_type` and `metadata` require it. Copying an object onto
itself with `replace_metadata=True` is how its metadata is changed.

A copy into a container with object lock gets that container's default
retention, reported in `copied.retention`. The retention of the source does not
follow, and a shorter one cannot be chosen.

## Presigned URLs

For a bulk export, use a presigned URL: the transfer goes directly to the
object store, and `MAX_OBJECT_SIZE` does not apply.

```python
presigned = client.storage.presign_get("photos", "holiday.jpg")
content = client.storage.fetch_presigned(presigned)
```

`presign_put` returns a URL that uploads one object in the same way.
`ttl_seconds` sets the expiry, up to `PRESIGN_TTL_CAP_SECONDS` (3600); a larger
value raises `ValueError`. A `PresignedObject` has `url`, `method`,
`expires_at` and `max_bytes`.

`fetch_presigned` does not send the client's credential. The presigned URL
carries its own authorisation, and the object store is a different host from
the API. Anyone holding the URL can use it until it expires, so treat it as a
secret and keep it out of logs.

## S3 access keys

For a full migration, or to use S3 tools such as `aws s3`, `rclone` or
`s3cmd`, create an S3 access key scoped to one container:

```python
import datetime

from nemau import CREDENTIAL_READ_ONLY

tomorrow = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
credential = client.storage.create_credential(
    "photos",
    CREDENTIAL_READ_ONLY,  # or CREDENTIAL_READ_WRITE
    expires_at=tomorrow,  # optional, timezone-aware
)
# S3 tools address credential.s3_bucket, not the container name:
#   aws --endpoint-url "$ENDPOINT" s3 sync s3://<credential.s3_bucket> ./backup
```

`credential.secret_access_key` is returned only by this call, and the platform
does not store it. If it is lost, create a new key. `repr()` and `str()` print
`[redacted]` in its place.

In the object store, each container has its own opaque bucket name, and a key's
policy covers that bucket only. Give S3 tools the value of
`credential.s3_bucket`, which is also set on each container returned by
`list_containers()`. The object store rejects the container name.
`credential.endpoint` and `credential.region` give the S3 endpoint and region.
An empty
`endpoint` means that the platform has no reachable S3 endpoint to show, as in
a demonstration environment.

`expires_at` must be in the future and within the maximum lifetime the platform
allows. List the keys of a container with `list_credentials("photos")`, and
revoke one with `revoke_credential("photos", access_key_id)`. Secrets are never
listed.

Creating, listing and revoking keys requires permission to manage storage
credentials (`storage:ManageCredentials`). Two error helpers apply here:

- `is_credential_limit(error)` is true when the container already holds the
  maximum number of keys. `error.limit` and `error.scope` carry the cap and
  what it counts. Revoke an unused key before creating another.
- `is_organization_read_only(error)` is true when the organization is in its
  read-only phase.
