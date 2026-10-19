"""URL and body shapes the platform imposes."""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs

from nemau import NemauApiError, is_not_found
from tests.helpers import Stub, client_for, routes

VOLID = "pool-a:104/disk-0.qcow2"
ACCEPTED = (202, {"task_id": "t", "status": "pending"})


class AddressingTest(unittest.TestCase):
    def test_volume_operations_carry_the_id_in_the_query(self) -> None:
        """A volume id can contain a slash. In the path it would add a segment,
        the route would stop matching, and the caller would get a bare 404
        instead of a readable error."""
        cases = [
            (
                "delete",
                lambda c: c.storage.delete_volume(VOLID),
                "DELETE",
                "/api/v1/storage/volumes",
            ),
            (
                "attach",
                lambda c: c.storage.attach_volume(VOLID, "inst-1"),
                "POST",
                "/api/v1/storage/volumes/attach",
            ),
            (
                "detach",
                lambda c: c.storage.detach_volume(VOLID, "inst-1"),
                "POST",
                "/api/v1/storage/volumes/detach",
            ),
            (
                "extend",
                lambda c: c.storage.extend_volume(VOLID, 50),
                "PUT",
                "/api/v1/storage/volumes/extend",
            ),
        ]
        for name, call, method, path in cases:
            with self.subTest(operation=name), Stub(lambda _r: ACCEPTED) as stub:
                call(client_for(stub))
                seen = stub.requests[0]
                self.assertEqual(seen.method, method)
                self.assertEqual(seen.path, path)
                self.assertEqual(parse_qs(seen.query)["id"], [VOLID])

    def test_record_set_id_travels_in_the_query(self) -> None:
        # "app.example.com./A" contains a slash too, so it also travels in the
        # query.
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).dns.delete_record_set("zone-1", "app.example.com./A")
            seen = stub.requests[0]
            self.assertEqual(seen.path, "/api/v1/dns/zones/zone-1/recordsets")
            self.assertEqual(parse_qs(seen.query)["id"], ["app.example.com./A"])


class BodyTest(unittest.TestCase):
    def test_empty_security_group_list_survives(self) -> None:
        # An empty list removes all filtering from the instance, so it must be
        # sent as an empty array, not dropped.
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).compute.set_security_groups("inst-1", [])
            seen = stub.requests[0]
            self.assertEqual(seen.method, "PUT")
            self.assertEqual(seen.path, "/api/v1/compute/instances/inst-1/security-groups")
            self.assertEqual(seen.json(), {"security_groups": []})

    def test_upload_sets_content_length_and_encodes_the_key(self) -> None:
        # The API answers 411 without a Content-Length.
        with Stub(lambda _r: (201, {"object_name": "a/b.txt", "size": 5})) as stub:
            uploaded = client_for(stub).storage.upload_object(
                "bucket", "a/b.txt", "hello", "text/plain"
            )
            seen = stub.requests[0]
            self.assertEqual(seen.headers["Content-Length"], "5")
            self.assertEqual(seen.headers["Content-Type"], "text/plain")
            self.assertEqual(parse_qs(seen.query)["key"], ["a/b.txt"])
            self.assertEqual(uploaded.size, 5)

    def test_oversized_upload_fails_before_the_request(self) -> None:
        with Stub(lambda _r: (201, {})) as stub:
            client = client_for(stub)
            with self.assertRaises(ValueError):
                client.storage.upload_object("bucket", "k", b"x" * (100 * 1024 * 1024 + 1))
            self.assertEqual(stub.requests, [])


