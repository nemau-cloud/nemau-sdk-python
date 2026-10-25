"""Bucket-scoped S3 access keys."""

from __future__ import annotations

import dataclasses
import datetime
import logging
import unittest

from nemau import (
    CREDENTIAL_READ_ONLY,
    CREDENTIAL_READ_WRITE,
    IssuedCredential,
    NemauApiError,
    is_credential_limit,
    is_organization_read_only,
)
from tests.helpers import Stub, client_for

FAKE_SECRET = "EXAMPLE-SECRET-NOT-REAL-0123456789"

ISSUED = {
    "access_key_id": "AKEXAMPLE",
    "secret_access_key": FAKE_SECRET,
    "access": "read_only",
    "container": "photos",
    "s3_bucket": "nemau-dev-00000000000000000000000000000000",
    "region": "fr-occitanie-1",
    "endpoint": "",
    "created_at": "2026-10-04T16:06:25Z",
    "expires_at": None,
}


class CreateCredentialTests(unittest.TestCase):
    def test_posts_the_access_and_omits_an_unset_expiry(self) -> None:
        with Stub(lambda request: (201, ISSUED)) as stub:
            got = client_for(stub).storage.create_credential("photos", CREDENTIAL_READ_ONLY)
            seen = stub.requests[0]
            self.assertEqual(seen.method, "POST")
            self.assertEqual(seen.path, "/api/v1/storage/containers/photos/credentials")
            self.assertEqual(seen.json(), {"access": "read_only"})
        self.assertEqual(got.secret_access_key, FAKE_SECRET)
        self.assertEqual(got.region, "fr-occitanie-1")
        self.assertEqual(got.endpoint, "")
        self.assertIsNone(got.expires_at)
        # The bucket to address is the store's name, not the container's.
        self.assertEqual(got.container, "photos")
        self.assertEqual(got.s3_bucket, "nemau-dev-00000000000000000000000000000000")
        self.assertIn("nemau-dev-00000000000000000000000000000000", repr(got))
        self.assertNotIn(FAKE_SECRET, repr(got))

    def test_an_older_server_without_s3_bucket_decodes_to_empty(self) -> None:
        body = {k: v for k, v in ISSUED.items() if k != "s3_bucket"}
        with Stub(lambda request: (201, body)) as stub:
            got = client_for(stub).storage.create_credential("photos", CREDENTIAL_READ_ONLY)
        self.assertEqual(got.s3_bucket, "")

    def test_get_container_carries_the_s3_bucket(self) -> None:
        listing = {
            "containers": [
                {
                    "id": "c-1",
                    "name": "photos",
                    "bytes": 0,
                    "count": 0,
                    "s3_bucket": "nemau-dev-11111111111111111111111111111111",
                }
            ],
            "count": 1,
        }
        with Stub(lambda request: (200, listing)) as stub:
            got = client_for(stub).storage.get_container("photos")
        self.assertEqual(got.name, "photos")
        self.assertEqual(got.s3_bucket, "nemau-dev-11111111111111111111111111111111")

    def test_sends_the_expiry_as_rfc3339_utc(self) -> None:
        paris = datetime.timezone(datetime.timedelta(hours=2))
        body = dict(ISSUED, expires_at="2026-10-05T12:00:00Z")
        with Stub(lambda request: (201, body)) as stub:
            got = client_for(stub).storage.create_credential(
                "photos",
                CREDENTIAL_READ_WRITE,
                expires_at=datetime.datetime(2026, 10, 5, 14, 0, 0, 789, tzinfo=paris),
            )
            self.assertEqual(
                stub.requests[0].json(),
                {"access": "read_write", "expires_at": "2026-10-05T12:00:00Z"},
            )
        self.assertEqual(got.expires_at, "2026-10-05T12:00:00Z")

    def test_invalid_input_is_refused_before_any_call(self) -> None:
        with Stub(lambda request: (201, ISSUED)) as stub:
            storage = client_for(stub).storage
            with self.assertRaises(ValueError):
                storage.create_credential("", CREDENTIAL_READ_ONLY)
            with self.assertRaises(ValueError):
                storage.create_credential("photos", "admin")
            with self.assertRaises(ValueError):
                storage.create_credential(
                    "photos", CREDENTIAL_READ_ONLY, expires_at=datetime.datetime(2030, 1, 1)
                )
            self.assertEqual(stub.requests, [])


