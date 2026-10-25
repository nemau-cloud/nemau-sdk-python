"""Data models for API responses.

Python attribute names are snake_case. The API uses snake_case for most
resources but camelCase for tasks, Kubernetes clusters and a few others, so
each class declares the key mapping it needs.

Every model is built with :meth:`Model.from_dict`, which keeps unknown keys in
``extra`` instead of raising: a field added to the API does not break an
existing SDK release.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any, TypeVar

T = TypeVar("T", bound="Model")

#: Attribute name to JSON key, where the two differ.
_ALIASES = "_aliases"


@dataclass
class Model:
    """Model base: lenient decoding from a JSON dictionary."""

    #: Fields received that this SDK version does not know. Keeping them lets
    #: a caller read a brand-new field without waiting for a new release.
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _aliases_map(cls) -> Mapping[str, str]:
        return getattr(cls, _ALIASES, {})

    @classmethod
    def from_dict(cls: type[T], payload: Mapping[str, Any]) -> T:
        aliases = cls._aliases_map()
        known = {f.name for f in fields(cls) if f.name != "extra"}
        values: dict[str, Any] = {}
        extra: dict[str, Any] = {}

        reverse = {aliases.get(name, name): name for name in known}
        for key, value in payload.items():
            attribute = reverse.get(key)
            if attribute is None:
                extra[key] = value
                continue
            values[attribute] = _convert(cls, attribute, value)

        values["extra"] = extra
        return cls(**values)

    @classmethod
    def list_from(cls: type[T], payload: Any) -> list[T]:
        if not isinstance(payload, list):
            return []
        return [cls.from_dict(item) for item in payload if isinstance(item, dict)]


#: Nested fields: attribute to (model, is_list).
_NESTED: dict[str, Any] = {}


def _convert(cls: type, attribute: str, value: Any) -> Any:
    spec = _NESTED.get(f"{cls.__name__}.{attribute}")
    if spec is None or value is None:
        return value
    model, is_list = spec
    if is_list:
        return model.list_from(value)
    return model.from_dict(value) if isinstance(value, dict) else None


def _nest(owner: type, attribute: str, model: type, *, many: bool = False) -> None:
    _NESTED[f"{owner.__name__}.{attribute}"] = (model, many)


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


class TaskStatus:
    """The statuses of a task."""

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})


@dataclass
class TaskRef(Model):
    """The 202 answer to a write.

    It names the queued work and nothing more: the resource does not exist yet
    when this comes back.
    """

    task_id: str = ""
    status: str = ""
    queue: str = ""
    message: str = ""
    #: True when the server recognised the write's ``Idempotency-Key`` and
    #: answered with the task of an earlier identical request instead of
    #: queueing a new one. Set from the ``Idempotent-Replayed`` response header.
    replayed: bool = False


#: A delete the platform refused because something still depends on the resource
#: (a volume attached to an instance, a subnet on a network). Remove the
#: dependant first, then retry; repeating the same call cannot succeed.
ERROR_CODE_RESOURCE_IN_USE = "resource_in_use"


@dataclass
class Task(Model):
    """A queued operation, as the API exposes it.

    Internal details are not included: parameters, retry counters, the
    idempotency key, the user who started it, and the raw error.
    """

    _aliases = {
        "task_type": "taskType",
        "error_code": "errorCode",
        "resource_id": "resourceId",
        "resource_type": "resourceType",
        "created_at": "createdAt",
        "completed_at": "completedAt",
    }

    id: str = ""
    task_type: str = ""
    status: str = ""
    progress: int = 0
    resource_id: str | None = None
    resource_type: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    #: Machine-readable cause of some failures, so a caller can react without
    #: parsing ``error``. Currently only :data:`ERROR_CODE_RESOURCE_IN_USE`.
    #: ``None`` for any other failure, and on a task that did not fail.
    error_code: str | None = None
    created_at: str = ""
    completed_at: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TaskStatus.TERMINAL

    @property
    def succeeded(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    def result_str(self, *keys: str) -> str:
        """Reads a string from the result, trying each key in order.

        Task results are untyped dictionaries whose keys depend on the
        operation: the caller says which keys may hold the value.
        """
        for key in keys:
            value = self.result.get(key)
            if isinstance(value, str) and value:
                return value
        return ""


# --------------------------------------------------------------------------- #
# Compute
# --------------------------------------------------------------------------- #


@dataclass
class Ref(Model):
    """The {id, name} envelope used for image, flavor and security groups."""

    id: str = ""
    name: str = ""


@dataclass
class Sizing(Model):
    """A node's CPU/RAM/disk shape."""

    vcpus: int = 0
    ram_mb: int = 0
    disk_gb: int = 0


@dataclass
class Instance(Model):
    """A compute instance.

    A field the platform cannot report is omitted rather than set to zero, so
    an empty ``addresses`` means "unknown", not "no address".
    """

    id: str = ""
    name: str = ""
    description: str = ""
    #: running | stopped | paused | pending
    status: str = ""
    tenant_id: str = ""
    project_id: str = ""
    user_id: str = ""
    flavor: Ref | None = None
    sizing: Sizing | None = None
    image: Ref | None = None
    key_name: str = ""
    addresses: list[str] = field(default_factory=list)
    security_groups: list[Ref] = field(default_factory=list)
    #: The instance group the instance belongs to, None when it belongs to none.
    instance_group_id: str | None = None
    created: str | None = None
    #: none | daily | weekly. The API always reports it; ``none`` means no
    #: scheduled backup.
    backup_schedule: str = "none"
    #: Hour of the day (UTC) at which the backup starts. None when unset.
    backup_hour: int | None = None
    #: 0 (Sunday) to 6 (Saturday). Only meaningful when ``backup_schedule`` is
    #: ``weekly``. None when unset.
    backup_weekday: int | None = None
    #: Days each scheduled backup is kept. None when unset.
    backup_retention_days: int | None = None
    #: SHA-256 of the ``user_data`` the instance was created with, lowercase
    #: hex. Empty when none was set. The content is never returned.
    user_data_sha256: str = ""


