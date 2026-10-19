# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) (0.x: a minor
release may change the API).

## [Unreleased]

### Changed

- **`DEFAULT_BASE_URL` is now `https://api.nemau-cloud.com`**, the production
  API. Set `NEMAU_API_URL` or `base_url` to target another environment.
- The README covers installation and a first example. The detailed guide is in
  `docs/`, and `CONTRIBUTING.md` and `SECURITY.md` describe how to contribute
  and how to report a vulnerability.

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
