# Compute

`client.compute` manages instances and their snapshots, and the catalogue they
are built from: images, flavors and keypairs.

## Instances

```python
for instance in client.compute.list_instances():
    print(instance.id, instance.name, instance.status, instance.addresses)

instance = client.compute.get_instance(instance_id)
```

`get_instance` raises a `404` `NemauApiError` when the id is unknown to the
project. `Instance.status` is `running`, `stopped`, `paused` or `pending`. An
empty `addresses` means that the platform could not report the addresses.

### Creating an instance

An instance needs a name, an image and a size. The size is either `flavor_id`
or all three of `vcpus`, `ram_mb` and `disk_gb`. The API rejects a partial
custom size.

```python
ref = client.compute.create_instance(
    name="web-1",
    image_id="ubuntu-24.04",
    vcpus=2,
    ram_mb=4096,
    disk_gb=40,
    network_id=network_id,
    security_groups=[group_id],
    key_name="deploy",
)
instance_id, task = client.wait_for_resource_id(ref, "instance_id")
```

The other optional arguments are `description` and `service_level`.
`flavor_id` requires the compute-flavors organization feature.

`create_instance_and_wait` takes the same arguments, waits for the instance to
be built and returns the `Instance`.

### Lifecycle

Each action returns a `TaskRef`.

```python
client.wait_for(client.compute.stop(instance_id))
client.wait_for(client.compute.start(instance_id))
client.compute.reboot(instance_id)  # SOFT
client.compute.reboot(instance_id, "HARD")
```

The other actions are `pause`, `unpause`, `suspend`, `resume` and
`delete_instance`. Deleting an instance deletes its snapshots. While a volume
is still attached, the delete fails with `resource_in_use`; see
[Failed tasks](errors-and-retries.md#failed-tasks).

### Resizing

```python
client.wait_for(client.compute.resize(instance_id, vcpus=4, ram_mb=8192, disk_gb=80))
```

All three values are required. The API accepts 1 to 64 vCPUs, 1024 to 262144 MB
of RAM and 10 to 5000 GB of disk.

### Security groups

`set_security_groups` replaces the security groups of an instance. An empty
list removes all filtering from the instance.

```python
client.compute.set_security_groups(instance_id, [web_group_id, ssh_group_id])
```

### Metrics

```python
metrics = client.compute.instance_metrics(instance_id, "24h")
```

The period is `1h` (the default), `6h`, `24h` or `7d`. Any other value raises
`ValueError` before the request is sent. The result is the decoded JSON
response, as a dictionary.

## Snapshots

A snapshot copies the system disk of an instance to the platform's object store
without stopping the instance. Attached block volumes are not included.

```python
ref = client.compute.create_snapshot(instance_id, "before-upgrade")
client.wait_for(ref)  # runs until the upload ends

for snapshot in client.compute.list_snapshots(instance_id):
    print(snapshot.id, snapshot.name, snapshot.status, snapshot.size_bytes, snapshot.monthly_cost)
```

The task runs until the upload to the object store ends, which takes minutes,
or hours for a large disk. Until then the snapshot is listed as `creating`.
`list_snapshots()` without an argument lists the snapshots of every instance in
the project.

A snapshot is billed per GB and month on the stored size, which is the size of
the compressed archive. `size_bytes`, `monthly_cost` (excluding VAT, for a
730-hour month) and `currency` are set once the snapshot is active.

`restore_snapshot` restores a snapshot onto the instance it was taken from:

```python
client.wait_for(client.compute.restore_snapshot(snapshot_id))
```

The data is first restored next to the running instance, then the system disk
is swapped. The instance stops only for the swap, and restarts if it was
running. Everything written since the snapshot is lost. Addresses, security
groups and attached volumes are kept, and the disk keeps its current size. Only
a snapshot with `restorable` set can be restored.

`delete_snapshot` deletes a snapshot. A snapshot that is being created or
restored cannot be deleted (`409`).

Older snapshots have `storage == "hypervisor"`. They can be listed and deleted,
are not billed, and cannot be restored.

## Images

`list_images()` returns `Image` objects with `id`, `name`, `os_name`, `status`
and `disk_format`. Pass the `id` as `image_id` when you create an instance.

## Flavors

Flavors are instance sizes defined by the organization. They require the
compute-flavors organization feature, and the API answers `403` without it.
Flavor writes are synchronous and return no task.

```python
flavor = client.compute.create_flavor(name="medium", vcpus=2, ram_mb=4096, disk_gb=40)
flavors = client.compute.list_flavors()
flavor = client.compute.update_flavor(flavor.id, name="medium", vcpus=4, ram_mb=8192, disk_gb=40)
client.compute.delete_flavor(flavor.id)
```

`update_flavor` requires all four values. `create_flavor` and `update_flavor`
also accept `service_level`.

## Keypairs

```python
with open("id_ed25519.pub") as handle:
    client.compute.create_keypair("deploy", public_key=handle.read())

generated = client.compute.create_keypair("ci")
# generated.private_key is returned only once.
```

`create_keypair` is synchronous and returns a `CreatedKeypair`. Without
`public_key`, the platform generates the key pair and `private_key` holds the
private key. It is not stored, and cannot be retrieved later.

`list_keypairs()` returns `Keypair` objects with `id`, `name`, `public_key`,
`fingerprint` and `type`. `delete_keypair(name)` deletes a keypair by name and
returns a `TaskRef`. A keypair whose name contains a slash can be created but
not deleted.

## Pricing

`get_pricing()` returns the compute price list as a dictionary, decoded from
the API response.

## Updates

`update_instance(instance_id, name=..., description=...)` renames an instance
or changes its description. See [Updating resources](updating-resources.md).