@dataclass
class Snapshot(Model):
    """A crash-consistent copy of an instance's system disk.

    ``storage`` is ``object_store`` for compressed archives on the platform's
    object store, which are billed on ``size_bytes`` and can be restored. Older
    snapshots report ``hypervisor``: they can be listed and deleted, are not
    billed, and cannot be restored. Attached block volumes are not included.

    ``size_bytes``, ``monthly_cost`` and ``currency`` are set once an
    object-store snapshot is active, and ``None`` otherwise.
    """

    id: str = ""
    name: str = ""
    instance_id: str = ""
    #: creating | active | restoring
    status: str = ""
    #: object_store | hypervisor
    storage: str = ""
    #: Bytes the object store actually holds (the compressed archive).
    size_bytes: int | None = None
    #: Capacity of the system disk that was captured.
    source_disk_gb: int | None = None
    #: Price excluding VAT of keeping ``size_bytes`` for a 730-hour month.
    monthly_cost: float | None = None
    currency: str | None = None
    restorable: bool = False
    created_at: str | None = None
    #: manual | scheduled. A scheduled snapshot comes from the instance's
    #: backup policy.
    origin: str = ""
    #: RFC 3339 time at which a scheduled snapshot is deleted. None for a manual
    #: snapshot, which never expires on its own.
    expires_at: str | None = None


@dataclass
class Image(Model):
    id: str = ""
    name: str = ""
    os_name: str = ""
    status: str = ""
    disk_format: str = ""


@dataclass
class Flavor(Model):
    """An organization-defined flavor. Requires the compute-flavors feature."""

    id: str = ""
    tenant_id: str = ""
    name: str = ""
    vcpus: int = 0
    ram_mb: int = 0
    disk_gb: int = 0
    service_level: str = ""
    created_by: str = ""


@dataclass
class Keypair(Model):
    id: str = ""
    name: str = ""
    public_key: str = ""
    fingerprint: str = ""
    type: str = ""


@dataclass
class CreatedKeypair(Model):
    """The answer to a keypair creation.

    ``private_key`` is set only when the platform generated the key, that is,
    when the request carried no public key. It is not stored and cannot be
    retrieved later.
    """

    name: str = ""
    public_key: str = ""
    private_key: str = ""
    task: Task | None = None


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #


@dataclass
class Network(Model):
    id: str = ""
    name: str = ""
    description: str = ""
    status: str = ""
    tenant_id: str = ""
    project_id: str = ""
    external: bool = False


@dataclass
class AllocationPool(Model):
    start: str = ""
    end: str = ""


#: IP versions, for :attr:`Subnet.ip_version`, :attr:`FloatingIP.ip_version`
#: and the calls that choose one.
IPV4 = 4
IPV6 = 6


@dataclass
class Subnet(Model):
    """An IPv6 subnet is a /64 the platform assigns from the region's routed
    prefix: its CIDR and gateway come back here, and it has no allocation
    pools."""

    id: str = ""
    name: str = ""
    network_id: str = ""
    cidr: str = ""
    #: 4 or 6. A region that predates IPv6 omits it, which means 4.
    ip_version: int = IPV4
    gateway_ip: str = ""
    address_assignment: str = ""
    dns_servers: list[str] = field(default_factory=list)
    allocation_pools: list[AllocationPool] = field(default_factory=list)


@dataclass
class InterfaceAddress(Model):
    subnet_id: str = ""
    address: str = ""


@dataclass
class NetworkInterface(Model):
    id: str = ""
    slot: str = ""
    network_id: str = ""
    instance_id: str = ""
    instance_name: str = ""
    mac_address: str = ""
    status: str = ""
    addresses: list[InterfaceAddress] = field(default_factory=list)


@dataclass
class FloatingIP(Model):
    """An IPv6 floating IP is translated to the target's private IPv4 address
    on the region's exit node: the guest keeps an IPv4-only configuration and
    sees IPv6 clients as addresses of the translation range. A target holds at
    most one."""

    id: str = ""
    address: str = ""
    #: 4 or 6. A region that predates IPv6 omits it, which means 4.
    ip_version: int = IPV4
    description: str = ""
    external_network_id: str = ""
    private_address: str = ""
    #: instance | load_balancer
    target_type: str = ""
    target_id: str = ""
    status: str = ""


@dataclass
class NetworkCapabilities(Model):
    """Optional network features the region offers. Both are off in a region
    without an IPv6 prefix."""

    ipv6_floating_ips: bool = False
    ipv6_subnets: bool = False


@dataclass
class SecurityGroupRule(Model):
    id: str = ""
    security_group_id: str = ""
    direction: str = ""
    action: str = ""
    protocol: str = ""
    port_range_min: int = 0
    port_range_max: int = 0
    remote_ip_prefix: str = ""


@dataclass
class SecurityGroup(Model):
    """``egress_filtering`` is read-only. ``True`` for a group whose egress
    rules are enforced: every group created now, and older groups once
    converted with ``network.enable_security_group_egress_filtering``. ``False``
    for a group created before egress filtering existed: it lets all outbound
    traffic through and its egress rules have no effect."""

    id: str = ""
    name: str = ""
    description: str = ""
    tenant_id: str = ""
    project_id: str = ""
    egress_filtering: bool = False
    rules: list[SecurityGroupRule] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


