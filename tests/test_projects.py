"""Project selection: header, client copy, discovery."""

from __future__ import annotations

import unittest

from nemau import PROJECT_SCOPE_HEADER, AccessKey, NemauApiError, is_not_found
from tests.helpers import Stub, client_for, routes

PROJECTS = {
    "projects": [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "staging",
            "slug": "staging",
            "region": "fr-occitanie-1",
            "is_active": True,
            "provisioning_status": "active",
            "current": False,
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "prod",
            "slug": "prod",
            "region": "fr-occitanie-1",
            "is_active": True,
            "provisioning_status": "pending",
            "current": True,
        },
    ],
    "count": 2,
    "current_project_id": "22222222-2222-2222-2222-222222222222",
}

STAGING_ID = PROJECTS["projects"][0]["id"]
PROD_ID = PROJECTS["projects"][1]["id"]

LISTING = routes({"GET /api/v1/projects": PROJECTS})


class HeaderTest(unittest.TestCase):
    def test_no_header_until_a_project_is_chosen(self) -> None:
        # An empty header would read as an empty selector; with no choice
        # made, the credential's own scope must apply as-is.
        with Stub(routes({"GET /api/v1/compute/instances": {"instances": []}})) as stub:
            client_for(stub).compute.list_instances()
            self.assertNotIn(PROJECT_SCOPE_HEADER, stub.requests[0].headers)

    def test_chosen_project_travels_as_a_header(self) -> None:
        with Stub(routes({"GET /api/v1/compute/instances": {"instances": []}})) as stub:
            client_for(stub, project="proj-42").compute.list_instances()
            self.assertEqual(stub.requests[0].headers[PROJECT_SCOPE_HEADER], "proj-42")

    def test_project_comes_from_the_environment(self) -> None:
        with Stub(routes({})) as stub:
            client = client_for(stub, env={"NEMAU_PROJECT_ID": "proj-env"}, project=None)
            self.assertEqual(client.project, "proj-env")
            # The explicit argument wins.
            explicit = client_for(stub, env={"NEMAU_PROJECT_ID": "proj-env"}, project="proj-opt")
            self.assertEqual(explicit.project, "proj-opt")


class ForProjectTest(unittest.TestCase):
    def test_for_project_copies_rather_than_mutates(self) -> None:
        with Stub(routes({"GET /api/v1/network/networks": {"networks": []}})) as stub:
            base = client_for(stub)
            staging = base.for_project("proj-staging")
            prod = base.for_project("proj-prod")

            for client in (base, staging, prod, base):
                client.network.list_networks()

            seen = [r.headers.get(PROJECT_SCOPE_HEADER) for r in stub.requests]
            self.assertEqual(seen, [None, "proj-staging", "proj-prod", None])
            self.assertEqual(base.project, "")

    def test_for_project_keeps_the_credential(self) -> None:
        with Stub(routes({"GET /api/v1/dns/zones": {"zones": []}})) as stub:
            base = client_for(stub, credential=AccessKey("AKIA", "s3cr3t"))
            base.for_project("proj-9").dns.list_zones()

            seen = stub.requests[0]
            self.assertEqual(seen.headers["X-Access-Key-Id"], "AKIA")
            self.assertEqual(seen.headers[PROJECT_SCOPE_HEADER], "proj-9")

    def test_for_project_keeps_the_user_agent(self) -> None:
        with Stub(routes({"GET /api/v1/dns/zones": {"zones": []}})) as stub:
            base = client_for(stub, user_agent="backup-job/2")
            base.for_project("proj-9").dns.list_zones()

            agent = stub.requests[0].headers["User-Agent"]
            self.assertTrue(agent.startswith("backup-job/2 nemau-sdk-python/"))


class DiscoveryTest(unittest.TestCase):
    def test_list_lookup_and_current(self) -> None:
        with Stub(LISTING) as stub:
            client = client_for(stub)

            listing = client.projects.list()
            self.assertEqual(stub.requests[0].path, "/api/v1/projects")
            self.assertEqual(listing.current_project_id, PROD_ID)

            self.assertEqual(client.projects.get(STAGING_ID).name, "staging")
            self.assertEqual(client.projects.get("prod").id, PROD_ID)
            self.assertEqual(client.projects.get("staging").id, STAGING_ID)

            current = client.projects.current()
            assert current is not None
            self.assertEqual(current.name, "prod")

    def test_ready_distinguishes_a_provisioning_project(self) -> None:
        with Stub(LISTING) as stub:
            projects = client_for(stub).projects.list().projects
            # A project still provisioning is listed but refuses every write:
            # that is what a caller needs to know before selecting it.
            self.assertTrue(projects[0].ready)
            self.assertFalse(projects[1].ready)

    def test_unknown_project_is_a_404(self) -> None:
        with Stub(LISTING) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError) as caught:
                client.projects.get("qa")
            self.assertTrue(is_not_found(caught.exception))

    def test_current_is_none_when_nothing_resolves(self) -> None:
        payload = {
            "projects": [dict(PROJECTS["projects"][0], current=False)],
            "count": 1,
            "current_project_id": "",
        }
        with Stub(routes({"GET /api/v1/projects": payload})) as stub:
            self.assertIsNone(client_for(stub).projects.current())


if __name__ == "__main__":
    unittest.main()
