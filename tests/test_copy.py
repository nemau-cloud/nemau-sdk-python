"""Server-side copy: the bytes stay in the store."""

from __future__ import annotations

import json
import unittest

from nemau import NemauApiError
from tests.helpers import Stub, client_for, routes

COPIED = {
    "container": "archive",
    "key": "reports/copy.csv",
    "size": 1234,
    "etag": "9b2cf535f27731c974343645a3985328",
    "last_modified": "2026-09-23T10:00:00Z",
}


class CopyObjectTest(unittest.TestCase):
    def test_source_container_in_the_path_and_key_in_the_body(self) -> None:
        # The key travels in the body: it can contain slashes, which in the
        # path would open extra segments and stop the route matching.
        with Stub(lambda _r: (201, COPIED)) as stub:
            client_for(stub).storage.copy_object(
                "a/b",
                "reports/2026.csv",
                destination_container="archive",
                destination_key="reports/copy.csv",
                replace_metadata=True,
                content_type="text/csv",
                metadata={"owner": "finance"},
            )

            seen = stub.requests[0]
            self.assertEqual(seen.method, "POST")
            self.assertEqual(seen.path, "/api/v1/storage/containers/a%2Fb/copy")
            self.assertEqual(seen.query, "")
            self.assertEqual(
                json.loads(seen.body),
                {
                    "key": "reports/2026.csv",
                    "destination_container": "archive",
                    "destination_key": "reports/copy.csv",
                    "replace_metadata": True,
                    "content_type": "text/csv",
                    "metadata": {"owner": "finance"},
                },
            )

    def test_options_left_empty_are_not_sent(self) -> None:
        # Every default is the server's, so an empty option is left out rather
        # than sent as "".
        with Stub(routes({"POST /api/v1/storage/containers/photos/copy": (201, COPIED)})) as stub:
            storage = client_for(stub).storage
            storage.copy_object("photos", "a/b.jpg")
            storage.copy_object(
                "photos",
                "a/b.jpg",
                destination_container="",
                destination_key="",
                replace_metadata=False,
                content_type="",
                metadata={},
            )
            for seen in stub.requests:
                self.assertEqual(json.loads(seen.body), {"key": "a/b.jpg"})

    def test_decodes_with_and_without_retention(self) -> None:
        locked = {
            **COPIED,
            "version_id": "3HL4kqtJlcpXroDTDmJ+rmSpXd3dIbrHY",
            "retention": {"mode": "COMPLIANCE", "retain_until": "2026-10-23T10:00:01Z"},
        }
        answers = [COPIED, locked]
        with Stub(lambda _r: (201, answers.pop(0))) as stub:
            storage = client_for(stub).storage

            plain = storage.copy_object("reports", "2026.csv")
            self.assertEqual(plain.container, "archive")
            self.assertEqual(plain.key, "reports/copy.csv")
            self.assertEqual(plain.size, 1234)
            self.assertEqual(plain.etag, COPIED["etag"])
            self.assertEqual(plain.last_modified, "2026-09-23T10:00:00Z")
            self.assertEqual(plain.version_id, "")
            self.assertIsNone(plain.retention)

            worm = storage.copy_object("reports", "2026.csv", destination_container="archive")
            self.assertEqual(worm.version_id, locked["version_id"])
            assert worm.retention is not None
            self.assertEqual(worm.retention.mode, "COMPLIANCE")
            self.assertEqual(worm.retention.retain_until, "2026-10-23T10:00:01Z")

    def test_metadata_without_replace_is_refused_before_any_call(self) -> None:
        # Refused client-side so the caller learns the rule from the message
        # rather than from a 400 after a round trip.
        with Stub(routes({})) as stub:
            storage = client_for(stub).storage
            with self.assertRaisesRegex(ValueError, "require replace_metadata"):
                storage.copy_object("reports", "2026.csv", metadata={"owner": "finance"})
            with self.assertRaisesRegex(ValueError, "require replace_metadata"):
                storage.copy_object("reports", "2026.csv", content_type="text/csv")
            self.assertEqual(stub.requests, [])

    def test_source_over_the_copy_limit_surfaces_as_an_api_error(self) -> None:
        payload = {"error": "object exceeds the 5 GiB server-side copy limit"}
        with Stub(lambda _r: (413, payload)) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError) as caught:
                client.storage.copy_object("backups", "disk.img", destination_container="archive")
            self.assertEqual(caught.exception.status_code, 413)
            self.assertIn("5 GiB server-side copy limit", str(caught.exception))
            # A 413 is not retryable: one request, not max_attempts.
            self.assertEqual(len(stub.requests), 1)


if __name__ == "__main__":
    unittest.main()