@dataclass
class VolumeAttachment(Model):
    id: str = ""
    server_id: str = ""
    server_name: str = ""
    device: str = ""
    attached_at: str = ""


@dataclass
class Volume(Model):
    """A block volume. ``size`` is in GiB."""

    id: str = ""
    name: str = ""
    description: str = ""
    size: int = 0
    status: str = ""
    tenant_id: str = ""
    project_id: str = ""
    attachments: list[VolumeAttachment] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class ContainerProtection(Model):
    """A container's versioning and object-lock state."""

    #: ``Enabled``, ``Suspended``, or empty.
    versioning: str = ""
    object_lock: bool = False
    retention_mode: str = ""
    retention_days: int = 0

    @property
    def immutable(self) -> bool:
        """Whether WORM protection is enforced.

        Versioning alone is not enough: without object lock, a delete still
        removes a version.
        """
        return self.object_lock and self.versioning == "Enabled"


@dataclass
class Container(Model):
    id: str = ""
    name: str = ""
    bytes: int = 0
    count: int = 0
    #: The protection requested at creation, ``None`` for a plain container.
    #: For what the object store enforces, see ``storage.container_protection()``.
    protection: ContainerProtection | None = None
    #: The bucket name an S3 tool addresses with one of this container's keys.
    #: It differs from ``name``: in the object store, each container has its
    #: own opaque bucket name.
    s3_bucket: str = ""


@dataclass
class StoredObject(Model):
    name: str = ""
    bytes: int = 0
    content_type: str = ""
    last_modified: str = ""


@dataclass
class ObjectPage(Model):
    """One page of a container listing."""

    objects: list[StoredObject] = field(default_factory=list)
    #: Empty on the last page; pass it back as ``marker`` for the next one.
    next_marker: str = ""
    count: int = 0


@dataclass
class UploadedObject(Model):
    object_name: str = ""
    size: int = 0


#: What an S3 access key may do inside its container.
CREDENTIAL_READ_ONLY = "read_only"
CREDENTIAL_READ_WRITE = "read_write"
CREDENTIAL_ACCESS_LEVELS = (CREDENTIAL_READ_ONLY, CREDENTIAL_READ_WRITE)

#: Stable codes carried by NemauApiError.code.
ERROR_CODE_CREDENTIAL_LIMIT = "credential_limit"
ERROR_CODE_ORGANIZATION_READ_ONLY = "organization_read_only"
ERROR_CODE_INVALID_REQUEST = "invalid_request"


@dataclass
class StorageCredential(Model):
    """An S3 access key scoped to a single object container.

    It never carries the secret: that exists only on the
    :class:`IssuedCredential` returned when the key is created.
    """

    access_key_id: str = ""
    access: str = ""
    container: str = ""
    created_at: str = ""
    #: None for a key that never expires.
    expires_at: str | None = None


@dataclass(repr=False)
class IssuedCredential(StorageCredential):
    """The result of creating a key.

    ``secret_access_key`` is only returned here. The platform does not store
    it, so if it is lost, create a new key. ``repr()`` and ``str()`` print
    ``[redacted]`` in its place; read the attribute directly to get the value.
    """

    secret_access_key: str = ""
    #: The bucket name to address with this key from an S3 tool
    #: (``aws s3 sync s3://<s3_bucket>``, ``rclone sync remote:<s3_bucket>``).
    #: It is not the container name: each container has its own opaque bucket
    #: name in the object store, and the key's policy covers that bucket.
    s3_bucket: str = ""
    region: str = ""
    #: Empty when the platform has no reachable S3 endpoint to show (a
    #: demonstration environment).
    endpoint: str = ""

    def __repr__(self) -> str:
        return (
            f"IssuedCredential(access_key_id={self.access_key_id!r}, "
            f"container={self.container!r}, s3_bucket={self.s3_bucket!r}, "
            f"access={self.access!r}, region={self.region!r}, endpoint={self.endpoint!r}, "
            f"expires_at={self.expires_at!r}, secret_access_key='[redacted]')"
        )

    __str__ = __repr__


@dataclass
class PresignedObject(Model):
    """A temporary URL to an object, usable directly against the store.

    Anyone holding the URL can use it until it expires, so do not log or share
    it.
    """

    url: str = ""
    method: str = ""
    expires_at: str = ""
    max_bytes: int = 0


@dataclass
class ObjectRetention(Model):
    """The retention an object version is locked under."""

    mode: str = ""
    retain_until: str = ""


@dataclass
class CopiedObject(Model):
    """The object a server-side copy created."""

    container: str = ""
    key: str = ""
    size: int = 0
    etag: str = ""
    #: Empty when the destination container is not versioned.
    version_id: str = ""
    last_modified: str = ""
    #: The destination container's default retention, ``None`` when it has none.
    retention: ObjectRetention | None = None


# --------------------------------------------------------------------------- #
# Load balancer
# --------------------------------------------------------------------------- #


@dataclass
class Listener(Model):
    _aliases = {"load_balancer_id": "loadbalancer_id"}

    id: str = ""
    name: str = ""
    description: str = ""
    load_balancer_id: str = ""
    protocol: str = ""
    protocol_port: int = 0
    default_pool_id: str = ""
    connection_limit: int = 0
    #: ID of the :class:`Certificate` a TERMINATED_HTTPS listener serves.
    tls_certificate_ref: str = ""
    admin_state_up: bool = True
    provisioning_status: str = ""
    operating_status: str = ""


@dataclass
class CertificateUse(Model):
    """A listener that serves a certificate."""

    listener_id: str = ""
    listener_name: str = ""
    load_balancer_id: str = ""


