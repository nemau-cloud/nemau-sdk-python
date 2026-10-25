"""Load-balancer tiers and the HA state of a pair."""

from __future__ import annotations

import unittest

from tests.helpers import Stub, client_for

ACCEPTED = (202, {"task_id": "t", "status": "pending"})


class LoadBalancerTierTest(unittest.TestCase):
    def test_flavors_come_from_their_own_endpoint(self) -> None:
        payload = {
            "flavors": [
                {
                    "name": "lb-small",
                    "appliances": 1,
                    "vcpus": 1,
                    "ram_mb": 1024,
                    "high_availability": False,
                    "hourly_price": 0.0074,
                    "monthly_price": 5.4,
                    "currency": "eur",
                },
                {
                    "name": "lb-ha",
                    "appliances": 2,
                    "vcpus": 1,
                    "ram_mb": 1024,
                    "high_availability": True,
                    "hourly_price": 0.0141,
                    "monthly_price": 10.3,
                    "currency": "eur",
                },
            ],
            "default": "lb-small",
        }
        with Stub(lambda _r: (200, payload)) as stub:
            flavors = client_for(stub).load_balancer.list_flavors()
            seen = stub.requests[0]
            self.assertEqual(
                (seen.method, seen.path), ("GET", "/api/v1/network/load-balancer-flavors")
            )
        self.assertEqual([f.name for f in flavors], ["lb-small", "lb-ha"])
        self.assertTrue(flavors[1].high_availability)
        self.assertEqual(flavors[1].appliances, 2)
        self.assertEqual(flavors[1].monthly_price, 10.3)

    def test_the_tier_travels_only_when_set(self) -> None:
        for flavor, expected in (("lb-medium", "lb-medium"), ("", None)):
            with self.subTest(flavor=flavor):
                with Stub(lambda _r: ACCEPTED) as stub:
                    client_for(stub).load_balancer.create(
                        name="edge", vip_subnet_id="sub-1", flavor=flavor
                    )
                    body = stub.requests[0].json()
                self.assertEqual(body.get("flavor"), expected)

    def test_the_engine_is_never_sent(self) -> None:
        # There is no engine to choose: the API applies automatic mode.
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).load_balancer.create(name="edge", vip_subnet_id="sub-1")
            body = stub.requests[0].json()
        self.assertNotIn("provider", body)
        with self.assertRaises(TypeError):
            client_for(stub).load_balancer.create(  # type: ignore[call-arg]
                name="edge", vip_subnet_id="sub-1", provider="haproxy"
            )

    def test_the_ha_state_of_a_pair_is_decoded(self) -> None:
        payload = {
            "load_balancer": {
                "id": "lb-1",
                "name": "edge",
                "flavor": "lb-ha",
                "provider": "auto",
                "provisioning_status": "ACTIVE",
                "admin_state_up": True,
                "listeners": [],
                "pools": [],
                "high_availability": {
                    "status": "DEGRADED",
                    "reason": "APPLIANCE_UNAVAILABLE",
                    "appliances": [
                        {"index": 1, "state": "ACTIVE", "address": "10.0.0.11"},
                        {"index": 2, "state": "DOWN", "address": "10.0.0.12"},
                    ],
                },
            }
        }
        with Stub(lambda _r: (200, payload)) as stub:
            lb = client_for(stub).load_balancer.get("lb-1")
        self.assertEqual(lb.flavor, "lb-ha")
        self.assertEqual(lb.provider, "auto")
        self.assertIsNotNone(lb.high_availability)
        assert lb.high_availability is not None
        self.assertEqual(lb.high_availability.reason, "APPLIANCE_UNAVAILABLE")
        self.assertEqual([a.state for a in lb.high_availability.appliances], ["ACTIVE", "DOWN"])
        self.assertEqual(lb.high_availability.appliances[1].address, "10.0.0.12")

    def test_a_single_appliance_has_no_ha_block(self) -> None:
        payload = {
            "load_balancer": {
                "id": "lb-1",
                "name": "edge",
                "flavor": "lb-small",
                "listeners": [],
                "pools": [],
            }
        }
        with Stub(lambda _r: (200, payload)) as stub:
            lb = client_for(stub).load_balancer.get("lb-1")
        self.assertIsNone(lb.high_availability)


if __name__ == "__main__":
    unittest.main()
