# Block storage

Block volumes are disks that attach to instances. They are managed with
`client.storage`, which also covers [object storage](object-storage.md).

## Volumes

```python
volume = client.storage.create_volume_and_wait("data", 100, "database files")

for item in client.storage.list_volumes():
    print(item.id, item.name, item.size, item.status)
```

`create_volume(name, size_gb, description="")` returns a `TaskRef`, and
`create_volume_and_wait` waits and returns the `Volume`. Sizes are in GiB.
`get_volume(volume_id)` lists the volumes and filters them, and raises a `404`
`NemauApiError` for an unknown id.

A `Volume` has `id`, `name`, `description`, `size`, `status`, `attachments`,
`created_at` and `updated_at`. Each attachment has `server_id`, `server_name`,
`device` and `attached_at`.

## Attaching and detaching

```python
task = client.wait_for(client.storage.attach_volume(volume.id, instance_id))
print(task.result_str("device_id"))
```

The finished task carries `device_id`, the name of the disk under
`/dev/disk/by-id` in the guest.

```python
client.wait_for(client.storage.detach_volume(volume.id, instance_id))
```

An instance cannot be deleted while a volume is attached to it: the delete task
fails with `resource_in_use`. Detach the volume first.

## Growing a volume

```python
client.wait_for(client.storage.extend_volume(volume.id, 200))
```

`extend_volume` takes the new size in GiB. A volume only grows.

## Deleting a volume

```python
client.wait_for(client.storage.delete_volume(volume.id))
```

## Updates

`update_volume(volume_id, name=..., description=...)` changes the name or the
description. See [Updating resources](updating-resources.md).