@dataclass
class Certificate(Model):
    """A TLS certificate imported into the project, for TERMINATED_HTTPS
    listeners.

    It carries what the certificate says about itself and never the private
    key, which cannot be read back. A certificate is immutable: to renew one,
    import the new certificate, point the listener at it, delete the old one.
    """

    id: str = ""
    name: str = ""
    #: In the form ``openssl x509 -fingerprint -sha256`` prints.
    fingerprint_sha256: str = ""
    subject: str = ""
    domains: list[str] = field(default_factory=list)
    issuer: str = ""
    not_before: str = ""
    not_after: str = ""
    #: RSA-2048 to RSA-4096, ECDSA-P256 or ECDSA-P384.
    key_algorithm: str = ""
    #: Intermediate certificates served with the leaf.
    chain_length: int = 0
    used_by: list[CertificateUse] = field(default_factory=list)
    created_at: str = ""


@dataclass
class SessionPersistence(Model):
    type: str = ""
    cookie_name: str = ""


@dataclass
class PoolMember(Model):
    id: str = ""
    name: str = ""
    pool_id: str = ""
    address: str = ""
    subnet_id: str = ""
    protocol_port: int = 0
    weight: int = 1
    admin_state_up: bool = True
    provisioning_status: str = ""
    operating_status: str = ""


@dataclass
class HealthMonitor(Model):
    """A pool's health probe.

    UDP pools report UDP_CONNECT: failures there are detected passively, without
    an active probe.
    """

    id: str = ""
    pool_id: str = ""
    type: str = ""
    delay: int = 0
    timeout: int = 0
    max_retries: int = 0
    http_method: str = ""
    url_path: str = ""
    expected_codes: str = ""
    admin_state_up: bool = True
    provisioning_status: str = ""


@dataclass
class Pool(Model):
    _aliases = {"load_balancer_id": "loadbalancer_id"}

    id: str = ""
    name: str = ""
    description: str = ""
    load_balancer_id: str = ""
    listener_id: str = ""
    protocol: str = ""
    lb_algorithm: str = ""
    session_persistence: SessionPersistence | None = None
    admin_state_up: bool = True
    provisioning_status: str = ""
    operating_status: str = ""
    members: list[PoolMember] = field(default_factory=list)
    health_monitors: list[HealthMonitor] = field(default_factory=list)


@dataclass
class LoadBalancerAppliance(Model):
    """One appliance of a high-availability pair.

    ``state`` is ACTIVE (holds the VIP), STANDBY, DOWN or UNKNOWN. ``address``
    is the appliance's own address: health checks and proxied connections leave
    from it, so members' security groups must admit both appliances.
    """

    index: int = 0
    state: str = ""
    address: str = ""


@dataclass
class LoadBalancerHA(Model):
    """Observed state of a high-availability load balancer's two appliances.

    ``status`` is REDUNDANT (one active, one standby, on two hosts), DEGRADED or
    UNKNOWN. ``reason`` says why a pair is DEGRADED: SPLIT_BRAIN,
    NO_ACTIVE_APPLIANCE, APPLIANCE_UNAVAILABLE or SINGLE_HOST.
    """

    status: str = ""
    reason: str = ""
    appliances: list[LoadBalancerAppliance] = field(default_factory=list)


@dataclass
class LoadBalancerFlavor(Model):
    """One load-balancer tier. ``vcpus`` and ``ram_mb`` size each appliance; a
    high-availability tier runs ``appliances`` of them, billed as one load
    balancer."""

    name: str = ""
    appliances: int = 0
    vcpus: int = 0
    ram_mb: int = 0
    high_availability: bool = False
    hourly_price: float = 0.0
    monthly_price: float = 0.0
    currency: str = ""


@dataclass
class LoadBalancer(Model):
    id: str = ""
    name: str = ""
    description: str = ""
    vip_address: str = ""
    vip_subnet_id: str = ""
    #: The engine, read-only. New balancers are always "auto", which serves
    #: every listener protocol; older ones may report "haproxy" (no UDP
    #: listeners) or "nginx" (UDP listeners only).
    provider: str = ""
    provisioning_status: str = ""
    operating_status: str = ""
    tenant_id: str = ""
    project_id: str = ""
    admin_state_up: bool = True
    listeners: list[Listener] = field(default_factory=list)
    pools: list[Pool] = field(default_factory=list)
    created_at: str | None = None
    #: The tier: lb-small, lb-ha, lb-medium or lb-large. Fixed at creation.
    flavor: str = ""
    #: Live state of the active/standby pair; None on a single-appliance tier.
    high_availability: LoadBalancerHA | None = None


# --------------------------------------------------------------------------- #
# DNS
# --------------------------------------------------------------------------- #


@dataclass
class DNSZoneVerification(Model):
    """The record that proves control of a pending zone's domain.

    ``name`` is fully qualified ("_nemau-challenge.example.com."), ``type`` is
    "TXT" and ``value`` is the exact text the record must hold.
    """

    name: str = ""
    type: str = ""
    value: str = ""


@dataclass
class DNSZone(Model):
    """An authoritative zone.

    ``id`` is the platform's identifier (a UUID) and is what every zone method
    takes; ``name`` is the canonical zone name ("example.com."), which is not
    an identifier.

    A new zone is not served until its owner proves control of the domain:
    ``status`` is ``PENDING_VERIFICATION`` and ``verification`` names the TXT
    record to publish at the current DNS host. Once
    :meth:`~nemau.services.dns.DNSService.verify_zone` has found it, ``status``
    becomes ``ACTIVE`` and ``verification`` is ``None``.
    """

    id: str = ""
    name: str = ""
    email: str = ""
    description: str = ""
    ttl: int = 0
    status: str = ""
    verification: DNSZoneVerification | None = None
    tenant_id: str = ""
    project_id: str = ""
    created_at: str = ""


