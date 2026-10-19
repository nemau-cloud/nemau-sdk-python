# IAM

`client.iam` reads the users of the organization and their access keys. These
endpoints serve the web console. They require a console session
(`SessionToken`), and the API refuses an access key or a bearer token with
`401`. Their writes require a CSRF token, so the SDK only reads.

```python
from nemau import NemauClient, SessionToken

client = NemauClient(credential=SessionToken(session_token))
```

## Users

```python
page = client.iam.list_users(page=1, limit=50)
for user in page.data:
    print(user.id, user.username, user.email, user.type, user.totp_enabled)
print(page.total, page.total_pages, page.has_more)
```

`list_users` returns one page of users. Only owners can call it. `page` starts
at 1, and `limit` is 1 to 100 (20 by default); other values raise `ValueError`.
A user's `type` is `owner` or `member`.

`list_users` calls `/iam/users`. The older `/account/iam/users` is deprecated
and will be removed on 2027-04-10.

## Access keys

```python
keys = client.iam.list_access_keys(user_id)
for key in keys:
    print(key.access_key_id, key.status, key.expires_at, key.last_used_at)

limits = client.iam.access_key_limits()
print(limits.current, limits.max_access_keys)
```

Members see their own keys, and owners see anyone's. Secrets are never
returned. A key's `status` is `active` or `inactive`, and `expires_at` is
`None` on older keys that do not expire.

Access keys expire after 90 days by default and after 365 days at most.

`access_key_limits()` returns the cap on active keys and how many the caller
holds. With a `user_id`, it returns the figures of that user, which only owners
may read (`403` otherwise). A user holds at most 5 active keys. Creating or
reactivating a key beyond the cap is refused with a `409`, which
`is_access_key_limit` recognises.