class DecodingTest(unittest.TestCase):
    def test_floating_ips_use_the_platform_key(self) -> None:
        payload = {"floatingips": [{"id": "fip-1", "address": "203.0.113.7", "status": "ACTIVE"}]}
        with Stub(routes({"GET /api/v1/network/floating-ips": payload})) as stub:
            fips = client_for(stub).network.list_floating_ips()
            self.assertEqual(fips[0].address, "203.0.113.7")

    def test_list_and_filter_read_produces_a_real_404(self) -> None:
        with Stub(
            routes({"GET /api/v1/network/networks": {"networks": [{"id": "net-1"}]}})
        ) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError) as caught:
                client.network.get_network("net-absent")
            self.assertTrue(is_not_found(caught.exception))

    def test_a_synthesised_404_names_the_resource_in_english(self) -> None:
        empty = {
            "GET /api/v1/network/floating-ips": {"floatingips": []},
            "GET /api/v1/dns/zones": {"zones": []},
            "GET /api/v1/network/pools/p-1/members": {"members": []},
        }
        with Stub(routes(empty)) as stub:
            client = client_for(stub)
            for read, kind in (
                (lambda: client.network.get_floating_ip("x"), "floating ip"),
                (lambda: client.dns.get_zone("x"), "dns zone"),
                (lambda: client.load_balancer.get_pool_member("p-1", "x"), "pool member"),
            ):
                with self.subTest(kind=kind):
                    with self.assertRaises(NemauApiError) as caught:
                        read()
                    self.assertIn(f'{kind} "x" not found', str(caught.exception))

    def test_kubernetes_cluster_is_name_addressed(self) -> None:
        payload = {
            "id": "c1",
            "name": "prod",
            "spec": {"topology": {"version": "1.30", "class": "std"}},
            "status": {"phase": "Provisioned", "controlPlaneReady": True},
        }
        with Stub(routes({"GET /api/v1/kubernetes/clusters/prod": payload})) as stub:
            cluster = client_for(stub).kubernetes.get_cluster("prod")
            self.assertEqual(stub.requests[0].path, "/api/v1/kubernetes/clusters/prod")
            self.assertEqual(cluster.status.phase, "Provisioned")
            # camelCase on the API, snake_case in Python.
            self.assertTrue(cluster.status.control_plane_ready)
            # "class" is reserved in Python: the alias keeps it readable.
            self.assertEqual(cluster.spec.topology.node_class, "std")

    def test_cluster_creation_carries_replicas_and_node_pools(self) -> None:
        """The API refuses a cluster without control-plane replicas or node
        pools; a pool of zero nodes keeps its 0."""
        with Stub(routes({"POST /api/v1/kubernetes/clusters": ACCEPTED})) as stub:
            client_for(stub).kubernetes.create_cluster(
                name="prod",
                kubernetes_version="v1.31.0",
                control_plane_vcpus=2,
                control_plane_ram_mb=4096,
                control_plane_disk_gb=20,
                control_plane_replicas=3,
                node_pools=[
                    {
                        "name": "workers",
                        "class": "default-worker",
                        "replicas": 3,
                        "vcpus": 2,
                        "ram_mb": 4096,
                        "disk_gb": 20,
                    },
                    {"name": "standby", "flavor_id": "flavor-1", "replicas": 0},
                ],
            )
            body = stub.requests[0].json()
            self.assertEqual(body["control_plane_replicas"], 3)
            self.assertEqual(len(body["node_pools"]), 2)
            self.assertEqual(body["node_pools"][1]["replicas"], 0)
            self.assertEqual(body["node_pools"][1]["flavor_id"], "flavor-1")

    def test_cluster_creation_requires_replicas_and_node_pools(self) -> None:
        with Stub(routes({})) as stub:
            with self.assertRaises(TypeError):
                client_for(stub).kubernetes.create_cluster(  # type: ignore[call-arg]
                    name="prod", kubernetes_version="v1.31.0"
                )
            self.assertEqual(stub.requests, [])

    def test_regions_are_a_bare_array(self) -> None:
        payload = [{"slug": "fr-occitanie-1", "displayName": "Occitanie", "country": "FR"}]
        with Stub(routes({"GET /api/v1/regions": payload})) as stub:
            regions = client_for(stub).regions.list()
            self.assertEqual(regions[0].slug, "fr-occitanie-1")
            self.assertEqual(regions[0].display_name, "Occitanie")

    def test_unknown_fields_are_kept_not_fatal(self) -> None:
        # A field added to the API must not break an existing SDK release.
        payload = {"networks": [{"id": "net-1", "name": "prod", "brand_new_field": 42}]}
        with Stub(routes({"GET /api/v1/network/networks": payload})) as stub:
            networks = client_for(stub).network.list_networks()
            self.assertEqual(networks[0].id, "net-1")
            self.assertEqual(networks[0].extra["brand_new_field"], 42)


class SnapshotTest(unittest.TestCase):
    def test_restore_posts_to_the_restore_route_without_a_body(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            ref = client_for(stub).compute.restore_snapshot("snap-1")
            seen = stub.requests[0]
            self.assertEqual(ref.task_id, "t")
            self.assertEqual(seen.method, "POST")
            self.assertEqual(seen.path, "/api/v1/compute/snapshots/snap-1/restore")
            self.assertEqual(seen.body, b"")

    def test_listing_carries_storage_size_and_cost(self) -> None:
        # None, not zero, until an object-store snapshot is active, and always
        # None on hypervisor snapshots.
        listing = {
            "snapshots": [
                {
                    "id": "s1",
                    "name": "a",
                    "instance_id": "i1",
                    "status": "active",
                    "storage": "object_store",
                    "size_bytes": 2147483648,
                    "source_disk_gb": 20,
                    "monthly_cost": 0.1,
                    "currency": "eur",
                    "restorable": True,
                },
                {
                    "id": "s2",
                    "name": "b",
                    "instance_id": "i1",
                    "status": "active",
                    "storage": "hypervisor",
                    "restorable": False,
                },
            ],
            "count": 2,
        }
        with Stub(lambda _r: (200, listing)) as stub:
            items = client_for(stub).compute.list_snapshots("i1")
            self.assertEqual(parse_qs(stub.requests[0].query)["instance_id"], ["i1"])
        self.assertEqual(items[0].size_bytes, 2147483648)
        self.assertEqual(items[0].source_disk_gb, 20)
        self.assertEqual(items[0].monthly_cost, 0.1)
        self.assertTrue(items[0].restorable)
        self.assertEqual(items[1].storage, "hypervisor")
        self.assertIsNone(items[1].size_bytes)
        self.assertFalse(items[1].restorable)


if __name__ == "__main__":
    unittest.main()
