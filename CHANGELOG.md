# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) (0.x: a minor
release may change the API).

## [Unreleased]

### Added

- Scheduled backups of an instance. `compute.update_instance` takes
  `backup_schedule` (`none`, `daily` or `weekly`), `backup_hour` (0 to 23, UTC),
  `backup_weekday` (0 to 6, weekly only) and `backup_retention_days` (1 to 35).
  Values are checked before the request is sent.
- `Instance` carries `backup_schedule`, `backup_hour`, `backup_weekday` and
  `backup_retention_days`.
- `Snapshot` carries `origin` (`manual` or `scheduled`) and `expires_at`, set
  for scheduled snapshots only.
- `user_data` on `create_instance` (and `create_instance_and_wait`): a
  cloud-init document run on the instance's first boot. It is checked
  client-side as UTF-8, at most 16 KiB and without NUL; the API stays the
  authority. Instances report `user_data_sha256` when one was set. The content
  is never returned.
- `client.databases`: managed PostgreSQL. Create, read, update, resize, restore
  from a point in time, rotate the password, get the database CA and delete. A
  create, a rotation and a restore return the password once, in a
  `DatabaseCredentials` whose `password` is left out of `repr()`; these writes
  carry no `Idempotency-Key`, as the API requires.
- `client.instance_groups`: instance groups. Create, read, list, activities,
  update, desired capacity, member protection and delete. Launch templates carry
  `user_data`; target tracking on CPU and scheduled actions are supported.
- Instance groups take a `description` on create and update, and their models
  follow the server contract: `pools`, `launch_template.version`,
  `members[].status`, `activities[].type` and `created_at`.
- `Instance.instance_group_id` is `None` when the instance belongs to no group.
- Databases: `Database.status` is one of `DatabaseStatus` (`building`,
  `active`, `error`, `deleted`). The model adds `pending_resize`
  (`DatabasePendingResize`), the restore window (`earliest_restorable_time`,
  `latest_restorable_time`, `restorable_until`) and `description`. A resize
  applies now unless `apply_at="maintenance_window"` is given.
- `databases.restart(database_id)`, and the catalogs `databases.flavors()`
  (`DatabaseFlavor`: `name`, `vcpus`, `memory_mb`) and `databases.versions()`.
- `databases.update(database_id, maintenance_window=None)` clears the
  maintenance window with an explicit `null`. Leaving the argument out keeps the
  window; `None` is no longer the same as leaving it out for this field.
- `instance_groups.refresh(group_id, max_unavailable_pct=..., max_surge=...)`
  starts a rolling refresh, and `instance_groups.cancel_refresh(group_id)` stops
  it. Each group carries its `refresh` (`InstanceGroupRefresh`).
- `kubernetes.create_cluster` takes the optional `pod_cidr` and `service_cidr`
  (IPv4 CIDRs), sent only when set.
- Client-side checks before a cluster creation is sent: `network_id` and
  `subnet_id` must be UUIDs, node pool labels and taints must match the server's
  syntax (at most 16 each), and a pool `class` must be `default-worker`. A
  malformed value raises `ValueError` naming the pool.
- `kubernetes.add_node_pool` takes `labels` and `taints`, checked the same way.
- The cluster view decodes `spec.network_id`, `spec.subnet_id`, `pod_cidr` and
  `service_cidr` (on `KubernetesClusterSpec`), and the node pool labels and taints.

### Changed

- **Breaking: `kubernetes.create_cluster` requires `network_id` and `subnet_id`.**
  Both are UUIDs: the project network the cluster lives in, and the subnet its
  nodes sit on. Callers that omit them get a `TypeError`. Update every call.
- **`DEFAULT_BASE_URL` is now `https://api.nemau-cloud.com`**, the production
  API. Set `NEMAU_API_URL` or `base_url` to target another environment.
- The README covers installation and a first example. The detailed guide is in
  `docs/`, and `CONTRIBUTING.md` and `SECURITY.md` describe how to contribute
  and how to report a vulnerability.
- Projects and quotas are read from `/projects` and `/quotas`. The former
  `/orchestration/projects` and `/orchestration/quotas` paths are gone.

### Security

- `DatabaseCredentials` no longer has a `password` dataclass field: the
  password lives in a one-time object with a redacted `repr()`, so
  `dataclasses.asdict()`, `fields()` and `extra` never carry it. `.password`
  still reads it. Code that built `DatabaseCredentials(password=...)`, for
  instance in a mock, must drop that argument.

## [0.2.0]

### Added

- Idempotency keys. Every asynchronous write sends an `Idempotency-Key`
  (generated, or pinned with the `client.idempotency_key(key)` context manager). `is_idempotency_key_mismatch`,
  `is_rate_limited` and `is_access_key_limit` classify errors.
- A read-only `iam` service: `list_users`, `list_access_keys` and
  `access_key_limits`.
- `storage.iter_objects` follows the opaque page cursor across a container
  listing.
- `kubernetes.upgrade_cluster(name, version)` runs the guided upgrade as one
  task.
- Client-side validation of the metrics period.

### Changed

- 429 and 503 are retried with `Retry-After`. **Only reads and writes that carry
  an `Idempotency-Key` are retried.**

### Removed

- **`PreflightBlockedError`, `kubernetes.upgrade` and `kubernetes.start_upgrade`**,
  replaced by `kubernetes.upgrade_cluster`. A blocked preflight fails the
  upgrade task.

### Notes

- Access keys expire: 90 days by default, 365 at most. After the expiry the API
  answers `401`.
