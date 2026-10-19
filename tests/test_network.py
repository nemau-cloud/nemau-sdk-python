"""Network: IPv6 bodies, decoding and region capabilities; security group egress."""

from __future__ import annotations

import unittest

from nemau import IPV4, IPV6
from tests.helpers import Stub, client_for, routes

ACCEPTED = (202, {"task_id": "t", "status": "pending"})


class FloatingIPAllocationTest(unittest.TestCase):
    def test_positional_allocation_sends_no_ip_version(self) -> None:
        # Without ip_version the field is left out, so a region without IPv6
        # receives the body it expects.
        with Stub(routes({"POST /api/v1/network/floating-ips": ACCEPTED})) as stub:
            client_for(stub).network.allocate_floating_ip("", "front")
            self.assertEqual(stub.requests[0].json(), {"description": "front"})

    def test_ip_version_is_sent_when_given(self) -> None:
        with Stub(routes({"POST /api/v1/network/floating-ips": ACCEPTED})) as stub:
            client_for(stub).network.allocate_floating_ip(ip_version=IPV6)
            self.assertEqual(stub.requests[0].json(), {"ip_version": 6})


class SubnetCreationTest(unittest.TestCase):
    def test_ipv6_subnet_omits_the_cidr(self) -> None:
        # The platform assigns the /64: the body names the version, not a range.
        with Stub(routes({"POST /api/v1/network/networks/net-1/subnets": ACCEPTED})) as stub:
            client_for(stub).network.create_subnet("net-1", name="v6", ip_version=IPV6)
            self.assertEqual(stub.requests[0].json(), {"name": "v6", "ip_version": 6})

    def test_ipv4_subnet_body_is_unchanged(self) -> None:
        with Stub(routes({"POST /api/v1/network/networks/net-1/subnets": ACCEPTED})) as stub:
            client_for(stub).network.create_subnet("net-1", name="v4", cidr="10.0.0.0/24")
            self.assertEqual(stub.requests[0].json(), {"name": "v4", "cidr": "10.0.0.0/24"})


class DecodingTest(unittest.TestCase):
    def test_ip_version_defaults_to_4_and_decodes_6(self) -> None:
        payload = {
            "floatingips": [
                {"id": "fip-4", "address": "203.0.113.7", "status": "available"},
                {"id": "fip-6", "address": "2001:db8:f::10", "ip_version": 6, "status": "attached"},
            ]
        }
        with Stub(routes({"GET /api/v1/network/floating-ips": payload})) as stub:
            fips = client_for(stub).network.list_floating_ips()
        self.assertEqual([fip.ip_version for fip in fips], [IPV4, IPV6])

        subnets = {
            "subnets": [
                {
                    "id": "sub-6",
                    "cidr": "2001:db8:100:1::/64",
                    "gateway_ip": "2001:db8:100:1::1",
                    "ip_version": 6,
                    "allocation_pools": [],
                }
            ]
        }
        with Stub(routes({"GET /api/v1/network/subnets": subnets})) as stub:
            subnet = client_for(stub).network.get_subnet("sub-6")
        self.assertEqual(subnet.ip_version, IPV6)
        self.assertEqual(subnet.cidr, "2001:db8:100:1::/64")
        self.assertNotIn("ip_version", subnet.extra)

    def test_capabilities(self) -> None:
        payload = {"capabilities": {"ipv6_floating_ips": True, "ipv6_subnets": False}}
        with Stub(routes({"GET /api/v1/network/capabilities": payload})) as stub:
            caps = client_for(stub).network.get_capabilities()
        self.assertTrue(caps.ipv6_floating_ips)
        self.assertFalse(caps.ipv6_subnets)


if __name__ == "__main__":
    unittest.main()


class SecurityGroupEgressTest(unittest.TestCase):
    def test_conversion_posts_without_a_body(self) -> None:
        route = "POST /api/v1/network/security-groups/sg-1/enable-egress-filtering"
        with Stub(routes({route: ACCEPTED})) as stub:
            ref = client_for(stub).network.enable_security_group_egress_filtering("sg-1")
            self.assertIsNone(stub.requests[0].json())
        self.assertEqual(ref.task_id, "t")

    def test_egress_filtering_decodes(self) -> None:
        payload = {
            "security_groups": [
                {"id": "sg-old", "name": "old", "egress_filtering": False, "rules": []},
                {"id": "sg-new", "name": "new", "egress_filtering": True, "rules": []},
                {"id": "sg-bare", "name": "bare", "rules": []},
            ],
            "count": 3,
        }
        with Stub(routes({"GET /api/v1/network/security-groups": payload})) as stub:
            groups = client_for(stub).network.list_security_groups()
        self.assertEqual([g.egress_filtering for g in groups], [False, True, False])
