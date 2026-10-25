"""The load-balancer half of /network: balancers, listeners, pools, monitors."""

from __future__ import annotations

import builtins
import urllib.parse
from collections.abc import Mapping
from typing import Any

from nemau.client import _unwrap
from nemau.errors import not_found
from nemau.models import (
    Certificate,
    HealthMonitor,
    Listener,
    LoadBalancer,
    LoadBalancerFlavor,
    Pool,
    PoolMember,
    TaskRef,
)
from nemau.services import Service
from nemau.services.update import patch

_CERTIFICATES = "/network/load-balancer-certificates"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _admin_state(body: dict[str, Any], admin_state_up: bool | None) -> None:
    """``admin_state_up`` is left out of the body unless it is set, and the API
    then defaults it to true."""
    if admin_state_up is not None:
        body["admin_state_up"] = admin_state_up


class LoadBalancerService(Service):
    """Load balancing."""

    # -- load balancers ------------------------------------------------ #

    def list(self) -> builtins.list[LoadBalancer]:
        payload = self._client.request("GET", "/network/load-balancers")
        return [LoadBalancer.from_dict(item) for item in _unwrap(payload, "load_balancers")]

    def list_flavors(self) -> builtins.list[LoadBalancerFlavor]:
        """The load-balancer tiers, with their sizing and price."""
        payload = self._client.request("GET", "/network/load-balancer-flavors")
        return [LoadBalancerFlavor.from_dict(item) for item in _unwrap(payload, "flavors")]

    def get(self, load_balancer_id: str) -> LoadBalancer:
        payload = self._client.request("GET", f"/network/load-balancers/{_quote(load_balancer_id)}")
        return LoadBalancer.from_dict((payload or {}).get("load_balancer") or {})

    def create(
        self,
        *,
        name: str,
        vip_subnet_id: str,
        description: str = "",
        flavor: str = "",
    ) -> TaskRef:
        """``vip_subnet_id`` is required: the VIP is allocated in that subnet.

        ``flavor`` picks the tier; empty means lb-small. lb-ha, lb-medium and
        lb-large run two appliances on two hosts behind the same VIP. The tier
        is fixed at creation.

        There is no engine option: a new load balancer runs in automatic mode,
        which serves HTTP, HTTPS, TCP and UDP listeners together.
        """
        body: dict[str, Any] = {"name": name, "vip_subnet_id": vip_subnet_id}
        if description:
            body["description"] = description
        if flavor:
            body["flavor"] = flavor
        return self._client.submit("POST", "/network/load-balancers", json=body)

    def create_and_wait(self, **kwargs: Any) -> LoadBalancer:
        ref = self.create(**kwargs)
        load_balancer_id, _ = self._client.wait_for_resource_id(ref, "load_balancer_id")
        return self.get(load_balancer_id)

    def delete(self, load_balancer_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/network/load-balancers/{_quote(load_balancer_id)}")

    # -- listeners ------------------------------------------------------ #

    def list_listeners(self, load_balancer_id: str) -> builtins.list[Listener]:
        """``load_balancer_id`` is required: there is no project-wide list."""
        payload = self._client.request(
            "GET", "/network/listeners", query={"lb_id": load_balancer_id}
        )
        return [Listener.from_dict(item) for item in _unwrap(payload, "listeners")]

    def get_listener(self, load_balancer_id: str, listener_id: str) -> Listener:
        for item in self.list_listeners(load_balancer_id):
            if item.id == listener_id:
                return item
        raise not_found("listener", listener_id, "/network/listeners")

    def create_listener(
        self,
        load_balancer_id: str,
        *,
        name: str,
        protocol: str,
        protocol_port: int,
        description: str = "",
        default_pool_id: str = "",
        connection_limit: int = 0,
        tls_certificate_ref: str = "",
        admin_state_up: bool | None = None,
    ) -> TaskRef:
        """``tls_certificate_ref`` is required for TERMINATED_HTTPS and refused
        otherwise: the ID of a certificate imported with
        :meth:`import_certificate`."""
        body: dict[str, Any] = {
            "name": name,
            "protocol": protocol,
            "protocol_port": protocol_port,
        }
        if description:
            body["description"] = description
        if default_pool_id:
            body["default_pool_id"] = default_pool_id
        if connection_limit:
            body["connection_limit"] = connection_limit
        if tls_certificate_ref:
            body["tls_certificate_ref"] = tls_certificate_ref
        _admin_state(body, admin_state_up)
        return self._client.submit(
            "POST", f"/network/load-balancers/{_quote(load_balancer_id)}/listeners", json=body
        )

    def delete_listener(self, listener_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/network/listeners/{_quote(listener_id)}")

    # -- TLS certificates ----------------------------------------------- #

    def import_certificate(
        self,
        *,
        name: str,
        certificate: str,
        private_key: str,
        chain: str = "",
    ) -> Certificate:
        """Validates and stores a certificate. Synchronous: a listener can use
        it as soon as this returns.

        PEM-encoded. ``certificate`` holds the leaf, optionally followed by its
        chain (a fullchain.pem); ``chain`` adds intermediates. The key must not
        be passphrase-protected: RSA 2048 to 4096 bits, ECDSA P-256 or P-384.
        The key is not part of the response and cannot be read back.
        """
        body: dict[str, Any] = {
            "name": name,
            "certificate": certificate,
            "private_key": private_key,
        }
        if chain:
            body["chain"] = chain
        payload = self._client.request("POST", _CERTIFICATES, json=body)
        return Certificate.from_dict(payload or {})

    def list_certificates(self) -> builtins.list[Certificate]:
        """The project's certificates and the listeners that serve them."""
        payload = self._client.request("GET", _CERTIFICATES)
        return [Certificate.from_dict(item) for item in _unwrap(payload, "certificates")]

    def get_certificate(self, certificate_id: str) -> Certificate:
        payload = self._client.request("GET", f"{_CERTIFICATES}/{_quote(certificate_id)}")
        return Certificate.from_dict(payload or {})

    def delete_certificate(self, certificate_id: str) -> None:
        """Removes a certificate no listener serves any more. While one does,
        the API answers 409; see :func:`nemau.is_certificate_in_use`."""
        self._client.request("DELETE", f"{_CERTIFICATES}/{_quote(certificate_id)}")

    # -- pools ---------------------------------------------------------- #

    def list_pools(self, load_balancer_id: str) -> builtins.list[Pool]:
        payload = self._client.request("GET", "/network/pools", query={"lb_id": load_balancer_id})
        return [Pool.from_dict(item) for item in _unwrap(payload, "pools")]

    def get_pool(self, load_balancer_id: str, pool_id: str) -> Pool:
        for item in self.list_pools(load_balancer_id):
            if item.id == pool_id:
                return item
        raise not_found("pool", pool_id, "/network/pools")

    def create_pool(
        self,
        load_balancer_id: str,
        *,
        name: str,
        protocol: str,
        lb_algorithm: str,
        description: str = "",
        listener_id: str = "",
        session_persistence: Mapping[str, str] | None = None,
        admin_state_up: bool | None = None,
    ) -> TaskRef:
        body: dict[str, Any] = {
            "name": name,
            "protocol": protocol,
            "lb_algorithm": lb_algorithm,
        }
        if description:
            body["description"] = description
        if listener_id:
            body["listener_id"] = listener_id
        if session_persistence:
            body["session_persistence"] = dict(session_persistence)
        _admin_state(body, admin_state_up)
        return self._client.submit(
            "POST", f"/network/load-balancers/{_quote(load_balancer_id)}/pools", json=body
        )

    def delete_pool(self, pool_id: str) -> TaskRef:
        return self._client.submit("DELETE", f"/network/pools/{_quote(pool_id)}")

    # -- members -------------------------------------------------------- #

    def list_pool_members(self, pool_id: str) -> builtins.list[PoolMember]:
        payload = self._client.request("GET", f"/network/pools/{_quote(pool_id)}/members")
        return [PoolMember.from_dict(item) for item in _unwrap(payload, "members")]

    def get_pool_member(self, pool_id: str, member_id: str) -> PoolMember:
        for item in self.list_pool_members(pool_id):
            if item.id == member_id:
                return item
        raise not_found("pool member", member_id, f"/network/pools/{pool_id}/members")

    def add_pool_member(
        self,
        pool_id: str,
        *,
        name: str,
        address: str,
        protocol_port: int,
        weight: int = 0,
        subnet_id: str = "",
        admin_state_up: bool | None = None,
    ) -> TaskRef:
        """``weight`` defaults to 1 server-side when left at zero."""
        body: dict[str, Any] = {
            "name": name,
            "address": address,
            "protocol_port": protocol_port,
        }
        if weight:
            body["weight"] = weight
        if subnet_id:
            body["subnet_id"] = subnet_id
        _admin_state(body, admin_state_up)
        return self._client.submit("POST", f"/network/pools/{_quote(pool_id)}/members", json=body)

    def remove_pool_member(self, pool_id: str, member_id: str) -> TaskRef:
        return self._client.submit(
            "DELETE", f"/network/pools/{_quote(pool_id)}/members/{_quote(member_id)}"
        )

    # -- health monitors ------------------------------------------------ #

    def list_pool_health_monitors(self, pool_id: str) -> builtins.list[HealthMonitor]:
        payload = self._client.request("GET", f"/network/pools/{_quote(pool_id)}/health-monitors")
        return [HealthMonitor.from_dict(item) for item in _unwrap(payload, "health_monitors")]

    def get_health_monitor(self, pool_id: str, monitor_id: str) -> HealthMonitor:
        for item in self.list_pool_health_monitors(pool_id):
            if item.id == monitor_id:
                return item
        raise not_found("health monitor", monitor_id, f"/network/pools/{pool_id}/health-monitors")

    def create_health_monitor(
        self,
        pool_id: str,
        *,
        monitor_type: str,
        delay: int,
        timeout: int,
        max_retries: int,
        http_method: str = "",
        url_path: str = "",
        expected_codes: str = "",
        admin_state_up: bool | None = None,
    ) -> TaskRef:
        """``monitor_type`` is HTTP, HTTPS, TCP or UDP_CONNECT."""
        body: dict[str, Any] = {
            "type": monitor_type,
            "delay": delay,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if http_method:
            body["http_method"] = http_method
        if url_path:
            body["url_path"] = url_path
        if expected_codes:
            body["expected_codes"] = expected_codes
        _admin_state(body, admin_state_up)
        return self._client.submit(
            "POST", f"/network/pools/{_quote(pool_id)}/health-monitors", json=body
        )

    def delete_health_monitor(self, pool_id: str, monitor_id: str) -> TaskRef:
        return self._client.submit(
            "DELETE", f"/network/pools/{_quote(pool_id)}/health-monitors/{_quote(monitor_id)}"
        )

    def update_load_balancer(
        self, lb_id: str, *, name: str | None = None, description: str | None = None
    ) -> TaskRef:
        """Changes a load balancer's name or description."""
        return patch(
            self._client,
            "update_load_balancer",
            f"/network/load-balancers/{_quote(lb_id)}",
            {"name": name, "description": description},
        )

    def update_listener(
        self,
        listener_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        connection_limit: int | None = None,
        tls_certificate_ref: str | None = None,
    ) -> TaskRef:
        """Changes a listener's name, description, connection limit or, on a
        TERMINATED_HTTPS listener, its certificate. Changing the certificate
        this way renews it without downtime."""
        return patch(
            self._client,
            "update_listener",
            f"/network/listeners/{_quote(listener_id)}",
            {
                "name": name,
                "description": description,
                "connection_limit": connection_limit,
                "tls_certificate_ref": tls_certificate_ref,
            },
        )

    def update_pool(
        self,
        pool_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        lb_algorithm: str | None = None,
    ) -> TaskRef:
        """Changes a pool's name, description or balancing algorithm.

        The appliance config is rewritten in place; established connections are
        not dropped.
        """
        return patch(
            self._client,
            "update_pool",
            f"/network/pools/{_quote(pool_id)}",
            {"name": name, "description": description, "lb_algorithm": lb_algorithm},
        )

    def update_pool_member(
        self, pool_id: str, member_id: str, *, name: str | None = None, weight: int | None = None
    ) -> TaskRef:
        """Changes a member's name or weight."""
        return patch(
            self._client,
            "update_pool_member",
            f"/network/pools/{_quote(pool_id)}/members/{_quote(member_id)}",
            {"name": name, "weight": weight},
        )

    def update_health_monitor(
        self,
        pool_id: str,
        monitor_id: str,
        *,
        name: str | None = None,
        delay: int | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        expected_codes: str | None = None,
    ) -> TaskRef:
        """Changes a monitor's probe timings or expected codes."""
        return patch(
            self._client,
            "update_health_monitor",
            f"/network/pools/{_quote(pool_id)}/health-monitors/{_quote(monitor_id)}",
            {
                "name": name,
                "delay": delay,
                "timeout": timeout,
                "max_retries": max_retries,
                "expected_codes": expected_codes,
            },
        )
