# Managed PostgreSQL

`client.databases` manages PostgreSQL databases: create, read, change,
resize, restore from a point in time, rotate the administrator password, and
delete. A database is reached at its `address` and `port` over TLS only.

## Creating a database

```python
created = client.databases.create(
    name="orders-db",
    version="17",
    flavor="db-medium",
    storage_gb=50,
    subnet_id=subnet_id,
    admin_username="app_admin",
)

# The password is returned once, here. Store it now: it cannot be read again.
vault.put("orders-db/admin", created.password)
```

`create` returns a `DatabaseCredentials`: `task_id`, `username` and `password`.
The database is built by the task. Wait for it with `client.wait_for_task(created.task_id)`,
then read it with `client.databases.get(database_id)`.

`version` and `flavor` must be part of the region's offer; the API refuses any
other value. The SDK checks what it can know: the name, the administrator
username (not `postgres`, not `pg_*`, not `nemau*`), `storage_gb` from 10 to
2000, `backup_retention_days` from 1 to 35, up to 5 security groups, and a
maintenance window of `weekday` 0 to 6 and `hour` 0 to 23, in UTC.

## The password

`create`, `rotate_password` and `restore` return a `DatabaseCredentials`.

- The password is shown once. It is not in the database record, in any task, or
  in a later read.
- The password is held outside the dataclass fields, in a `OneTimePassword`
  whose `repr()` and `str()` are redacted. So `repr()`, `dataclasses.asdict()`,
  `fields()` and a logged result never show it. Read it with `.password`, and
  store it at once. The SDK never puts it in an exception message. Do not log
  the attribute itself.
- These three writes are sent **without** an `Idempotency-Key`, which the API
  refuses on them. `client.idempotency_key(...)` does not apply to them. A retry
  after a lost response issues a new password, so read the database back, or
  rotate, instead of retrying blindly.
- A rotation is safe to repeat in one sense: until its task applies the new
  verifier, the old password stays valid.

## Connecting

`ca_certificate()` returns the PEM of the region's database CA. Connect with
`sslmode=verify-full` and that CA as `sslrootcert`:

```python
Path("db-ca.pem").write_text(client.databases.ca_certificate())
# sslmode=verify-full sslrootcert=db-ca.pem
```

The server accepts only TLS 1.2 and later, and refuses a plain connection.

## Reading and changing

- `list()` returns the databases of the project. `get(database_id)` returns one,
  with `description`, `version`, `flavor`, `storage_gb`, `address`, `port`, the
  backup retention and the maintenance window. No internal identifier is returned.
- `status` is one of the `DatabaseStatus` values: `building` (being provisioned),
  `active` (serving), `error` (failed, and needs an operator) or `deleted`
  (retired, its backups may still be inside their restore window).
- `earliest_restorable_time` and `latest_restorable_time` bound the point-in-time
  restores, as RFC 3339 strings. The latest is the last archived WAL; it is
  `None` until the agent has reported it. `restorable_until` is set only on a
  `deleted` database: it closes the window its backups stay restorable for.
- `pending_resize` is the resize held for the maintenance window, a
  `DatabasePendingResize` with `flavor`, `storage_gb` and `apply_at`, or `None`.
- `update(database_id, name=..., description=..., backup_retention_days=...,
  maintenance_window=...)` changes the fields given. `None` leaves a field
  unchanged; an update with no field is refused before any request. A raised
  retention is billed.
- `update(database_id, maintenance_window=None)` clears the window. The SDK
  sends an explicit `null`, which the API reads as a removal. Leaving the
  argument out keeps the window as it is, so the two are different calls.
- `restart(database_id)` reboots the appliance. The database stays `active`,
  and the restart is not billed.
- `flavors()` returns the sizes the region offers, as `DatabaseFlavor` objects
  (`name`, `vcpus`, `memory_mb`). Pass a `name` as `flavor`. `versions()`
  returns the PostgreSQL majors the region admits, such as `"17"`. Both come
  from the region's catalog, so check them before a create or a resize.
- `resize(database_id, flavor=..., storage_gb=..., apply_at=...)` moves to a
  larger flavor, or grows the storage. Storage only grows, and a database never
  shrinks. When `apply_at` is not given the SDK sends nothing and the server
  applies the resize **now**. Pass `apply_at="maintenance_window"` to hold it for
  the next window; it then shows in `pending_resize` until it applies.

## Restore

```python
restored = client.databases.restore(
    source_id,
    name="orders-db-restored",
    target_time=datetime(2026, 10, 20, 10, 30, tzinfo=timezone.utc),
    flavor="db-medium",
    storage_gb=50,
    subnet_id=subnet_id,
    admin_username="app_admin",
)
```

A restore writes a **new** database. The source is never changed. `target_time`
is an aware `datetime`, or an RFC 3339 string, and must not be in the future. It
must also fall in the source's backup window; the API refuses a time outside it.

## Rotating the password

`rotate_password(database_id)` returns new `DatabaseCredentials`. The password
from the previous call stops working once the task applies the new verifier.

## Deleting

`delete(database_id)` keeps the backups for their retention: the database then
reads `status == "deleted"`, with `restorable_until` set to the end of that
window. `delete(database_id, purge_backups=True)` erases them now. Both return a
`TaskRef`.
