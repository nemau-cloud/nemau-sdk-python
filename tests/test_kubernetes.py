"""Kubernetes: the listing's summary shape, the cluster contract, and the guided upgrade."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from nemau import KubernetesCluster
from nemau.services.kubernetes import KubernetesService
from tests.helpers import Stub, client_for, routes

#: The read contract of a cluster, as the API returns it.
GOLDEN_PATH = Path(__file__).parent / "fixtures" / "kubernetes_cluster.golden.json"
GOLDEN: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


class KubernetesListTest(unittest.TestCase):
    def test_list_decodes_the_summary_row(self) -> None:
        # The listing returns the platform's record of each cluster, not the
        # cluster object: a lowercase status and the figures in specs.
        payload = {
            "clusters": [
                {
                    "id": "c1",
                    "resourceType": "kubernetes_cluster",
                    "name": "prod",
                    "status": "active",
                    "projectId": "p",
                    "specs": {
                        "phase": "Provisioned",
                        "control_plane_ready": True,
                        "kubernetes_version": "v1.35.0",
                        "control_plane_replicas": 3,
                        "node_pools_count": 1,
                        "billing_future_key": 7,
                    },
                    "createdAt": "2026-09-01T10:00:00Z",
                    "updatedAt": "2026-09-01T10:05:00Z",
                },
                {"id": "c2", "name": "staging", "status": "building", "specs": {}},
            ],
            "count": 2,
        }
        with Stub(routes({"GET /api/v1/kubernetes/clusters": payload})) as stub:
            clusters = client_for(stub).kubernetes.list_clusters()
        self.assertEqual(len(clusters), 2)
        prod = clusters[0]
        self.assertEqual(prod.status, "active")
        self.assertEqual(prod.specs.phase, "Provisioned")
        self.assertEqual(prod.specs.kubernetes_version, "v1.35.0")
        self.assertTrue(prod.specs.control_plane_ready)
        self.assertEqual(prod.specs.control_plane_replicas, 3)
        self.assertEqual(prod.specs.extra["billing_future_key"], 7)
        self.assertEqual(prod.created_at, "2026-09-01T10:00:00Z")
        self.assertEqual(clusters[1].status, "building")
        self.assertEqual(clusters[1].specs.phase, "")


class KubernetesUpgradeTest(unittest.TestCase):
    def test_upgrade_cluster_posts_the_version_and_nothing_else(self) -> None:
        # One server-side task runs the preflight, the window and the upgrade:
        # the client sends a single request whose body holds only `version`.
        table = {"POST /api/v1/kubernetes/clusters/prod/upgrade": (202, {"task_id": "task-up"})}
        with Stub(routes(table)) as stub:
            ref = client_for(stub).kubernetes.upgrade_cluster("prod", "v1.36.0")
        self.assertEqual(ref.task_id, "task-up")
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(stub.requests[0].json(), {"version": "v1.36.0"})

    def test_upgrade_cluster_escapes_the_name(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client_for(stub).kubernetes.upgrade_cluster("a/b", "1.36.0")
        self.assertEqual(stub.requests[0].path, "/api/v1/kubernetes/clusters/a%2Fb/upgrade")

    def test_upgrade_cluster_rejects_a_malformed_version_before_the_request(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            kubernetes = client_for(stub).kubernetes
            for bad in ("", "1.36", "latest", "v1.36.0; rm", "1.36.0\n"):
                with self.assertRaises(ValueError, msg=bad):
                    kubernetes.upgrade_cluster("prod", bad)
            with self.assertRaises(ValueError):
                kubernetes.upgrade_cluster("", "1.36.0")
        self.assertEqual(stub.requests, [])

    def test_upgrade_cluster_sends_an_idempotency_key(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client_for(stub).kubernetes.upgrade_cluster("prod", "1.36.0")
        self.assertTrue(stub.requests[0].headers["Idempotency-Key"])

    def test_there_is_no_client_side_upgrade_sequence(self) -> None:
        # The window and the preflight are part of the upgrade task.
        service = KubernetesService(None)  # type: ignore[arg-type]
        self.assertFalse(hasattr(service, "upgrade"))
        self.assertFalse(hasattr(service, "start_upgrade"))


NETWORK_ID = "6f1c2a4e-3b7d-4e8a-9c0f-1d2e3f4a5b6c"
SUBNET_ID = "0a9b8c7d-6e5f-4a3b-8c2d-1e0f9a8b7c6d"


def _create(client, **overrides):  # type: ignore[no-untyped-def]
    """A cluster creation with valid defaults, overridden per test."""
    arguments = {
        "name": "prod",
        "kubernetes_version": "v1.31.0",
        "network_id": NETWORK_ID,
        "subnet_id": SUBNET_ID,
        "control_plane_replicas": 3,
        "node_pools": [{"name": "workers", "replicas": 3, "flavor_id": "flavor-1"}],
    }
    arguments.update(overrides)
    return client.kubernetes.create_cluster(**arguments)


class KubernetesClusterCreateTest(unittest.TestCase):
    def test_refuses_a_missing_or_malformed_network_before_any_request(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            for bad in ("", "not-a-uuid", NETWORK_ID + "\n", None):
                with self.subTest(network_id=bad), self.assertRaises(ValueError) as caught:
                    _create(client, network_id=bad)
                self.assertIn("network_id", str(caught.exception))
            with self.assertRaises(TypeError):
                client.kubernetes.create_cluster(  # type: ignore[call-arg]
                    name="prod",
                    kubernetes_version="v1.31.0",
                    subnet_id=SUBNET_ID,
                    control_plane_replicas=3,
                    node_pools=[{"name": "workers", "flavor_id": "f"}],
                )
        self.assertEqual(stub.requests, [])

    def test_refuses_a_missing_or_malformed_subnet_before_any_request(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            for bad in ("", "subnet-1", SUBNET_ID.upper() + "x"):
                with self.subTest(subnet_id=bad), self.assertRaises(ValueError) as caught:
                    _create(client, subnet_id=bad)
                self.assertIn("subnet_id", str(caught.exception))
            with self.assertRaises(TypeError):
                client.kubernetes.create_cluster(  # type: ignore[call-arg]
                    name="prod",
                    kubernetes_version="v1.31.0",
                    network_id=NETWORK_ID,
                    control_plane_replicas=3,
                    node_pools=[{"name": "workers", "flavor_id": "f"}],
                )
        self.assertEqual(stub.requests, [])

    def test_sends_network_and_subnet_and_omits_unset_cidrs(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            _create(client_for(stub))
        body = stub.requests[0].json()
        self.assertEqual(body["network_id"], NETWORK_ID)
        self.assertEqual(body["subnet_id"], SUBNET_ID)
        self.assertNotIn("pod_cidr", body)
        self.assertNotIn("service_cidr", body)

    def test_sends_the_cidrs_when_set(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            _create(client_for(stub), pod_cidr="10.244.0.0/16", service_cidr="10.96.0.0/12")
        body = stub.requests[0].json()
        self.assertEqual(body["pod_cidr"], "10.244.0.0/16")
        self.assertEqual(body["service_cidr"], "10.96.0.0/12")

    def test_accepts_valid_labels_and_taints(self) -> None:
        pool = {
            "name": "gpu",
            "replicas": 2,
            "flavor_id": "flavor-1",
            "labels": ["team=platform", "zone=eu.west_1-a", "tier=A1"],
            "taints": ["dedicated=gpu:NoSchedule", "spot:PreferNoSchedule", "drain:NoExecute"],
        }
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            _create(client_for(stub), node_pools=[pool])
        sent = stub.requests[0].json()["node_pools"][0]
        self.assertEqual(sent["labels"], pool["labels"])
        self.assertEqual(sent["taints"], pool["taints"])

    def test_refuses_invalid_labels_and_taints_before_any_request(self) -> None:
        cases = {
            "label without =": {"labels": ["team"]},
            "label with an empty value": {"labels": ["team="]},
            "label with a bad key": {"labels": ["Team=a"]},
            "label key starting with a dash": {"labels": ["-team=a"]},
            "label value with a space": {"labels": ["team=a b"]},
            "label value too long": {"labels": ["team=" + "a" * 64]},
            "17 labels": {"labels": [f"k{i}=v" for i in range(17)]},
            "taint without an effect": {"taints": ["dedicated=gpu"]},
            "taint with a bad effect": {"taints": ["dedicated:Evict"]},
            "taint with a bad key": {"taints": ["Dedicated=gpu:NoSchedule"]},
            "taint with an empty value": {"taints": ["dedicated=:NoSchedule"]},
            "taint with a bad value": {"taints": ["dedicated=a/b:NoSchedule"]},
            "taint with two colons": {"taints": ["dedicated:gpu:NoSchedule"]},
            "17 taints": {"taints": [f"k{i}:NoSchedule" for i in range(17)]},
            "class other than default-worker": {"class": "gpu-worker"},
        }
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            for label, extra in cases.items():
                pool = {"name": "gpu", "replicas": 1, "flavor_id": "flavor-1", **extra}
                with self.subTest(label), self.assertRaises(ValueError) as caught:
                    _create(client, node_pools=[pool])
                self.assertIn('"gpu"', str(caught.exception))
        self.assertEqual(stub.requests, [])

    def test_the_limit_of_16_labels_and_taints_is_inclusive(self) -> None:
        pool = {
            "name": "gpu",
            "replicas": 1,
            "flavor_id": "flavor-1",
            "labels": [f"k{i}=v" for i in range(16)],
            "taints": [f"k{i}:NoSchedule" for i in range(16)],
        }
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            _create(client_for(stub), node_pools=[pool])
        self.assertEqual(len(stub.requests), 1)

    def test_add_node_pool_sends_labels_and_taints_and_refuses_bad_ones(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            client.kubernetes.add_node_pool(
                "prod",
                pool_name="gpu",
                node_class="default-worker",
                flavor_id="flavor-1",
                replicas=1,
                labels=["team=platform"],
                taints=["dedicated=gpu:NoSchedule"],
            )
            body = stub.requests[0].json()
            self.assertEqual(body["labels"], ["team=platform"])
            self.assertEqual(body["taints"], ["dedicated=gpu:NoSchedule"])
            for bad in (
                {"labels": ["Team=a"]},
                {"taints": ["dedicated=gpu"]},
                {"taints": [f"k{i}:NoSchedule" for i in range(17)]},
            ):
                with self.subTest(bad), self.assertRaises(ValueError) as caught:
                    client.kubernetes.add_node_pool(
                        "prod", pool_name="gpu", node_class="default-worker", flavor_id="f", **bad
                    )
                self.assertIn('"gpu"', str(caught.exception))
        self.assertEqual(len(stub.requests), 1, "a refused pool sends no request")

    def test_the_cluster_view_decodes_the_golden_contract(self) -> None:
        cluster = KubernetesCluster.from_dict(GOLDEN)
        self.assertEqual(cluster.extra, {})
        assert cluster.spec is not None
        self.assertEqual(cluster.spec.network_id, "11111111-1111-4111-8111-111111111111")
        self.assertEqual(cluster.spec.subnet_id, "22222222-2222-4222-8222-222222222222")
        self.assertEqual(cluster.spec.pod_cidr, "10.245.0.0/16")
        self.assertEqual(cluster.spec.service_cidr, "10.96.0.0/12")
        assert cluster.spec.topology is not None
        general, batch = cluster.spec.topology.node_pools
        self.assertEqual(general.labels, ["workload=general"])
        self.assertEqual(general.taints, ["dedicated=general:NoSchedule"])
        self.assertEqual((batch.labels, batch.taints), ([], []))

    def test_the_default_worker_class_and_an_empty_class_are_accepted(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            _create(client, node_pools=[{"name": "a", "class": "default-worker", "replicas": 1}])
            _create(client, node_pools=[{"name": "b", "class": "", "replicas": 1}])
        self.assertEqual(len(stub.requests), 2)


if __name__ == "__main__":
    unittest.main()
