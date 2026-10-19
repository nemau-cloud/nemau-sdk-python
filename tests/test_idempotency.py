"""Idempotency keys, replay, and the bounded retries on 429 and 503."""

from __future__ import annotations

import unittest
from unittest import mock

from nemau import (
    NemauApiError,
    is_idempotency_key_mismatch,
    is_rate_limited,
)
from tests.helpers import Stub, client_for

KEY = "Idempotency-Key"


def _flaky(failures: int, status: int, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    state = {"n": 0}

    def handler(_request):  # type: ignore[no-untyped-def]
        state["n"] += 1
        if state["n"] <= failures:
            return status, {"error": "refused"}, headers or {}
        return 202, {"task_id": "task-1"}

    return handler


class KeyTest(unittest.TestCase):
    def test_every_queued_write_carries_a_fresh_key(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            client.network.create_network("a")
            client.network.create_network("b")
            client.network.delete_network("net-1")
        keys = [r.headers[KEY] for r in stub.requests]
        self.assertEqual(len(set(keys)), 3)
        for key in keys:
            self.assertRegex(key, r"^[A-Za-z0-9._:-]{1,128}$")

    def test_reads_carry_no_key(self) -> None:
        with Stub(lambda _request: (200, {"networks": []})) as stub:
            client_for(stub).network.list_networks()
        self.assertNotIn(KEY, stub.requests[0].headers)

    def test_a_pinned_key_overrides_the_random_one(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            with client.idempotency_key("nightly-2026-10-10:net"):
                client.network.create_network("a")
            client.network.create_network("b")
        self.assertEqual(stub.requests[0].headers[KEY], "nightly-2026-10-10:net")
        self.assertNotEqual(stub.requests[1].headers[KEY], "nightly-2026-10-10:net")

    def test_an_explicit_key_on_submit_wins(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            with client.idempotency_key("outer"):
                client.submit("POST", "/network/networks", json={}, idempotency_key="inner")
        self.assertEqual(stub.requests[0].headers[KEY], "inner")

    def test_a_malformed_key_is_refused_before_any_request(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            for bad in ("", "a b", "x" * 129, "k\n", "clé", "a/b"):
                with self.assertRaises(ValueError, msg=repr(bad)), client.idempotency_key(bad):
                    pass
                with self.assertRaises(ValueError, msg=repr(bad)):
                    client.submit("POST", "/network/networks", idempotency_key=bad)
        self.assertEqual(stub.requests, [])

    def test_the_pinned_key_is_released_after_the_block(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            client = client_for(stub)
            with self.assertRaises(RuntimeError), client.idempotency_key("k1"):
                raise RuntimeError("boom")
            client.network.create_network("a")
        self.assertNotEqual(stub.requests[0].headers[KEY], "k1")


class ReplayTest(unittest.TestCase):
    def test_replay_header_is_exposed(self) -> None:
        def handler(_request):  # type: ignore[no-untyped-def]
            return 202, {"task_id": "orig"}, {"Idempotent-Replayed": "true"}

        with Stub(handler) as stub:
            ref = client_for(stub).network.create_network("a")
        self.assertEqual(ref.task_id, "orig")
        self.assertTrue(ref.replayed)

    def test_a_first_run_is_not_a_replay(self) -> None:
        with Stub(lambda _request: (202, {"task_id": "t"})) as stub:
            self.assertFalse(client_for(stub).network.create_network("a").replayed)

    def test_422_mismatch_is_recognised_and_not_retried(self) -> None:
        with Stub(lambda _request: (422, {"error": "idempotency_key_mismatch"})) as stub:
            with self.assertRaises(NemauApiError) as caught:
                client_for(stub).network.create_network("a")
            self.assertEqual(len(stub.requests), 1)
        self.assertTrue(is_idempotency_key_mismatch(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_other_422_is_not_a_mismatch(self) -> None:
        other = NemauApiError(422, "unprocessable", method="POST", path="/x")
        self.assertFalse(is_idempotency_key_mismatch(other))
        self.assertFalse(is_idempotency_key_mismatch(ValueError("idempotency_key_mismatch")))


class RetryTest(unittest.TestCase):
    def test_a_keyed_write_is_retried_on_429_with_the_same_key(self) -> None:
        with Stub(_flaky(2, 429, {"Retry-After": "0"})) as stub:
            ref = client_for(stub).network.create_network("a")
        self.assertEqual(ref.task_id, "task-1")
        self.assertEqual(len(stub.requests), 3)
        self.assertEqual(len({r.headers[KEY] for r in stub.requests}), 1)

    def test_a_keyed_write_is_retried_on_503(self) -> None:
        with Stub(_flaky(1, 503, {"Retry-After": "0"})) as stub:
            client_for(stub).network.create_network("a")
        self.assertEqual(len(stub.requests), 2)

    def test_retries_are_bounded(self) -> None:
        with (
            Stub(_flaky(100, 429, {"Retry-After": "0"})) as stub,
            self.assertRaises(NemauApiError) as caught,
        ):
            client_for(stub).network.create_network("a")
        self.assertEqual(len(stub.requests), 3)  # client_for allows 3 attempts
        self.assertTrue(is_rate_limited(caught.exception))
        self.assertEqual(caught.exception.retry_after, 0.0)

    def test_retry_after_is_honoured(self) -> None:
        with (
            Stub(_flaky(1, 429, {"Retry-After": "2"})) as stub,
            mock.patch("nemau.client.time.sleep") as sleep,
        ):
            client_for(stub).network.create_network("a")
        self.assertGreaterEqual(sleep.call_args_list[0].args[0], 2.0)

    def test_retry_after_beyond_the_cap_surfaces_at_once(self) -> None:
        # Waiting 60 s inside a call is for the caller to decide.
        with (
            Stub(_flaky(5, 429, {"Retry-After": "60"})) as stub,
            mock.patch("nemau.client.time.sleep") as sleep,
            self.assertRaises(NemauApiError) as caught,
        ):
            client_for(stub).network.create_network("a")
        self.assertEqual(len(stub.requests), 1)
        sleep.assert_not_called()
        self.assertEqual(caught.exception.retry_after, 60.0)

    def test_an_unparseable_retry_after_falls_back_to_backoff(self) -> None:
        with Stub(_flaky(1, 429, {"Retry-After": "soon"})) as stub:
            client_for(stub).network.create_network("a")
        self.assertEqual(len(stub.requests), 2)

    def test_the_error_message_stays_generic(self) -> None:
        body = {"error": "rate limit exceeded", "code": "rate_limited"}
        with (
            Stub(lambda _request: (429, body, {"Retry-After": "60"})) as stub,
            self.assertRaises(NemauApiError) as caught,
        ):
            client_for(stub).network.create_network("a")
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertIn("rate limit exceeded", str(caught.exception))

    def test_a_write_without_a_key_is_never_replayed(self) -> None:
        # These writes are not queued and carry no key: a retry could apply
        # them twice.
        with Stub(lambda _request: (503, {"error": "unavailable"}, {"Retry-After": "0"})) as stub:
            client = client_for(stub)
            with self.assertRaises(NemauApiError):
                client.request("POST", "/kubernetes/clusters/prod/preflight", json={})
            with self.assertRaises(NemauApiError):
                client.request("PUT", "/network/networks", json={})
        self.assertEqual(len(stub.requests), 2)

    def test_reads_are_still_retried(self) -> None:
        state = {"n": 0}

        def handler(_request):  # type: ignore[no-untyped-def]
            state["n"] += 1
            return (503, {"error": "x"}) if state["n"] == 1 else (200, {"networks": []})

        with Stub(handler) as stub:
            client_for(stub).network.list_networks()
        self.assertEqual(len(stub.requests), 2)


if __name__ == "__main__":
    unittest.main()