@dataclass
class DNSRecordSet(Model):
    """A DNS record set.

    Its id is "canonical.name./TYPE". Because it contains a slash, it is sent
    as a query parameter, not as a path segment.
    """

    id: str = ""
    name: str = ""
    type: str = ""
    ttl: int = 0
    records: list[str] = field(default_factory=list)
    description: str = ""
    protected: bool = False
    status: str = ""
    zone_id: str = ""


# --------------------------------------------------------------------------- #
# Kubernetes
# --------------------------------------------------------------------------- #


@dataclass
class NodePoolSpec(Model):
    # "class" is reserved in Python but an ordinary key in JSON.
    _aliases = {
        "node_class": "class",
        "autoscaler_min": "autoscalerMin",
        "autoscaler_max": "autoscalerMax",
    }

    name: str = ""
    node_class: str = ""
    flavor_id: str = ""
    vcpus: int = 0
    ram_mb: int = 0
    disk_gb: int = 0
    replicas: int = 0
    autoscaler_min: int = 0
    autoscaler_max: int = 0
    labels: list[str] = field(default_factory=list)
    taints: list[str] = field(default_factory=list)


@dataclass
class KubernetesTopology(Model):
    _aliases = {
        "node_class": "class",
        "control_plane_flavor_id": "controlPlaneFlavorId",
        "control_plane_sizing": "controlPlaneSizing",
        "control_plane_replicas": "controlPlaneReplicas",
        "node_pools": "nodePools",
    }

    version: str = ""
    node_class: str = ""
    control_plane_flavor_id: str = ""
    control_plane_sizing: Sizing | None = None
    control_plane_replicas: int = 0
    node_pools: list[NodePoolSpec] = field(default_factory=list)


@dataclass
class KubernetesClusterSpec(Model):
    """The spec of a cluster: its topology, and the network it lives in."""

    topology: KubernetesTopology | None = None
    #: Public UUID of the project network the cluster lives in.
    network_id: str = ""
    #: Public UUID of the subnet the nodes are attached to.
    subnet_id: str = ""
    #: IPv4 CIDR of the pod network, as set at creation.
    pod_cidr: str | None = None
    #: IPv4 CIDR of the service network, as set at creation.
    service_cidr: str | None = None


@dataclass
class KubernetesAPIEndpoint(Model):
    host: str = ""
    port: int = 0


@dataclass
class KubernetesClusterCondition(Model):
    _aliases = {"last_transition_time": "lastTransitionTime"}

    type: str = ""
    status: str = ""
    reason: str = ""
    last_transition_time: str | None = None


@dataclass
class KubernetesClusterStatus(Model):
    _aliases = {
        "infrastructure_ready": "infrastructureReady",
        "control_plane_ready": "controlPlaneReady",
        "control_plane_endpoint": "controlPlaneEndpoint",
    }

    phase: str = ""
    infrastructure_ready: bool = False
    control_plane_ready: bool = False
    control_plane_endpoint: KubernetesAPIEndpoint | None = None
    conditions: list[KubernetesClusterCondition] = field(default_factory=list)


@dataclass
class KubernetesCluster(Model):
    _aliases = {"created_at": "createdAt"}

    id: str = ""
    name: str = ""
    created_at: str | None = None
    spec: KubernetesClusterSpec | None = None
    status: KubernetesClusterStatus | None = None


@dataclass
class KubernetesClusterSummarySpecs(Model):
    """Figures the cluster listing carries. All optional; unknown keys land in
    ``extra``. They are refreshed by a periodic reconcile, so a cluster created
    a moment ago may not carry them all yet."""

    phase: str = ""
    infrastructure_ready: bool = False
    control_plane_ready: bool = False
    kubernetes_version: str = ""
    cluster_class: str = ""
    node_pools_count: int = 0
    control_plane_replicas: int = 0
    control_plane_vcpus: int = 0
    control_plane_ram_mb: int = 0
    control_plane_disk_gb: int = 0
    worker_replicas: int = 0
    worker_vcpus: int = 0
    worker_ram_mb: int = 0
    worker_disk_gb: int = 0


@dataclass
class KubernetesClusterSummary(Model):
    """One row of the cluster listing.

    The platform's record of the cluster, not the live cluster object:
    ``status`` is the lowercase lifecycle status ("active", "building",
    "upgrading", "deleting", "error") and the capitalised phase is
    ``specs.phase``. Use ``get_cluster`` for the topology and the conditions.
    """

    _aliases = {
        "resource_type": "resourceType",
        "project_id": "projectId",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
    }

    id: str = ""
    name: str = ""
    status: str = ""
    resource_type: str = ""
    project_id: str | None = None
    specs: KubernetesClusterSummarySpecs | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class KubernetesPreflightCheck(Model):
    name: str = ""
    passed: bool = False
    details: str = ""


@dataclass
class KubernetesPreflightReport(Model):
    """Whether a cluster may be upgraded now."""

    _aliases = {
        "cluster_name": "clusterName",
        "current_version": "currentVersion",
        "target_version": "targetVersion",
        "blocking_issues": "blockingIssues",
    }

    cluster_name: str = ""
    current_version: str = ""
    target_version: str = ""
    ready: bool = False
    checks: list[KubernetesPreflightCheck] = field(default_factory=list)
    blocking_issues: list[str] = field(default_factory=list)


@dataclass
class KubernetesPreflight(Model):
    """The answer to a preflight request."""

    preflight_id: str = ""
    report: KubernetesPreflightReport | None = None