class ListAndRevokeTests(unittest.TestCase):
    def test_list_and_revoke_use_the_containers_own_paths(self) -> None:
        listing = {
            "credentials": [
                {
                    "access_key_id": "AK1",
                    "access": "read_write",
                    "container": "photos",
                    "created_at": "2026-10-04T16:06:25Z",
                    "expires_at": None,
                },
                {
                    "access_key_id": "AK2",
                    "access": "read_only",
                    "container": "photos",
                    "created_at": "2026-10-04T16:07:25Z",
                    "expires_at": "2026-10-05T12:00:00Z",
                },
            ]
        }

        def handler(request):
            return (204, "") if request.method == "DELETE" else (200, listing)

        with Stub(handler) as stub:
            storage = client_for(stub).storage
            keys = storage.list_credentials("photos")
            self.assertEqual(
                (stub.requests[0].method, stub.requests[0].path),
                ("GET", "/api/v1/storage/containers/photos/credentials"),
            )
            self.assertEqual([k.access_key_id for k in keys], ["AK1", "AK2"])
            self.assertIsNone(keys[0].expires_at)
            self.assertEqual(keys[1].expires_at, "2026-10-05T12:00:00Z")
            self.assertNotIn("secret", repr(keys))
            self.assertFalse(
                any(f.name == "secret_access_key" for f in dataclasses.fields(keys[0]))
            )

            storage.revoke_credential("photos", "AKEXAMPLE1")
            self.assertEqual(
                (stub.requests[1].method, stub.requests[1].path),
                ("DELETE", "/api/v1/storage/containers/photos/credentials/AKEXAMPLE1"),
            )


class ErrorCodeTests(unittest.TestCase):
    def test_a_refusal_is_classified_by_its_stable_code(self) -> None:
        cases = [
            (
                429,
                {
                    "error": "this container already has the maximum number of access keys",
                    "code": "credential_limit",
                },
                True,
                False,
            ),
            (
                403,
                {"error": "the organization is read-only", "code": "organization_read_only"},
                False,
                True,
            ),
            (429, {"error": "rate limit exceeded"}, False, False),
            (403, {"error": "forbidden", "reason": "permission_denied"}, False, False),
        ]
        for status, body, limit, read_only in cases:
            with self.subTest(body=body), Stub(lambda request, s=status, b=body: (s, b)) as stub:
                with self.assertRaises(NemauApiError) as caught:
                    client_for(stub, retry=None).storage.create_credential(
                        "photos", CREDENTIAL_READ_WRITE
                    )
                self.assertEqual(is_credential_limit(caught.exception), limit)
                self.assertEqual(is_organization_read_only(caught.exception), read_only)
        self.assertFalse(is_credential_limit(RuntimeError("plain")))


class LimitAndPathEncodingTests(unittest.TestCase):
    def test_a_credential_limit_carries_its_cap_when_the_server_says_it(self) -> None:
        cases = [
            (
                {"error": "limit", "code": "credential_limit", "limit": 10, "scope": "bucket"},
                10,
                "bucket",
            ),
            (
                {"error": "limit", "code": "credential_limit", "limit": 50, "scope": "project"},
                50,
                "project",
            ),
            ({"error": "limit", "code": "credential_limit"}, 0, ""),
            ({"error": "limit", "code": "credential_limit", "limit": "ten", "scope": 3}, 0, ""),
        ]
        for body, limit, scope in cases:
            with self.subTest(body=body), Stub(lambda request, b=body: (429, b)) as stub:
                with self.assertRaises(NemauApiError) as caught:
                    client_for(stub, retry=None).storage.create_credential(
                        "photos", CREDENTIAL_READ_WRITE
                    )
                self.assertTrue(is_credential_limit(caught.exception))
                self.assertEqual(caught.exception.limit, limit)
                self.assertEqual(caught.exception.scope, scope)

    def test_a_name_with_special_characters_reaches_the_server_encoded_once(self) -> None:
        from urllib.parse import quote

        for name in ["with space", "100%", "a/b", "a+b", "café", "a?b#c", "%2F"]:
            with self.subTest(name=name), Stub(lambda request: (200, {"credentials": []})) as stub:
                client_for(stub).storage.list_credentials(name)
                self.assertEqual(
                    stub.requests[0].path,
                    f"/api/v1/storage/containers/{quote(name, safe='')}/credentials",
                )


class SecretRedactionTests(unittest.TestCase):
    def test_the_secret_never_shows_up_in_repr_str_or_logs(self) -> None:
        cred = IssuedCredential.from_dict(ISSUED)
        for text in (
            repr(cred),
            str(cred),
            f"{cred}",
            # %-formatting goes through str(); the logging call below covers it.
            repr([cred]),
            repr({"k": cred}),
        ):
            self.assertNotIn(FAKE_SECRET, text)
            self.assertIn("AKEXAMPLE", text)
        with self.assertLogs("nemau.test", level="INFO") as logs:
            logging.getLogger("nemau.test").info("issued %s", cred)
        self.assertNotIn(FAKE_SECRET, "\n".join(logs.output))
        # The caller can still read it explicitly.
        self.assertEqual(cred.secret_access_key, FAKE_SECRET)


if __name__ == "__main__":
    unittest.main()
