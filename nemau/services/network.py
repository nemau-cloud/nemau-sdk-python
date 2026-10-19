"""/network: networks, subnets, ports, floating IPs, security groups."""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from typing import Any

from nemau.client import _unwrap
from nemau.errors import not_found
from nemau.models import (
    FloatingIP,
    Network,
    NetworkCapabilities,
    NetworkInterface,
    SecurityGroup,
    Subnet,
    TaskRef,
)
from nemau.services import Service
from nemau.services.update import patch

#: The targets a floating IP can be associated with.
TARGET_INSTANCE = "instance"
TARGET_LOAD_BALANCER = "load_balancer"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class NetworkService(Service):
    """Virtual networks and filtering."""

    # -- networks ------------------------------------------------------ #

    def list_networks(self) -> list[Network]:
        payload = self._client.request("GET", "/network/networks")
        return [Network.from_dict(item) for item in _unwrap(payload, "networks")]

    def get_network(self, network_id: str) -> Network:
        """The API has no per-network read: list and filter.

        An unknown id raises a 404 :class:`~nemau.errors.NemauApiError`, as a
        direct read would.
        """
        for item in self.list_networks():
            if item.id == network_id:
                return item
        raise not_found("network", network_id, "/network/networks")

    def create_network(self, name: str, description: str = "") -> TaskRef:
        return self._client.submit(
            "POST", "/network/networks", json={"name": name, "description": description}
        )

    def create_network_and_wait(self, name: str, description: str = "") -> Network:
        ref = self.create_network(name, description)
        network_id, _ = self._client.wait_for_resource_id(ref, "network_id")
        return self.get_network(network_id)

    def delete_network(self, network_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/network/networks/{_quote(network_id)}")

    # -- subnets -------------------------------------------------------- #

    def list_subnets(self) -> list[Subnet]:
        payload = self._client.request("GET", "/network/subnets")
        return [Subnet.from_dict(item) for item in _unwrap(payload, "subnets")]

    def get_subnet(self, subnet_id: str) -> Subnet:
        for item in self.list_subnets():
            if item.id == subnet_id:
                return item
        raise not_found("subnet", subnet_id, "/network/subnets")

    def create_subnet(
        self,
        network_id: str,
        *,
        name: str,
        cidr: str | None = None,
        ip_version: int | None = None,
        gateway_ip: str | None = None,
        dns_servers: Sequence[str] | None = None,
        allocation_pools: Sequence[dict[str, str]] | None = None,
    ) -> TaskRef:
        """``gateway_ip`` is an ``Optional``: an explicit empty string (a subnet
        with no gateway) is not the same as "let the platform choose".

        ``ip_version`` defaults to 4, for which ``cidr`` is required. For 6,
        leave ``cidr``, ``gateway_ip`` and ``allocation_pools`` out: the
        platform assigns a /64 from the region's routed prefix and uses its
        first address as the gateway. The network must already have an IPv4
        subnet, and carries at most one IPv6 subnet.

        An IPv4 CIDR must not overlap any other subnet of the project, on this
        network or another: the API refuses it with a 409 that
        :func:`~nemau.errors.is_subnet_overlap` recognises. An IPv6 subnet is
        not checked for overlap: its /64 is assigned, not chosen."""
        body: dict[str, Any] = {"name": name}
        if cidr:
            body["cidr"] = cidr
        if ip_version:
            body["ip_version"] = ip_version
        if gateway_ip is not None:
            body["gateway_ip"] = gateway_ip
        if dns_servers:
            body["dns_servers"] = list(dns_servers)
        if allocation_pools:
            body["allocation_pools"] = [dict(pool) for pool in allocation_pools]
        return self._client.submit(
            "POST", f"/network/networks/{_quote(network_id)}/subnets", json=body
        )

    def delete_subnet(self, subnet_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/network/subnets/{_quote(subnet_id)}")

    # -- interfaces ---------------------------------------------------- #

    def list_network_interfaces(self) -> list[NetworkInterface]:
        payload = self._client.request("GET", "/network/interfaces")
        return [NetworkInterface.from_dict(item) for item in _unwrap(payload, "interfaces")]

    # -- floating IPs -------------------------------------------------- #

    def list_floating_ips(self) -> list[FloatingIP]:
        """The platform spells the key ``floatingips``, with no separator."""
        payload = self._client.request("GET", "/network/floating-ips")
        return [FloatingIP.from_dict(item) for item in _unwrap(payload, "floatingips")]

    def get_floating_ip(self, floating_ip_id: str) -> FloatingIP:
        for item in self.list_floating_ips():
            if item.id == floating_ip_id:
                return item
        raise not_found("floating ip", floating_ip_id, "/network/floating-ips")

    def allocate_floating_ip(
        self,
        external_network_id: str = "",
        description: str = "",
        *,
        ip_version: int | None = None,
    ) -> TaskRef:
        """An empty ``external_network_id`` uses the region's external network,
        and no ``ip_version`` means 4. An IPv6 allocation is refused with a 400 in a
        region that does not offer it; see :meth:`get_capabilities`."""
        body: dict[str, Any] = {}
        if external_network_id:
            body["floating_network_id"] = external_network_id
        if description:
            body["description"] = description
        if ip_version:
            body["ip_version"] = ip_version
        return self._client.submit("POST", "/network/floating-ips", json=body)

    def get_capabilities(self) -> NetworkCapabilities:
        """Which optional network features (IPv6 floating IPs, IPv6 subnets)
        the region offers."""
        payload = self._client.request("GET", "/network/capabilities")
        raw = payload.get("capabilities") if isinstance(payload, dict) else None
        return NetworkCapabilities.from_dict(raw if isinstance(raw, dict) else {})

    def release_floating_ip(self, floating_ip_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/network/floating-ips/{_quote(floating_ip_id)}")

    def associate_floating_ip(
        self, floating_ip_id: str, target_type: str, target_id: str
    ) -> TaskRef:
        """``target_type`` is :data:`TARGET_INSTANCE` or :data:`TARGET_LOAD_BALANCER`."""
        return self._client.submit(
            "PUT",
            f"/network/floating-ips/{_quote(floating_ip_id)}/associate",
            json={"target_type": target_type, "target_id": target_id},
        )

    def disassociate_floating_ip(self, floating_ip_id: str) -> TaskRef:
        """Detaches the address from its target, keeping the allocation."""
        return self._client.submit(
            "PUT", f"/network/floating-ips/{_quote(floating_ip_id)}/disassociate"
        )

    # -- security groups ------------------------------------------------ #

    def list_security_groups(self) -> list[SecurityGroup]:
        payload = self._client.request("GET", "/network/security-groups")
        return [SecurityGroup.from_dict(item) for item in _unwrap(payload, "security_groups")]

    def get_security_group(self, group_id: str) -> SecurityGroup:
        for item in self.list_security_groups():
            if item.id == group_id:
                return item
        raise not_found("security group", group_id, "/network/security-groups")

    def create_security_group(self, name: str, description: str = "") -> TaskRef:
        return self._client.submit(
            "POST", "/network/security-groups", json={"name": name, "description": description}
        )

    def delete_security_group(self, group_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/network/security-groups/{_quote(group_id)}")

    def enable_security_group_egress_filtering(self, group_id: str) -> TaskRef:
        """Converts a group created before egress filtering existed.

        The group receives two egress rules that allow all outbound traffic
        (``0.0.0.0/0`` and ``::/0``), then every instance carrying it has its
        outbound policy recomputed: an instance whose groups all filter egress
        only sends what one of their egress rules allows.

        Traffic is unchanged when the task completes, since the added rules
        allow everything; removing or narrowing them afterwards takes effect.
        The conversion cannot be undone, and running it on a group that already
        filters egress succeeds without adding anything.
        """
        return self._client.submit(
            "POST", f"/network/security-groups/{_quote(group_id)}/enable-egress-filtering"
        )

    def create_security_group_rule(
        self,
        group_id: str,
        *,
        direction: str,
        protocol: str = "",
        port_range_min: int = 0,
        port_range_max: int = 0,
        remote_ip_prefix: str = "",
    ) -> TaskRef:
        """``direction`` is ``ingress`` or ``egress``.

        Leave the ports at zero for a protocol that has none: the API forwards
        them only when one of the two is non-zero.
        """
        body: dict[str, Any] = {"direction": direction}
        if protocol:
            body["protocol"] = protocol
        if port_range_min or port_range_max:
            body["port_range_min"] = port_range_min
            body["port_range_max"] = port_range_max
        if remote_ip_prefix:
            body["remote_ip_prefix"] = remote_ip_prefix
        return self._client.submit(
            "POST", f"/network/security-groups/{_quote(group_id)}/rules", json=body
        )

    def delete_security_group_rule(self, group_id: str, rule_id: str) -> TaskRef:
        return self._client.submit(
            "DELETE", f"/network/security-groups/{_quote(group_id)}/rules/{_quote(rule_id)}"
        )

    def update_network(
        self, network_id: str, *, name: str | None = None, description: str | None = None
    ) -> TaskRef:
        """Changes a network's name or description.

        The network is not rebuilt: its identifier and its segment stay as they
        are, so every instance already attached is untouched.
        """
        return patch(
            self._client,
            "update_network",
            f"/network/networks/{_quote(network_id)}",
            {"name": name, "description": description},
        )

    def update_subnet(self, subnet_id: str, *, name: str | None = None) -> TaskRef:
        """Changes a subnet's name."""
        return patch(
            self._client,
            "update_subnet",
            f"/network/subnets/{_quote(subnet_id)}",
            {"name": name},
        )

    def update_security_group(
        self, group_id: str, *, name: str | None = None, description: str | None = None
    ) -> TaskRef:
        """Changes a security group's name or description.

        Its rules stay immutable: a rule is created and deleted, never edited.
        """
        return patch(
            self._client,
            "update_security_group",
            f"/network/security-groups/{_quote(group_id)}",
            {"name": name, "description": description},
        )

    def update_floating_ip(self, floating_ip_id: str, *, description: str | None = None) -> TaskRef:
        """Changes a floating IP's description. The address is the allocation."""
        return patch(
            self._client,
            "update_floating_ip",
            f"/network/floating-ips/{_quote(floating_ip_id)}",
            {"description": description},
        )
