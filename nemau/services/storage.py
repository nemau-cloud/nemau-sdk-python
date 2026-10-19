"""/storage: block volumes and object containers.

Volume operations send the volume id as ``?id=`` and object operations send the
key as ``?key=``, not in the path. Both can contain slashes, which would add path
segments and stop the route from matching.
"""

from __future__ import annotations

import datetime
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from nemau.client import API_PREFIX, _unwrap
from nemau.errors import NemauApiError, not_found
from nemau.models import (
    CREDENTIAL_ACCESS_LEVELS,
    Container,
    ContainerProtection,
    CopiedObject,
    IssuedCredential,
    ObjectPage,
    PresignedObject,
    StorageCredential,
    StoredObject,
    TaskRef,
    UploadedObject,
    Volume,
)
from nemau.services import Service
from nemau.services.update import patch

#: The most objects one listing page holds.
MAX_LIST_PAGE = 1000

#: The largest object the API accepts for an upload or a download. Presigned
#: URLs are not subject to it.
MAX_OBJECT_SIZE = 100 * 1024 * 1024

#: The largest object a server-side copy accepts: the limit of a single S3
#: CopyObject. For larger objects, use the project's S3 keys with rclone or the
#: AWS CLI.
MAX_COPY_SIZE = 5 * 1024 * 1024 * 1024

#: Longest expiry the API accepts for a presigned URL.
PRESIGN_TTL_CAP_SECONDS = 3600