@dataclass
class MaintenanceWindow(Model):
    """A period during which a cluster may be upgraded."""

    _aliases = {
        "cluster_name": "clusterName",
        "starts_at": "startsAt",
        "ends_at": "endsAt",
        "created_by": "createdBy",
        "created_at": "createdAt",
    }

    id: str = ""
    cluster_name: str = ""
    name: str = ""
    starts_at: str | None = None
    ends_at: str | None = None
    reason: str = ""
    created_by: str = ""
    created_at: str | None = None


@dataclass
class ClusterClass(Model):
    name: str = ""
    namespace: str = ""


# --------------------------------------------------------------------------- #
# Quotas
# --------------------------------------------------------------------------- #


@dataclass
class QuotaDimension(Model):
    """One limit and what currently stands against it."""

    name: str = ""
    #: ``"count"``, ``"MB"``, ``"GB"`` or ``"bytes"``. RAM is counted in MB and
    #: volume size in GB, so check the unit before comparing dimensions.
    unit: str = ""
    used: int = 0
    #: Capacity claimed by tasks admitted but not yet settled. The API compares
    #: used + reserved + requested to the limit, so headroom computed without it
    #: is an overestimate.
    reserved: int = 0
    limit: int = 0

    @property
    def available(self) -> int:
        """Headroom left, never negative."""
        return max(0, self.limit - self.used - self.reserved)


@dataclass
class QuotaScope(Model):
    """One scope's allowance. A write needs room in every scope."""

    scope: str = ""
    project_id: str = ""
    dimensions: list[QuotaDimension] = field(default_factory=list)

    def dimension(self, name: str) -> QuotaDimension | None:
        """One dimension of this scope by name, or ``None``."""
        for dimension in self.dimensions:
            if dimension.name == name:
                return dimension
        return None


@dataclass
class QuotaList(Model):
    """The project a credential resolves to, and the organization above it."""

    quotas: list[QuotaScope] = field(default_factory=list)

    def scope(self, name: str) -> QuotaScope | None:
        """One scope by name, ``"project"`` or ``"tenant"``."""
        for scope in self.quotas:
            if scope.scope == name:
                return scope
        return None

    def binding(self, name: str) -> QuotaDimension | None:
        """The dimension that will refuse first: the least headroom.

        A write is accepted only when both the project and its organization
        have room, so the project's figures alone can show capacity that the
        organization's limit does not allow. The binding dimension does not
        always have the smallest limit.
        """
        binding: QuotaDimension | None = None
        for scope in self.quotas:
            dimension = scope.dimension(name)
            if dimension is None:
                continue
            if binding is None or dimension.available < binding.available:
                binding = dimension
        return binding


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #


@dataclass
class Project(Model):
    """A project a credential can act on."""

    id: str = ""
    name: str = ""
    slug: str = ""
    region: str = ""
    is_active: bool = False
    provisioning_status: str = ""
    #: True for the project the listing request was itself scoped to.
    current: bool = False

    @property
    def ready(self) -> bool:
        """Whether the project can receive resources.

        A project that is inactive or still provisioning is listed but
        refuses every write.
        """
        return self.is_active and self.provisioning_status == "active"


@dataclass
class ProjectList(Model):
    projects: list[Project] = field(default_factory=list)
    count: int = 0
    #: The project the request resolved to. It can name a project that is not
    #: in ``projects``, for example when the credential points at a deleted
    #: project.
    current_project_id: str = ""


# --------------------------------------------------------------------------- #
# Regions
# --------------------------------------------------------------------------- #


@dataclass
class Region(Model):
    _aliases = {"display_name": "displayName"}

    slug: str = ""
    display_name: str = ""
    country: str = ""
    enabled: bool = False


@dataclass
class RegionStatus(Model):
    _aliases = {"checked_at": "checkedAt"}

    slug: str = ""
    healthy: bool = False
    checked_at: str = ""
    error: str = ""


# Nested-field declarations, after every class is defined.
_nest(Certificate, "used_by", CertificateUse, many=True)
_nest(Instance, "flavor", Ref)
_nest(Instance, "image", Ref)
_nest(Instance, "sizing", Sizing)
_nest(Instance, "security_groups", Ref, many=True)
_nest(Subnet, "allocation_pools", AllocationPool, many=True)
_nest(NetworkInterface, "addresses", InterfaceAddress, many=True)
_nest(SecurityGroup, "rules", SecurityGroupRule, many=True)
_nest(Volume, "attachments", VolumeAttachment, many=True)
_nest(ObjectPage, "objects", StoredObject, many=True)
_nest(DNSZone, "verification", DNSZoneVerification)
_nest(Pool, "session_persistence", SessionPersistence)
_nest(Pool, "members", PoolMember, many=True)
_nest(Pool, "health_monitors", HealthMonitor, many=True)
_nest(LoadBalancer, "listeners", Listener, many=True)
_nest(LoadBalancer, "pools", Pool, many=True)
_nest(LoadBalancer, "high_availability", LoadBalancerHA)
_nest(LoadBalancerHA, "appliances", LoadBalancerAppliance, many=True)
_nest(KubernetesTopology, "control_plane_sizing", Sizing)
_nest(KubernetesTopology, "node_pools", NodePoolSpec, many=True)
_nest(KubernetesClusterSpec, "topology", KubernetesTopology)
_nest(KubernetesClusterStatus, "control_plane_endpoint", KubernetesAPIEndpoint)
_nest(KubernetesClusterStatus, "conditions", KubernetesClusterCondition, many=True)
_nest(KubernetesCluster, "spec", KubernetesClusterSpec)
_nest(KubernetesCluster, "status", KubernetesClusterStatus)
_nest(KubernetesClusterSummary, "specs", KubernetesClusterSummarySpecs)
_nest(KubernetesPreflightReport, "checks", KubernetesPreflightCheck, many=True)
_nest(KubernetesPreflight, "report", KubernetesPreflightReport)
_nest(CreatedKeypair, "task", Task)
_nest(ProjectList, "projects", Project, many=True)
_nest(QuotaList, "quotas", QuotaScope, many=True)
_nest(QuotaScope, "dimensions", QuotaDimension, many=True)
_nest(Container, "protection", ContainerProtection)
_nest(CopiedObject, "retention", ObjectRetention)


