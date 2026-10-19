"""Kubernetes: the listing's summary shape and the guided upgrade."""

from __future__ import annotations

import unittest

from nemau.services.kubernetes import KubernetesService
from tests.helpers import Stub, client_for, routes


class KubernetesListTest(unittest.TestCase):
    def test_list_decodes_the_summary_row(self) -> None:
        # The listing returns the platform's record of each cluster, not the
        # Cluster API object: a lowercase status and the figures in specs.
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


if __name__ == "__main__":
    unittest.main()