#: Retention modes for an immutable container.
#:
#: GOVERNANCE retention can be lifted by the platform operator; COMPLIANCE
#: retention cannot be lifted by anyone before it expires. Customer credentials
#: cannot lift either mode.
RETENTION_GOVERNANCE = "GOVERNANCE"
RETENTION_COMPLIANCE = "COMPLIANCE"
#: Longest default retention the API accepts: seven years.
MAX_RETENTION_DAYS = 2555


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class StorageService(Service):
    """Block volumes and object storage."""

    # -- volumes ------------------------------------------------------- #

    def list_volumes(self) -> list[Volume]:
        payload = self._client.request("GET", "/storage/volumes")
        return [Volume.from_dict(item) for item in _unwrap(payload, "volumes")]

    def get_volume(self, volume_id: str) -> Volume:
        """The API has no per-volume read: list and filter."""
        for item in self.list_volumes():
            if item.id == volume_id:
                return item
        raise not_found("volume", volume_id, "/storage/volumes")

    def create_volume(self, name: str, size_gb: int, description: str = "") -> TaskRef:
        """``size_gb`` is in GiB."""
        return self._client.submit(
            "POST",
            "/storage/volumes",
            json={"name": name, "description": description, "size": size_gb},
        )

    def create_volume_and_wait(self, name: str, size_gb: int, description: str = "") -> Volume:
        ref = self.create_volume(name, size_gb, description)
        volume_id, _ = self._client.wait_for_resource_id(ref, "volume_id")
        return self.get_volume(volume_id)

    def delete_volume(self, volume_id: str) -> TaskRef:
        return self._client.submit("DELETE", "/storage/volumes", query={"id": volume_id})

    def attach_volume(self, volume_id: str, instance_id: str) -> TaskRef:
        """The finished task carries ``device_id``, the /dev/disk/by-id name in the guest."""
        return self._client.submit(
            "POST",
            "/storage/volumes/attach",
            query={"id": volume_id},
            json={"instance_id": instance_id},
        )

    def detach_volume(self, volume_id: str, instance_id: str) -> TaskRef:
        return self._client.submit(
            "POST",
            "/storage/volumes/detach",
            query={"id": volume_id},
            json={"instance_id": instance_id},
        )

    def extend_volume(self, volume_id: str, new_size_gb: int) -> TaskRef:
        """A volume only ever grows."""
        return self._client.submit(
            "PUT",
            "/storage/volumes/extend",
            query={"id": volume_id},
            json={"new_size": new_size_gb},
        )

    # -- containers ----------------------------------------------------- #

    def list_containers(self) -> list[Container]:
        payload = self._client.request("GET", "/storage/containers")
        return [Container.from_dict(item) for item in _unwrap(payload, "containers")]

    def get_container(self, name: str) -> Container:
        for item in self.list_containers():
            if item.name == name:
                return item
        raise not_found("container", name, "/storage/containers")

    def create_container(
        self,
        name: str,
        *,
        versioning: bool = False,
        object_lock: bool = False,
        retention_mode: str = "",
        retention_days: int = 0,
    ) -> TaskRef:
        """Creates a container, optionally versioned and immutable.

        ``object_lock`` makes stored versions immutable for ``retention_days``,
        for example to protect backups against ransomware.

        Protection can only be set at creation: S3 does not allow object lock
        to be added to an existing bucket.
        """
        if not object_lock and (retention_days or retention_mode):
            raise ValueError("nemau: a retention policy requires object_lock")
        if retention_days > MAX_RETENTION_DAYS:
            raise ValueError(f"nemau: retention_days must not exceed {MAX_RETENTION_DAYS}")
        if retention_mode not in ("", RETENTION_GOVERNANCE, RETENTION_COMPLIANCE):
            raise ValueError(
                f"nemau: retention_mode must be {RETENTION_GOVERNANCE!r} "
                f"or {RETENTION_COMPLIANCE!r}"
            )
        body: dict[str, Any] = {"name": name}
        if versioning or object_lock:
            body["versioning"] = True
        if object_lock:
            body["object_lock"] = True
        if retention_mode:
            body["retention_mode"] = retention_mode
        if retention_days:
            body["retention_days"] = retention_days
        return self._client.submit("POST", "/storage/containers", json=body)

    def container_protection(self, container: str) -> ContainerProtection:
        """Reads the protection the object store enforces for a container.

        ``Container.protection`` reports what was requested at creation; this
        method reports what is in force, so an audit can compare the two.
        """
        payload = self._client.request("GET", f"/storage/containers/{_quote(container)}/protection")
        return ContainerProtection.from_dict((payload or {}).get("protection") or {})

    def delete_container(self, name: str) -> TaskRef:
        return self._client.submit("DELETE", f"/storage/containers/{_quote(name)}")

    # -- objects -------------------------------------------------------- #

    def list_objects(
        self,
        container: str,
        *,
        prefix: str = "",
        marker: str = "",
        limit: int = 0,
    ) -> ObjectPage:
        """One page of a container's objects, in key order.

        ``limit`` is 1 to 1000 (0 leaves it to the server, which defaults to
        100). ``marker`` is the opaque cursor of a previous page's
        ``next_marker``, passed back verbatim: never build one from a key. The
        listing is complete when ``next_marker`` comes back empty. To walk it
        all, use :meth:`iter_objects`.
        """
        if limit < 0 or limit > MAX_LIST_PAGE:
            raise ValueError(f"nemau: limit must be between 1 and {MAX_LIST_PAGE}")
        query: dict[str, str] = {}
        if prefix:
            query["prefix"] = prefix
        if marker:
            query["marker"] = marker
        if limit > 0:
            query["limit"] = str(limit)
        payload = self._client.request(
            "GET", f"/storage/containers/{_quote(container)}/objects", query=query or None
        )
        return ObjectPage.from_dict(payload or {})

    def iter_objects(
        self, container: str, *, prefix: str = "", page_size: int = 0
    ) -> Iterator[StoredObject]:
        """Every object of a container, fetching one page at a time.

        Follows ``next_marker`` until the server returns none. ``page_size`` is
        1 to 1000 (0 for the server's default of 100): it only sizes the
        requests.
        """
        marker = ""
        while True:
            page = self.list_objects(container, prefix=prefix, marker=marker, limit=page_size)
            yield from page.objects
            if not page.next_marker:
                return
            if page.next_marker == marker:
                # A cursor that does not advance would loop forever.
                raise NemauApiError(
                    502,
                    "the object listing returned the same cursor twice",
                    method="GET",
                    path=f"{API_PREFIX}/storage/containers/{_quote(container)}/objects",
                )
            marker = page.next_marker

    def upload_object(
        self,
        container: str,
        key: str,
        body: bytes | str,
        content_type: str = "application/octet-stream",
    ) -> UploadedObject:
        """Uploads an object into a container.

        Synchronous, unlike most storage writes. The API requires an exact
        Content-Length and refuses anything over :data:`MAX_OBJECT_SIZE`. The
        request is not retried.
        """
        payload_bytes = body.encode("utf-8") if isinstance(body, str) else body
        if len(payload_bytes) > MAX_OBJECT_SIZE:
            raise ValueError(f"nemau: object exceeds the {MAX_OBJECT_SIZE}-byte limit")
        answer = self._client.request(
            "POST",
            f"/storage/containers/{_quote(container)}/objects",
            query={"key": key},
            body=payload_bytes,
            content_type=content_type,
        )
        return UploadedObject.from_dict(answer or {})

    def delete_object(self, container: str, key: str) -> None:
        """Synchronous delete: answers 204."""
        self._client.request(
            "DELETE", f"/storage/containers/{_quote(container)}/objects", query={"key": key}
        )

    def copy_object(
        self,
        container: str,
        key: str,
        *,
        destination_container: str | None = None,
        destination_key: str | None = None,
        replace_metadata: bool = False,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> CopiedObject:
        """Copies an object server-side, within its container or into another one.

        The bytes pass through neither the API nor the client, so
        :data:`MAX_OBJECT_SIZE` does not apply; the limit is
        :data:`MAX_COPY_SIZE`, that of a single S3 CopyObject. A larger source
        answers 413: copy it with S3 keys scoped to the containers
        (:meth:`create_credential`) and rclone or the AWS CLI.

        Both containers must belong to the current project. Synchronous, like
        uploads: the copy exists when this returns, and there is no task to
        wait for.

        ``destination_container`` and ``destination_key`` default to the
        source's. The source's content type and metadata are kept unless
        ``replace_metadata`` is set, which ``content_type`` and ``metadata``
        require. Copying an object onto itself is refused unless it replaces
        them, which is how an object's metadata is changed.

        A copy into a container with object lock receives that container's
        default retention: the source's retention does not follow, and a
        shorter one cannot be chosen.
        """
        if not replace_metadata and (content_type or metadata):
            raise ValueError("nemau: content_type and metadata require replace_metadata")
        body: dict[str, Any] = {"key": key}
        if destination_container:
            body["destination_container"] = destination_container
        if destination_key:
            body["destination_key"] = destination_key
        if replace_metadata:
            body["replace_metadata"] = True
        if content_type:
            body["content_type"] = content_type
        if metadata:
            body["metadata"] = metadata
        payload = self._client.request(
            "POST", f"/storage/containers/{_quote(container)}/copy", json=body
        )
        return CopiedObject.from_dict(payload or {})

    # -- S3 access keys ------------------------------------------------- #

    def create_credential(
        self,
        container: str,
        access: str,
        *,
        expires_at: datetime.datetime | None = None,
    ) -> IssuedCredential:
        """Creates an S3 access key scoped to one container.

        Presigned URLs cover one object at a time; a key works with
        ``aws s3 sync``, ``rclone sync`` and anything else that speaks S3. Each
        container holds a limited number of keys (see ``is_credential_limit``)
        and each key can be revoked on its own.

        ``access`` is ``CREDENTIAL_READ_ONLY`` or ``CREDENTIAL_READ_WRITE``.
        ``expires_at`` (timezone-aware, optional) revokes the key at that instant;
        it must be in the future and within the platform's maximum lifetime (the
        server decides). The secret is returned here only.

        The caller needs permission to manage storage credentials
        (``storage:ManageCredentials``). When the cap is reached
        ``is_credential_limit`` is true, and the error's ``limit`` and ``scope``
        say how many keys are allowed and what that counts.
        """
        if not container:
            raise ValueError("container is required")
        if access not in CREDENTIAL_ACCESS_LEVELS:
            raise ValueError(f"access must be one of {', '.join(CREDENTIAL_ACCESS_LEVELS)}")
        body: dict[str, Any] = {"access": access}
        if expires_at is not None:
            if expires_at.tzinfo is None:
                raise ValueError("expires_at must be timezone-aware")
            utc = expires_at.astimezone(datetime.timezone.utc).replace(microsecond=0)
            body["expires_at"] = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = self._client.request(
            "POST", f"/storage/containers/{_quote(container)}/credentials", json=body
        )
        return IssuedCredential.from_dict(payload or {})

    def list_credentials(self, container: str) -> list[StorageCredential]:
        """A container's access keys. Secrets never come back."""
        if not container:
            raise ValueError("container is required")
        payload = self._client.request(
            "GET", f"/storage/containers/{_quote(container)}/credentials"
        )
        return [StorageCredential.from_dict(item) for item in _unwrap(payload, "credentials")]

    def revoke_credential(self, container: str, access_key_id: str) -> None:
        """Revokes one access key of a container."""
        if not container or not access_key_id:
            raise ValueError("container and access key id are required")
        self._client.request(
            "DELETE",
            f"/storage/containers/{_quote(container)}/credentials/{_quote(access_key_id)}",
        )

    def presign_get(self, container: str, key: str, ttl_seconds: int = 0) -> PresignedObject:
        """Returns a URL that downloads one object without going through the API.

        Use it for bulk exports. The other object methods pass the data through
        the API, which limits each transfer to :data:`MAX_OBJECT_SIZE`. A
        presigned URL is fetched directly from the object store, so that limit
        does not apply.
        """
        return self._presign(container, key, "GET", ttl_seconds)

    def presign_put(self, container: str, key: str, ttl_seconds: int = 0) -> PresignedObject:
        """Returns a URL that uploads one object without going through the API,
        lifting :data:`MAX_OBJECT_SIZE` for a migration in."""
        return self._presign(container, key, "PUT", ttl_seconds)

    def _presign(self, container: str, key: str, method: str, ttl_seconds: int) -> PresignedObject:
        if ttl_seconds > PRESIGN_TTL_CAP_SECONDS:
            raise ValueError(
                f"nemau: requested expiry ({ttl_seconds}s) exceeds the "
                f"{PRESIGN_TTL_CAP_SECONDS}s cap"
            )
        body: dict[str, Any] = {"key": key, "method": method}
        if ttl_seconds > 0:
            body["expires_in"] = ttl_seconds
        payload = self._client.request(
            "POST", f"/storage/containers/{_quote(container)}/presign", json=body
        )
        return PresignedObject.from_dict(payload or {})

    def fetch_presigned(self, presigned: PresignedObject) -> bytes:
        """Fetches a presigned URL.

        The request does not carry the client's credential: the URL is already
        authorised, and the object store is a different host from the API.
        """
        request = urllib.request.Request(presigned.url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as error:
            # The URL is left out of the error: its signature is a live
            # credential, and error messages end up in logs.
            raise NemauApiError(
                error.code,
                "presigned download refused",
                method="GET",
                path="(presigned url)",
            ) from None

    def download_object(self, container: str, key: str) -> bytes:
        """Downloads an object.

        The body is returned in memory: the platform caps an object at
        :data:`MAX_OBJECT_SIZE`, which bounds the cost.
        """
        return self._client.request_raw(
            "GET", f"/storage/containers/{_quote(container)}/download", query={"key": key}
        )

    def update_volume(
        self, volume_id: str, *, name: str | None = None, description: str | None = None
    ) -> TaskRef:
        """Changes a volume's name or description.

        Its size changes with :meth:`extend_volume`, which only ever grows.
        """
        return patch(
            self._client,
            "update_volume",
            "/storage/volumes",
            {"name": name, "description": description},
            query={"id": volume_id},
        )

    def update_container(
        self, name: str, *, versioning: bool | None = None, retention_days: int | None = None
    ) -> TaskRef:
        """Changes a container's versioning or retention.

        The name cannot change, and retention can be extended but not
        shortened.
        """
        return patch(
            self._client,
            "update_container",
            f"/storage/containers/{_quote(name)}",
            {"versioning": versioning, "retention_days": retention_days},
        )


__all__ = [
    "StorageService",
    "MAX_OBJECT_SIZE",
    "PRESIGN_TTL_CAP_SECONDS",
    "RETENTION_GOVERNANCE",
    "MAX_RETENTION_DAYS",
    "RETENTION_COMPLIANCE",
]
