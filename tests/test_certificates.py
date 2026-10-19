"""TLS certificates of load balancers.

The fixtures are placeholders ("CERT", "KEY"): the SDK relays PEM as given and
the API does the validation.
"""

from __future__ import annotations

import unittest

from nemau import NemauApiError, is_certificate_in_use
from tests.helpers import Stub, client_for

CERTIFICATE = {
    "id": "crt-1",
    "name": "www",
    "fingerprint_sha256": "AB:CD",
    "subject": "www.example.com",
    "domains": ["www.example.com", "example.com"],
    "issuer": "Example CA (R11)",
    "not_before": "2026-09-01T00:00:00Z",
    "not_after": "2026-12-01T00:00:00Z",
    "key_algorithm": "ECDSA-P256",
    "chain_length": 1,
    "used_by": [{"listener_id": "lis-1", "listener_name": "https", "load_balancer_id": "lb-1"}],
    "created_at": "2026-09-23T10:00:00Z",
}


class CertificateTest(unittest.TestCase):
    def test_import_posts_the_pem_and_returns_metadata(self) -> None:
        with Stub(lambda _r: (201, CERTIFICATE)) as stub:
            cert = client_for(stub).load_balancer.import_certificate(
                name="www", certificate="CERT", private_key="KEY"
            )
            seen = stub.requests[0]
        self.assertEqual(
            (seen.method, seen.path), ("POST", "/api/v1/network/load-balancer-certificates")
        )
        self.assertEqual(seen.json(), {"name": "www", "certificate": "CERT", "private_key": "KEY"})
        self.assertEqual(cert.domains, ["www.example.com", "example.com"])
        self.assertEqual(cert.used_by[0].listener_name, "https")

    def test_list_get_and_delete(self) -> None:
        def handler(request):  # type: ignore[no-untyped-def]
            if request.method == "DELETE":
                return 204, ""
            if request.path.endswith("/crt-1"):
                return 200, CERTIFICATE
            return 200, {"certificates": [CERTIFICATE], "count": 1}

        with Stub(handler) as stub:
            lb = client_for(stub).load_balancer
            self.assertEqual(lb.list_certificates()[0].id, "crt-1")
            self.assertEqual(lb.get_certificate("crt-1").key_algorithm, "ECDSA-P256")
            lb.delete_certificate("crt-1")
            seen = [f"{r.method} {r.path}" for r in stub.requests]
        self.assertEqual(
            seen,
            [
                "GET /api/v1/network/load-balancer-certificates",
                "GET /api/v1/network/load-balancer-certificates/crt-1",
                "DELETE /api/v1/network/load-balancer-certificates/crt-1",
            ],
        )

    def test_delete_in_use_is_recognizable(self) -> None:
        refusal = {"error": "certificate is used by a listener", "reason": "certificate_in_use"}
        refused = Stub(lambda _r: (409, refusal))
        with refused as stub, self.assertRaises(NemauApiError) as caught:
            client_for(stub).load_balancer.delete_certificate("crt-1")
        self.assertTrue(is_certificate_in_use(caught.exception))

    def test_a_listener_can_be_pointed_at_another_certificate(self) -> None:
        with Stub(lambda _r: (202, {"task_id": "t", "status": "pending"})) as stub:
            client_for(stub).load_balancer.update_listener("lis-1", tls_certificate_ref="crt-2")
            seen = stub.requests[0]
        self.assertEqual((seen.method, seen.path), ("PATCH", "/api/v1/network/listeners/lis-1"))
        self.assertEqual(seen.json(), {"tls_certificate_ref": "crt-2"})


if __name__ == "__main__":
    unittest.main()
