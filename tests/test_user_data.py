"""``user_data`` at instance creation, and the digest an instance reports."""

from __future__ import annotations

import hashlib
import unittest
from typing import Any

from nemau.models import Instance
from nemau.services.compute import USER_DATA_MAX_BYTES
from tests.helpers import Stub, client_for

ACCEPTED = (202, {"task_id": "task-1", "status": "pending"})
BASE: dict[str, Any] = {
    "name": "web",
    "image_id": "ubuntu",
    "vcpus": 1,
    "ram_mb": 512,
    "disk_gb": 10,
}


class CreateBodyTest(unittest.TestCase):
    def test_body_carries_user_data_only_when_set(self) -> None:
        with Stub(lambda _request: ACCEPTED) as stub:
            client = client_for(stub)
            client.compute.create_instance(**BASE)
            client.compute.create_instance(**BASE, user_data="#cloud-config\nruncmd: [echo hi]\n")
            client.compute.create_instance(**BASE, user_data="")
            without, with_data, empty = (request.json() for request in stub.requests)
            self.assertNotIn("user_data", without)
            self.assertEqual(with_data["user_data"], "#cloud-config\nruncmd: [echo hi]\n")
            self.assertEqual(empty["user_data"], "")

    def test_accepted_up_to_16_kib_in_utf8_bytes(self) -> None:
        self.assertEqual(USER_DATA_MAX_BYTES, 16384)
        with Stub(lambda _request: ACCEPTED) as stub:
            client = client_for(stub)
            client.compute.create_instance(**BASE, user_data="a" * 16384)
            # "é" is 2 bytes: 8192 of them fill the limit exactly.
            client.compute.create_instance(**BASE, user_data="é" * 8192)
            self.assertEqual(len(stub.requests), 2)

    def test_oversized_user_data_is_refused_before_any_request(self) -> None:
        with Stub(lambda _request: ACCEPTED) as stub:
            client = client_for(stub)
            # 9000 characters but 18000 bytes: a character count would pass.
            for user_data in ("a" * 16385, "é" * 9000):
                with self.assertRaisesRegex(
                    ValueError, r"user_data is \d+ bytes; the limit is 16384"
                ):
                    client.compute.create_instance(**BASE, user_data=user_data)
            self.assertEqual(len(stub.requests), 0)

    def test_nul_lone_surrogate_and_non_string_are_refused(self) -> None:
        bad: list[Any] = ["#!/bin/sh\x00echo", "broken \ud800 text", 42, b"bytes", {"text": "x"}]
        with Stub(lambda _request: ACCEPTED) as stub:
            client = client_for(stub)
            for user_data in bad:
                with self.assertRaises(ValueError, msg=repr(user_data)) as caught:
                    client.compute.create_instance(**BASE, user_data=user_data)
                self.assertTrue(str(caught.exception).startswith("nemau: user_data must "))
            self.assertEqual(len(stub.requests), 0)

    def test_none_means_no_user_data(self) -> None:
        with Stub(lambda _request: ACCEPTED) as stub:
            client_for(stub).compute.create_instance(**BASE, user_data=None)
            self.assertNotIn("user_data", stub.requests[0].json())

    def test_error_text_never_repeats_the_document(self) -> None:
        with Stub(lambda _request: ACCEPTED) as stub:
            with self.assertRaises(ValueError) as caught:
                client_for(stub).compute.create_instance(**BASE, user_data="password=s3cr3t\x00")
            self.assertNotIn("s3cr3t", str(caught.exception))


class DigestDecodingTest(unittest.TestCase):
    def test_instance_decodes_user_data_sha256(self) -> None:
        digest = hashlib.sha256(b"#cloud-config\n").hexdigest()
        with_data = {"id": "i-1", "name": "web", "status": "running", "user_data_sha256": digest}
        without = {"id": "i-2", "name": "db", "status": "running"}
        self.assertEqual(Instance.from_dict(with_data).user_data_sha256, digest)
        self.assertEqual(Instance.from_dict(without).user_data_sha256, "")
        self.assertNotIn("user_data", Instance.from_dict(with_data).extra)

    def test_get_and_list_return_the_digest(self) -> None:
        digest = hashlib.sha256(b"#!/bin/sh\n").hexdigest()
        instance = {"id": "i-1", "name": "web", "status": "running", "user_data_sha256": digest}

        def handler(request: Any) -> tuple[Any, ...]:
            if request.path == "/api/v1/compute/instances":
                return 200, {"instances": [instance]}
            return 200, {"instance": instance}

        with Stub(handler) as stub:
            client = client_for(stub)
            self.assertEqual(client.compute.list_instances()[0].user_data_sha256, digest)
            self.assertEqual(client.compute.get_instance("i-1").user_data_sha256, digest)


if __name__ == "__main__":
    unittest.main()
