"""Quota reads: both scopes, reservations, and which one binds."""

from __future__ import annotations

import unittest

from nemau import QuotaDimension, QuotaList
from tests.helpers import Stub, client_for, routes

QUOTAS = {
    "quotas": [
        {
            "scope": "tenant",
            "dimensions": [
                {"name": "cores", "unit": "count", "used": 95, "reserved": 0, "limit": 100},
                {"name": "ram", "unit": "MB", "used": 0, "reserved": 0, "limit": 51200},
            ],
        },
        {
            "scope": "project",
            "project_id": "11111111-1111-1111-1111-111111111111",
            "dimensions": [
                {"name": "cores", "unit": "count", "used": 8, "reserved": 4, "limit": 32},
                {
                    "name": "objectStorage",
                    "unit": "bytes",
                    "used": 4096,
                    "reserved": 0,
                    "limit": 107374182400,
                },
            ],
        },
    ]
}


class QuotasTest(unittest.TestCase):
    def test_reads_both_scopes(self) -> None:
        with Stub(routes({"GET /api/v1/quotas": QUOTAS})) as stub:
            quotas = client_for(stub).quotas.get()

        self.assertEqual(len(quotas.quotas), 2)
        project = quotas.scope("project")
        assert project is not None
        self.assertEqual(project.project_id, "11111111-1111-1111-1111-111111111111")
        cores = project.dimension("cores")
        assert cores is not None
        self.assertEqual((cores.used, cores.reserved, cores.limit), (8, 4, 32))
        self.assertIsNone(quotas.scope("nowhere"))
        self.assertIsNone(project.dimension("nothing"))

    def test_available_subtracts_reservations(self) -> None:
        # The API compares used + reserved + requested to the limit.
        self.assertEqual(QuotaDimension(used=8, reserved=4, limit=32).available, 20)
        # Exhausted means no headroom, not negative headroom.
        self.assertEqual(QuotaDimension(used=40, reserved=5, limit=32).available, 0)

    def test_binding_is_the_least_headroom(self) -> None:
        # A write needs room in both scopes: the organization carries the larger
        # limit and still refuses first, 5 left against the project's 20.
        quotas = QuotaList.from_dict(QUOTAS)
        cores = quotas.binding("cores")
        assert cores is not None
        self.assertEqual(cores.limit, 100)
        self.assertEqual(cores.available, 5)

        # Reported by a single scope: that one binds.
        store = quotas.binding("objectStorage")
        assert store is not None
        self.assertEqual(store.limit, 107374182400)
        self.assertIsNone(quotas.binding("nothing"))


if __name__ == "__main__":
    unittest.main()
