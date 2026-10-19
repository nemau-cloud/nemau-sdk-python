"""Object listing cursor, metrics periods, IAM reads."""

from __future__ import annotations

import unittest

from nemau import NemauApiError, SessionToken, is_access_key_limit
from tests.helpers import RecordedRequest, Stub, client_for

OBJECTS = "/api/v1/storage/containers/backups/objects"


def _paged(pages: dict[str, tuple[list[str], str]]):  # type: ignore[no-untyped-def]
    def handler(request: RecordedRequest):  # type: ignore[no-untyped-def]
        marker = dict(p.split("=", 1) for p in request.query.split("&") if p).get("marker", "")
        names, nxt = pages[marker]
        return 200, {
            "objects": [{"name": n, "size": 1} for n in names],
            "next_marker": nxt,
        }

    return handler


class ObjectListingTest(unittest.TestCase):
    def test_iter_objects_follows_the_cursor_to_the_end(self) -> None:
        pages = {"": (["a", "b"], "Y3Vy"), "Y3Vy": (["c"], "ZGly"), "ZGly": (["d"], "")}
        with Stub(_paged(pages)) as stub:
            names = [o.name for o in client_for(stub).storage.iter_objects("backups", page_size=2)]
        self.assertEqual(names, ["a", "b", "c", "d"])
        self.assertEqual([r.path for r in stub.requests], [OBJECTS] * 3)
        self.assertEqual(
            [r.query for r in stub.requests],
            ["limit=2", "marker=Y3Vy&limit=2", "marker=ZGly&limit=2"],
        )

    def test_the_cursor_is_echoed_verbatim(self) -> None:
        cursor = "a2V5L3dpdGgvc2xhc2hlcys9PQ"
        pages = {"": (["a"], cursor), cursor: ([], "")}
        with Stub(_paged(pages)) as stub:
            list(client_for(stub).storage.iter_objects("backups"))
        self.assertIn(f"marker={cursor}", stub.requests[1].query)

    def test_a_cursor_that_does_not_advance_stops_the_walk(self) -> None:
        pages = {"": (["a"], "same"), "same": (["b"], "same")}
        with Stub(_paged(pages)) as stub, self.assertRaises(NemauApiError):
            list(client_for(stub).storage.iter_objects("backups"))
        self.assertEqual(len(stub.requests), 2)

    def test_the_default_page_size_is_left_to_the_server(self) -> None:
        with Stub(_paged({"": ([], "")})) as stub:
            list(client_for(stub).storage.iter_objects("backups"))
        self.assertEqual(stub.requests[0].query, "")

    def test_prefix_is_sent_and_escaped(self) -> None:
        with Stub(_paged({"": ([], "")})) as stub:
            list(client_for(stub).storage.iter_objects("backups", prefix="logs/a b"))
        self.assertEqual(stub.requests[0].query, "prefix=logs%2Fa+b")

    def test_limit_outside_1_to_1000_is_refused_before_the_request(self) -> None:
        with Stub(_paged({"": ([], "")})) as stub:
            storage = client_for(stub).storage
            for bad in (-1, 1001):
                with self.assertRaises(ValueError):
                    storage.list_objects("backups", limit=bad)
                with self.assertRaises(ValueError):
                    list(storage.iter_objects("backups", page_size=bad))
            storage.list_objects("backups", limit=1000)
        self.assertEqual(len(stub.requests), 1)


class MetricsTest(unittest.TestCase):
    def test_known_periods_pass(self) -> None:
        with Stub(lambda _request: (200, {"metrics": []})) as stub:
            compute = client_for(stub).compute
            for period in ("1h", "6h", "24h", "7d"):
                compute.instance_metrics("i-1", period)
            compute.instance_metrics("i-1")
        self.assertEqual(
            [r.query for r in stub.requests[:4]], [f"period={p}" for p in ("1h", "6h", "24h", "7d")]
        )
        self.assertEqual(stub.requests[4].query, "")

    def test_other_periods_are_refused_before_the_request(self) -> None:
        with Stub(lambda _request: (200, {})) as stub:
            compute = client_for(stub).compute
            for bad in ("30d", "1H", "1h&x=1", "1h ", "../x"):
                with self.assertRaises(ValueError, msg=bad):
                    compute.instance_metrics("i-1", bad)
        self.assertEqual(stub.requests, [])