# --------------------------------------------------------------------------- #
# IAM
# --------------------------------------------------------------------------- #


@dataclass
class IamUser(Model):
    """A user of the organization."""

    _aliases = {
        "organization_id": "organizationId",
        "created_at": "createdAt",
        "totp_enabled": "totpEnabled",
    }

    id: str = ""
    username: str = ""
    email: str = ""
    name: str = ""
    surname: str = ""
    #: "owner" or "member".
    type: str = ""
    status: str = ""
    organization_id: str = ""
    totp_enabled: bool = False
    created_at: str = ""


@dataclass
class IamUserPage(Model):
    """One page of the organization's users."""

    data: list[IamUser] = field(default_factory=list)
    total: int = 0
    page: int = 0
    limit: int = 0
    total_pages: int = 0
    has_more: bool = False


_nest(IamUserPage, "data", IamUser, many=True)


@dataclass
class IamAccessKey(Model):
    """An access key of an IAM user. It never carries the secret."""

    _aliases = {
        "access_key_id": "accessKeyId",
        "user_id": "userId",
        "expires_at": "expiresAt",
        "last_used_at": "lastUsedAt",
        "created_at": "createdAt",
    }

    id: str = ""
    access_key_id: str = ""
    description: str = ""
    user_id: str = ""
    #: "active" or "inactive".
    status: str = ""
    #: None on older keys that do not expire.
    expires_at: str | None = None
    last_used_at: str | None = None
    created_at: str = ""


@dataclass
class AccessKeyLimits(Model):
    """How many active access keys a user may hold, and holds."""

    _aliases = {"max_access_keys": "maxAccessKeys"}

    max_access_keys: int = 0
    current: int = 0


# --------------------------------------------------------------------------- #
# Managed PostgreSQL
# --------------------------------------------------------------------------- #


class DatabaseStatus:
    """The lifecycle of a database.

    ``building`` is being provisioned, ``active`` is serving, ``error`` failed
    and needs an operator, ``deleted`` is retired and its backups may still sit
    inside their restore window.
    """

    BUILDING = "building"
    ACTIVE = "active"
    ERROR = "error"
    DELETED = "deleted"


@dataclass
class DatabaseMaintenanceWindow(Model):
    """One UTC hour a week. ``weekday`` 0 is Sunday."""

    weekday: int = 0
    hour: int = 0


@dataclass
class DatabaseFlavor(Model):
    """A managed database size the region offers. Pass its ``name`` as ``flavor``."""

    name: str = ""
    vcpus: int = 0
    memory_mb: int = 0


@dataclass
class DatabasePendingResize(Model):
    """A resize the database holds until it applies.

    ``apply_at`` is the RFC 3339 instant the deferred resize applies, at the next
    maintenance window.
    """

    flavor: str = ""
    storage_gb: int = 0
    apply_at: str = ""


