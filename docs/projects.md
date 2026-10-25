# Projects, quotas and regions

The organization comes from the credential, and a call cannot leave it. The
project comes from the `project` argument when it is set, then from
`NEMAU_PROJECT_ID`, and from the credential otherwise.

## Selecting a project

```python
from nemau import NemauClient

client = NemauClient(project="<project id>")
```

When a project is selected, each request names it in the `X-Project-Id` header
(`nemau.PROJECT_SCOPE_HEADER`). When none is selected, the header is not sent
and the credential's own scope applies. `client.project` holds the selected
project, or an empty string.

`for_project` returns a client for another project. The new client has the same
base URL, credential, retry policy, poll interval and timeout. The original
client is not modified, so calls already in progress on it keep their project.

```python
staging = client.for_project(staging_id)
prod = client.for_project(prod_id)
```

The selected project is not stored on the platform, so two clients built from
the same credential can work on two projects at the same time. A service
account pinned to a project cannot leave it: the API answers `403`.

## Listing projects

`client.projects` reads the projects the credential can act on. Projects are
created and deleted from the web console, so this service only reads.

```python
listing = client.projects.list()
for project in listing.projects:
    print(project.id, project.name, project.slug, project.region, project.ready)

project = client.projects.get("staging")  # an id, a name or a slug
current = client.projects.current()
```

`get` looks for a matching id first, then for a name or a slug, so an id is
never ambiguous. An unknown project raises a `404` `NemauApiError`.

`current()` returns the project the credential resolves to, or `None`. `None`
is the normal result for an organization-wide service account with no project
selected.

`Project.ready` is true when the project is active and its provisioning has
finished. A project that is inactive or still provisioning is listed, but it
refuses every write.

`ProjectList.current_project_id` is the project the request resolved to. It can
name a project that is absent from `projects`, for example when the credential
points at a deleted project.

## Quotas

A write is accepted only when both the project and its organization have room.
`client.quotas.get()` returns the limits, the settled usage and the pending
reservations for both scopes.

```python
quotas = client.quotas.get()

project = quotas.scope("project")  # "tenant" for the organization
if project is not None:
    for dimension in project.dimensions:
        print(dimension.name, dimension.unit, dimension.used, dimension.reserved, dimension.limit)

cores = quotas.binding("cores")
if cores is not None:
    print("cores left:", cores.available)
```

`reserved` is the capacity claimed by tasks that were admitted but have not
finished. The API compares used, reserved and requested capacity to the limit,
so `available` subtracts both `used` and `reserved`, and is never negative.

`binding(name)` returns the dimension with the least headroom across both
scopes, which is the one that will refuse first. The project's figures alone
can show capacity that the organization's limit does not allow.

The `unit` of a dimension is `count`, `MB`, `GB` or `bytes`. RAM is counted in
MB and volume size in GB, so check the unit before comparing dimensions.

## Regions

The region endpoints do not require authentication. A client without a
credential can list regions, for example to check that the API is reachable.

```python
for region in client.regions.list():
    print(region.slug, region.display_name, region.country, region.enabled)

status = client.regions.status(region.slug)
print(status.healthy, status.checked_at)
```

An unhealthy region answers `503`. `status()` then raises a `NemauApiError`
once the retries are spent.
