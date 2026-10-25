# Metering

`client.metering` reads consumption and accrued cost for the organization of
the credential.

```python
import datetime

start = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)
end = datetime.datetime.now(datetime.timezone.utc)

costs = client.metering.costs(period_start=start, period_end=end)
summary = client.metering.usage_summary(period_start=start, period_end=end)
```

There are four methods: `usage`, `usage_summary`, `costs` and `events`. Each
takes these keyword arguments, all optional:

| Argument | Content |
| --- | --- |
| `resource_type` | Limits the results to one type of resource. |
| `period_start`, `period_end` | A `datetime` or a string. A `datetime` without a time zone is read as UTC. A string is sent as is. |
| `page`, `page_size` | Pagination. |

Other keyword arguments are ignored. The organization always comes from the
credential, so a `tenant_id` argument has no effect.

The responses do not follow a stable public schema. Each method returns the
decoded JSON response as a dictionary.