class IamTest(unittest.TestCase):
    def test_list_users_uses_the_current_route_and_decodes_the_page(self) -> None:
        body = {
            "data": [{"id": "u1", "username": "ada", "email": "ada@example.com", "type": "owner"}],
            "total": 1,
            "page": 1,
            "limit": 20,
            "total_pages": 1,
            "has_more": False,
        }
        with Stub(lambda _request: (200, body)) as stub:
            page = client_for(stub, credential=SessionToken("s")).iam.list_users()
        self.assertEqual(stub.requests[0].path, "/api/v1/iam/users")
        self.assertEqual(stub.requests[0].query, "page=1&limit=20")
        self.assertEqual(stub.requests[0].headers["X-Session-Token"], "s")
        self.assertEqual(page.data[0].username, "ada")
        self.assertEqual(page.total_pages, 1)
        self.assertFalse(page.has_more)

    def test_list_users_bounds_are_checked_client_side(self) -> None:
        with Stub(lambda _request: (200, {})) as stub:
            iam = client_for(stub).iam
            for page, limit in ((0, 20), (1, 0), (1, 101)):
                with self.assertRaises(ValueError):
                    iam.list_users(page=page, limit=limit)
        self.assertEqual(stub.requests, [])

    def test_access_key_limits_and_expiry_decode(self) -> None:
        responses = {
            "/api/v1/iam/access-keys/limits": {"maxAccessKeys": 5, "current": 2},
            "/api/v1/iam/users/u%2F1/access-keys": {
                "accessKeys": [
                    {
                        "id": "k1",
                        "accessKeyId": "AKIAEXAMPLE",
                        "status": "active",
                        "expiresAt": "2027-01-01T00:00:00Z",
                    }
                ],
                "total": 1,
            },
        }
        with Stub(lambda request: (200, responses[request.path])) as stub:
            iam = client_for(stub).iam
            limits = iam.access_key_limits()
            iam.access_key_limits("u1")
            keys = iam.list_access_keys("u/1")
        self.assertEqual((limits.max_access_keys, limits.current), (5, 2))
        self.assertEqual(stub.requests[0].query, "")
        self.assertEqual(stub.requests[1].query, "userId=u1")
        self.assertEqual(keys[0].expires_at, "2027-01-01T00:00:00Z")
        self.assertEqual(keys[0].access_key_id, "AKIAEXAMPLE")

    def test_without_a_session_the_api_refuses(self) -> None:
        with (
            Stub(lambda _request: (401, {"error": "Missing session token"})) as stub,
            self.assertRaises(NemauApiError) as caught,
        ):
            client_for(stub).iam.list_users()
        self.assertTrue(caught.exception.unauthorized)

    def test_409_access_key_limit_is_recognised_and_not_retried(self) -> None:
        body = {
            "error": "maximum number of active access keys (5) reached",
            "reason": "access_key_limit",
        }
        with Stub(lambda _request: (409, body)) as stub, self.assertRaises(NemauApiError) as caught:
            client_for(stub).iam.access_key_limits()
        self.assertTrue(is_access_key_limit(caught.exception))
        self.assertEqual(len(stub.requests), 1)
        self.assertFalse(is_access_key_limit(NemauApiError(409, "x", method="POST", path="/x")))
        self.assertFalse(
            is_access_key_limit(
                NemauApiError(400, "x", method="POST", path="/x", reason="access_key_limit")
            )
        )


if __name__ == "__main__":
    unittest.main()
