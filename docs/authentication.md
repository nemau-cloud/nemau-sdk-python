# Authentication

Every request carries a credential, except the region endpoints (see
[Regions](projects.md#regions)). The credential also decides the organization a
request acts on: a call cannot leave it.

## Credential types

| Class | Headers sent | Use |
| --- | --- | --- |
| `AccessKey(key_id, secret)` | `X-Access-Key-Id`, `X-Access-Key-Secret` | An IAM user's long-lived programmatic key, for scripts and CI jobs. |
| `BearerToken(token)` | `Authorization: Bearer <token>` | A service-account token. It expires after 15 minutes. |
| `SessionToken(token)` | `x-session-token` | A console session. Writes require a CSRF token, so in practice it is read-only. |

Access keys expire after 90 days by default and after 365 days at most. Once a
key has expired, the API answers `401`. Without a selected project, an access
key acts on its user's active project.

A service-account token is obtained from
`POST /api/v1/iam/service-accounts/token` and carries the account's pinned
project. `BearerToken` holds a fixed token and does not renew it. A
long-running process must obtain a new token and build a new client with it.

## Credentials from the environment

When `credential` is not given, the client calls `credential_from_env()`:

1. `NEMAU_ACCESS_KEY_ID` and `NEMAU_ACCESS_KEY_SECRET`, when both are set, give
   an `AccessKey`.
2. Otherwise `NEMAU_TOKEN` gives a `BearerToken`.
3. Otherwise the client has no credential.

The access key comes first so that a stale token left in a shell does not
replace the long-lived key. A `SessionToken` is never read from the
environment.

```sh
export NEMAU_ACCESS_KEY_ID=<access key id>
export NEMAU_ACCESS_KEY_SECRET=<secret access key>
```

```python
from nemau import NemauClient

client = NemauClient()
print(client.describe_credential())  # for example "access-key <id>", or "none"
```

## Explicit credentials

```python
from nemau import AccessKey, BearerToken, NemauClient, SessionToken

client = NemauClient(credential=AccessKey(key_id, key_secret))
client = NemauClient(credential=BearerToken(token))
client = NemauClient(credential=SessionToken(session_token))
```

The credential classes leave the secret out of their `repr()`, so it does not
appear in tracebacks or debug logs. `client.describe_credential()` returns a
redacted description of the credential in use.

Any object with `apply(headers)` and `describe()` methods can be passed as a
credential: it implements the `Credential` protocol. `apply` sets the
authentication headers on each outgoing request, and `describe` returns a
redacted label.

## Client settings

| Argument | Environment variable | Default |
| --- | --- | --- |
| `base_url` | `NEMAU_API_URL` | `https://api.nemau-cloud.com` (`nemau.DEFAULT_BASE_URL`) |
| `credential` | see above | none |
| `project` | `NEMAU_PROJECT_ID` | the credential's own project |
| `retry` | | `RetryPolicy()`, see [Errors and retries](errors-and-retries.md#retries) |
| `poll_interval` | | `2.0` seconds between two reads of a task |
| `timeout` | | `60.0` seconds for each HTTP request |
| `user_agent` | | none |
| `env` | | `os.environ` |

An argument passed to the constructor takes precedence over the environment.
`base_url` must be absolute (`scheme://host`), or the constructor raises
`ValueError`. The client adds the `/api/v1` prefix to every path.

`user_agent` is placed before the SDK's own identifier:

```python
client = NemauClient(user_agent="backup-job/1.4")
# User-Agent: backup-job/1.4 nemau-sdk-python/0.2.0
```

`env` replaces `os.environ` as the source of the environment variables. Pass
`env={}` to ignore the environment, for example in tests:

```python
client = NemauClient(credential=AccessKey(key_id, key_secret), env={})
```

The SDK version is available as `nemau.__version__`.