class OneTimePassword:
    """A password the API returned once, held outside the dataclass fields.

    It is not a field of any model, so ``dataclasses.asdict``, ``fields`` and
    ``Model.extra`` never carry it, and its ``repr`` and ``str`` are redacted.
    Read it with :attr:`value` and store it at once.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    @property
    def value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "OneTimePassword(<redacted>)"

    __str__ = __repr__


@dataclass
class Database(Model):
    """A managed PostgreSQL database.

    The API names no internal identifier, no management address and no
    hypervisor reference: ``address`` and ``port`` are the only way in, over TLS.
    ``status`` is one of the values of :class:`DatabaseStatus`.
    """

    id: str = ""
    name: str = ""
    description: str = ""
    status: str = ""
    version: str = ""
    flavor: str = ""
    storage_gb: int = 0
    subnet_id: str = ""
    security_group_ids: list[str] = field(default_factory=list)
    admin_username: str = ""
    address: str = ""
    port: int = 0
    backup_retention_days: int = 0
    maintenance_window: DatabaseMaintenanceWindow | None = None
    #: RFC 3339. The oldest instant a point-in-time restore can reach.
    earliest_restorable_time: str | None = None
    #: RFC 3339. The last archived WAL; None until the agent has reported it.
    latest_restorable_time: str | None = None
    #: RFC 3339. Closes the restore window of a deleted database; None for a live one.
    restorable_until: str | None = None
    #: The deferred resize, or None when none is held.
    pending_resize: DatabasePendingResize | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class DatabaseCredentials(Model):
    """The 202 of a write that issues a password.

    ``task_id``, ``database_id`` and ``username`` are fields. The password is
    not: it is held in a :class:`OneTimePassword` beside them, so ``repr``,
    ``asdict`` and the logs never carry it. Read it with :attr:`password`, from
    the object the write returned, and store it at once: the API does not return
    it again.
    """

    task_id: str = ""
    #: Set on a rotation; empty on a create or a restore, whose task names it.
    database_id: str = ""
    username: str = ""

    def __post_init__(self) -> None:
        self._secret: OneTimePassword | None = None

    @property
    def password(self) -> str:
        """The administrator password, or an empty string when none was issued here."""
        return self._secret.value if self._secret is not None else ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DatabaseCredentials:
        """Builds the credentials of a 202 that issues a password.

        The password is taken out of the payload before the fields are read, so
        it never reaches ``extra`` either: a decoded model never holds it as data.
        """
        raw = dict(payload)
        password = raw.pop("password", "")
        credentials = super().from_dict(raw)
        if isinstance(password, str) and password:
            credentials._secret = OneTimePassword(password)
        return credentials


# --------------------------------------------------------------------------- #
# Instance groups
# --------------------------------------------------------------------------- #


class InstanceGroupRefreshStatus:
    """The state of a rolling refresh.

    ``idle`` is a group that never refreshed; ``in_progress`` replaces members;
    ``blocked`` waits for room in the group or for a member allowed out, and
    resumes by itself; ``hold`` is paused by a billing hold; ``completed``,
    ``cancelled`` and ``failed`` are final.
    """

    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    HOLD = "hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class InstanceGroupRefresh(Model):
    """The rolling refresh of a group: its replacement of members onto the current template."""

    status: str = InstanceGroupRefreshStatus.IDLE
    #: The template version the refresh rolls to; 0 before any refresh.
    target_version: int = 0
    #: RFC 3339; None before the refresh starts.
    started_at: str | None = None
    #: RFC 3339; None while the refresh is not finished.
    completed_at: str | None = None
    #: Protected members the refresh leaves on an older template version.
    skipped_protected: int = 0


@dataclass
class InstanceGroupLaunchTemplate(Model):
    """The launch template of a group, as read back.

    ``user_data_present`` is a flag: the document is never returned.
    ``user_data_sha256`` is its fingerprint, None when the template has none.
    """

    #: The template version the group runs now.
    version: int = 0
    flavor_id: str = ""
    image_id: str = ""
    vcpus: int = 0
    ram_mb: int = 0
    disk_gb: int = 0
    network_id: str = ""
    subnet_id: str = ""
    security_group_ids: list[str] = field(default_factory=list)
    key_name: str = ""
    user_data_present: bool = False
    user_data_sha256: str | None = None


@dataclass
class InstanceGroupPoolTarget(Model):
    """A load balancer pool a group registers its members in."""

    pool_id: str = ""
    protocol_port: int = 0


@dataclass
class InstanceGroupScalingPolicy(Model):
    """A target-tracking policy, with the server's defaults filled in."""

    metric: str = ""
    target: int = 0
    window_seconds: int = 0
    scale_out_cooldown_seconds: int = 0
    scale_in_cooldown_seconds: int = 0
    scale_in_window_seconds: int = 0
    max_step_out: int = 0
    max_step_in: int = 0
    scale_in_enabled: bool = True


@dataclass
class InstanceGroupSchedule(Model):
    """A scheduled action: a UTC cron and the bounds it sets when it fires."""

    id: str = ""
    cron: str = ""
    min_size: int | None = None
    max_size: int | None = None
    desired_capacity: int | None = None
    #: RFC 3339. The last slot the schedule fired for; None before its first firing.
    last_slot: str | None = None


@dataclass
class InstanceGroup(Model):
    """An instance group as the list returns it: no members, no activities."""

    id: str = ""
    name: str = ""
    description: str = ""
    status: str = ""
    health_check: str = ""
    health_check_grace_seconds: int = 0
    drain_seconds: int = 0
    min_size: int = 0
    max_size: int = 0
    desired_capacity: int = 0
    instance_count: int = 0
    max_surge: int = 0
    max_unavailable_pct: int = 0
    template_version: int = 0
    launch_template: InstanceGroupLaunchTemplate | None = None
    pools: list[InstanceGroupPoolTarget] = field(default_factory=list)
    scaling_policies: list[InstanceGroupScalingPolicy] = field(default_factory=list)
    schedules: list[InstanceGroupSchedule] = field(default_factory=list)
    refresh: InstanceGroupRefresh | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class InstanceGroupMember(Model):
    """A member of a group, as ``get`` returns it."""

    instance_id: str = ""
    status: str = ""
    protected_from_scale_in: bool = False
    template_version: int = 0
    joined_at: str = ""


@dataclass
class InstanceGroupActivity(Model):
    """One decision of the group's controller: a scale step, a refusal, a skipped slot.

    ``instance_id`` is None when the decision concerns no single member.
    """

    id: int = 0
    type: str = ""
    reason: str = ""
    instance_id: str | None = None
    created_at: str = ""


@dataclass
class InstanceGroupDetail(InstanceGroup):
    """An instance group with its members and its recent activities, as ``get`` returns it."""

    members: list[InstanceGroupMember] = field(default_factory=list)
    activities: list[InstanceGroupActivity] = field(default_factory=list)


_nest(Database, "maintenance_window", DatabaseMaintenanceWindow)
_nest(Database, "pending_resize", DatabasePendingResize)
_nest(InstanceGroup, "pools", InstanceGroupPoolTarget, many=True)
_nest(InstanceGroup, "launch_template", InstanceGroupLaunchTemplate)
_nest(InstanceGroup, "scaling_policies", InstanceGroupScalingPolicy, many=True)
_nest(InstanceGroup, "schedules", InstanceGroupSchedule, many=True)
_nest(InstanceGroup, "refresh", InstanceGroupRefresh)
_nest(InstanceGroupDetail, "pools", InstanceGroupPoolTarget, many=True)
_nest(InstanceGroupDetail, "launch_template", InstanceGroupLaunchTemplate)
_nest(InstanceGroupDetail, "scaling_policies", InstanceGroupScalingPolicy, many=True)
_nest(InstanceGroupDetail, "schedules", InstanceGroupSchedule, many=True)
_nest(InstanceGroupDetail, "refresh", InstanceGroupRefresh)
_nest(InstanceGroupDetail, "members", InstanceGroupMember, many=True)
_nest(InstanceGroupDetail, "activities", InstanceGroupActivity, many=True)
