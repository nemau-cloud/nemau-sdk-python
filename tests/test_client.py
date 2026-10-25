"""Transport: credentials, errors, retries."""

from __future__ import annotations

import unittest

from nemau import (
    REASON_SUBNET_OVERLAP,
    AccessKey,
    BearerToken,
    NemauApiError,
    NemauClient,
    credential_from_env,
    is_forbidden,
    is_subnet_overlap,
)
from tests.helpers import Stub, client_for, routes


class CredentialTest(unittest.TestCase):
    def test_access_key_sends_its_two_headers_and_nothing_else(self) -> None:
        with Stub(routes({"GET /api/v1/compute/instances": {"instances": []}})) as stub:
            client_for(stub, credential=AccessKey("AKIA", "s3cr3t")).compute.list_instances()

            seen = stub.requests[0]
            self.assertEqual(seen.headers["X-Access-Key-Id"], "AKIA")
            self.assertEqual(seen.headers["X-Access-Key-Secret"], "s3cr3t")
            # The SDK sends exactly one credential: an extra Authorization
            # header could make the API authenticate the wrong one.
            self.assertNotIn("Authorization", seen.headers)
            self.assertTrue(seen.headers["User-Agent"].startswith("nemau-sdk-python/"))

    def test_service_account_token_goes_in_bearer(self) -> None:
        with Stub(routes({"GET /api/v1/dns/zones": {"zones": []}})) as stub:
            client_for(stub, credential=BearerToken("jwt")).dns.list_zones()
            self.assertEqual(stub.requests[0].headers["Authorization"], "Bearer jwt")

    def test_access_key_wins_over_token(self) -> None:
        # A stale NEMAU_TOKEN left in a shell must not shadow the long-lived
        # credential.
        both = credential_from_env(
            {
                "NEMAU_ACCESS_KEY_ID": "id",
                "NEMAU_ACCESS_KEY_SECRET": "secret",
                "NEMAU_TOKEN": "token",
            }
        )
        self.assertIsInstance(both, AccessKey)
        self.assertIsInstance(credential_from_env({"NEMAU_TOKEN": "token"}), BearerToken)
        self.assertIsNone(credential_from_env({}))

    def test_secrets_never_appear_in_repr(self) -> None:
        # A traceback prints reprs: the secret must not be in one.
        self.assertNotIn("s3cr3t", repr(AccessKey("AKIA", "s3cr3t")))
        self.assertNotIn("jwt", repr(BearerToken("jwt")))


class ErrorTest(unittest.TestCase):
    def test_api_error_keeps_message_and_reason(self) -> None:
        payload = {"error": "not authorized for compute:Write", "reason": "permission_denied"}
        with Stub(lambda _request: (403, payload)) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError) as caught:
                client.compute.create_instance(name="x", image_id="img", vcpus=1)
            error = caught.exception
            self.assertTrue(is_forbidden(error))
            self.assertIn("not authorized for compute:Write", str(error))
            self.assertIn("permission_denied", str(error))

    def test_subnet_overlap_is_recognisable_and_names_the_conflict(self) -> None:
        payload = {
            "error": 'cidr 10.0.1.0/24 overlaps subnet "app" (10.0.0.0/16) on network "backend"; '
            "the subnets of a project are routed together, so their ranges must not overlap",
            "reason": REASON_SUBNET_OVERLAP,
        }
        with Stub(lambda _request: (409, payload)) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError) as caught:
                client.network.create_subnet("net-1", name="web", cidr="10.0.1.0/24")
            error = caught.exception
            self.assertTrue(is_subnet_overlap(error))
            self.assertEqual(error.status_code, 409)
            self.assertIn('overlaps subnet "app" (10.0.0.0/16) on network "backend"', str(error))
            self.assertEqual(len(stub.requests), 1, "a refusal must not be retried")

        others = [
            NemauApiError(409, "listener already has a pool", method="POST", path="/x"),
            NemauApiError(403, "no", method="POST", path="/x", reason="permission_denied"),
            ValueError("subnet_overlap"),
        ]
        for other in others:
            self.assertFalse(is_subnet_overlap(other))


class RetryTest(unittest.TestCase):
    def test_503_is_retried(self) -> None:
        calls = {"n": 0}

        def handler(_request):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] < 3:
                return 503, {"error": "upstream unavailable"}
            return 200, {"networks": [{"id": "net-1", "name": "prod"}]}

        with Stub(handler) as stub:
            networks = client_for(stub).network.list_networks()
            self.assertEqual(len(networks), 1)
            self.assertEqual(calls["n"], 3)

    def test_400_is_not_retried(self) -> None:
        with Stub(lambda _request: (400, {"error": "sizing required"})) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError):
                client.compute.create_instance(name="x", image_id="i", vcpus=1)
            self.assertEqual(len(stub.requests), 1)


class ConstructionTest(unittest.TestCase):
    def test_relative_base_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            NemauClient(base_url="not-a-url", env={})

    def test_write_without_task_id_is_an_error(self) -> None:
        # A 202 without a task id leaves nothing to wait for.
        with Stub(lambda _request: (202, {})) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError) as caught:
                client.network.create_network("prod")
            self.assertIn("no task id", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
