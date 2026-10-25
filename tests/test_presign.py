"""Presigned URLs: direct transfers to and from the object store."""

from __future__ import annotations

import json
import unittest

from nemau import PRESIGN_TTL_CAP_SECONDS, AccessKey, NemauApiError, PresignedObject
from tests.helpers import Stub, client_for, routes

PRESIGNED = {
    "url": "https://s3.example.test/bucket/a.txt?X-Amz-Signature=abc",
    "method": "GET",
    "expires_at": "2099-01-01T00:00:00Z",
}


class PresignTest(unittest.TestCase):
    def test_presign_get_calls_the_endpoint(self) -> None:
        with Stub(routes({"POST /api/v1/storage/containers/bucket/presign": PRESIGNED})) as stub:
            presigned = client_for(stub).storage.presign_get("bucket", "a/b.txt", 300)

            seen = stub.requests[0]
            self.assertEqual(seen.method, "POST")
            self.assertEqual(
                json.loads(seen.body), {"key": "a/b.txt", "method": "GET", "expires_in": 300}
            )
            self.assertEqual(presigned.url, PRESIGNED["url"])

    def test_ttl_above_the_cap_is_refused_before_any_call(self) -> None:
        # Refused client-side so the caller learns the cap from the message
        # rather than from a 400 after a round trip.
        with Stub(routes({})) as stub:
            client = client_for(stub)
            with self.assertRaises(ValueError):
                client.storage.presign_get("bucket", "k", PRESIGN_TTL_CAP_SECONDS + 1)
            self.assertEqual(stub.requests, [])


class FetchPresignedTest(unittest.TestCase):
    def test_no_credential_reaches_the_object_store(self) -> None:
        # A presigned URL is already authorised; attaching the access key
        # would hand it to a host that is not the API.
        with Stub(lambda _r: (200, b"content")) as store:
            with Stub(routes({})) as api:
                client = client_for(api, credential=AccessKey("AKIA", "s3cr3t"))
                content = client.storage.fetch_presigned(
                    PresignedObject(url=store.url + "/o", method="GET")
                )
                self.assertEqual(content, b"content")

            seen = store.requests[0]
            self.assertNotIn("X-Access-Key-Id", seen.headers)
            self.assertNotIn("Authorization", seen.headers)

    def test_failure_does_not_leak_the_signature(self) -> None:
        with Stub(lambda _r: (403, "expired")) as store, Stub(routes({})) as api:
            client = client_for(api)
            url = store.url + "/o?X-Amz-Signature=deadbeefsecret"
            with self.assertRaises(NemauApiError) as caught:
                client.storage.fetch_presigned(PresignedObject(url=url, method="GET"))
            self.assertNotIn("deadbeefsecret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
