# nemau guide

This guide describes the Python SDK for Nemau Cloud. For installation and a
first example, see the [project README](../README.md).

## Using the client

- [Authentication](authentication.md): credential types, environment variables
  and client settings.
- [Projects, quotas and regions](projects.md): how the client selects a
  project, and how to read projects, quotas and regions.
- [Asynchronous writes](asynchronous-writes.md): task references, waiting for
  tasks, deadlines.
- [Updating resources](updating-resources.md): the `update_*` methods and how
  they send only the fields you pass.
- [Errors and retries](errors-and-retries.md): exception classes, error
  helpers, retry policy, rate limits and idempotency keys.
- [Models](models.md): how responses are decoded, attribute names, unknown
  fields.

## Services

| Client attribute | Page |
| --- | --- |
| `client.compute` | [Compute](compute.md): instances, snapshots, images, flavors, keypairs |
| `client.network` | [Networking](networking.md): networks, subnets, IPv6, floating IPs, security groups |
| `client.load_balancer` | [Load balancers](load-balancers.md): tiers, listeners, TLS certificates, pools, members, health monitors |
| `client.databases` | [Managed PostgreSQL](databases.md): databases, the one-time password, TLS, restore, rotation |
| `client.instance_groups` | [Instance groups](instance-groups.md): launch templates, membership, target tracking, schedules |
| `client.storage` | [Block storage](block-storage.md): volumes |
| `client.storage` | [Object storage](object-storage.md): containers, objects, presigned URLs, S3 access keys |
| `client.kubernetes` | [Kubernetes](kubernetes.md): clusters, node pools, kubeconfig, upgrades |
| `client.dns` | [DNS](dns.md): zones, ownership verification, record sets |
| `client.iam` | [IAM](iam.md): users and access keys, read-only |
| `client.metering` | [Metering](metering.md): usage, costs and events |
| `client.projects`, `client.quotas`, `client.regions` | [Projects, quotas and regions](projects.md) |
| `client.tasks` | [Asynchronous writes](asynchronous-writes.md#reading-tasks) |

The examples assume a client built as in the README:

```python
from nemau import NemauClient

client = NemauClient()
```

The Nemau Cloud documentation has a page on the SDKs:
https://docs.nemau-cloud.com/en/tools/sdks
