"""Asynchronous writes: polling, failure, resource id."""

from __future__ import annotations

import unittest
from typing import Any

from nemau import NemauTaskFailedError
from tests.helpers import RecordedRequest, Stub, client_for


def task_stub(
    *,
    until_nth: int,
    terminal: str,
    resource_id: str | None = None,
    result: dict[str, Any] | None = None,
):  # type: ignore[no-untyped-def]
    """Serves a write, then a task that turns terminal on the nth poll."""
    polls = {"n": 0}

    def handler(request: RecordedRequest) -> tuple[int, Any]:
        if not request.path.startswith("/api/v1/tasks/"):
            return 202, {"task_id": "task-1", "status": "pending"}
        polls["n"] += 1
        if polls["n"] < until_nth:
            return 200, {
                "id": "task-1",
                "taskType": "network:create",
                "status": "running",
                "progress": 40,
            }
        payload: dict[str, Any] = {
            "id": "task-1",
            "taskType": "network:create",
            "status": terminal,
            "progress": 100,
        }
        if resource_id:
            payload["resourceId"] = resource_id
            payload["resourceType"] = "network"
        if result:
            payload["result"] = result
        if terminal == "failed":
            payload["error"] = "the operation failed; contact support with the task id"
        return 200, payload

    return handler


class WaitTest(unittest.TestCase):
    def test_polls_until_terminal(self) -> None:
        with Stub(task_stub(until_nth=3, terminal="completed", resource_id="net-42")) as stub:
            client = client_for(stub)
            ref = client.network.create_network("prod")
            task = client.wait_for(ref)
            self.assertTrue(task.succeeded)
            self.assertEqual(task.resource_id, "net-42")
            # One write plus three polls.
            self.assertEqual(len(stub.requests), 4)

    def test_failed_task_raises_with_the_task_attached(self) -> None:
        with Stub(task_stub(until_nth=1, terminal="failed")) as stub:
            client = client_for(stub)
            ref = client.network.create_network("prod")
            with self.assertRaises(NemauTaskFailedError) as caught:
                client.wait_for(ref)
            # The platform replaces the error details with a generic message,
            # so the task id must appear in the exception message.
            self.assertEqual(caught.exception.task.id, "task-1")
            self.assertIn("task-1", str(caught.exception))

    def test_resource_id_wins_over_result(self) -> None:
        handler = task_stub(
            until_nth=1,
            terminal="completed",
            resource_id="net-authoritative",
            result={"network_id": "net-from-result"},
        )
        with Stub(handler) as stub:
            client = client_for(stub)
            ref = client.network.create_network("prod")
            identifier, _ = client.wait_for_resource_id(ref, "network_id")
            self.assertEqual(identifier, "net-authoritative")

    def test_result_is_the_fallback(self) -> None:
        handler = task_stub(until_nth=1, terminal="completed", result={"volume_id": "vol-9"})
        with Stub(handler) as stub:
            client = client_for(stub)
            ref = client.network.create_network("prod")
            identifier, _ = client.wait_for_resource_id(ref, "volume_id")
            self.assertEqual(identifier, "vol-9")

    def test_early_404_means_not_yet(self) -> None:
        # The task may not be readable yet right after the 202; failing on
        # that 404 would make every create flaky.
        polls = {"n": 0}

        def handler(request: RecordedRequest) -> tuple[int, Any]:
            if not request.path.startswith("/api/v1/tasks/"):
                return 202, {"task_id": "t", "status": "pending"}
            polls["n"] += 1
            if polls["n"] < 3:
                return 404, {"error": "task not found"}
            return 200, {"id": "t", "status": "completed", "resourceId": "net-1"}

        with Stub(handler) as stub:
            client = client_for(stub)
            ref = client.network.create_network("prod")
            self.assertEqual(client.wait_for(ref).resource_id, "net-1")

    def test_deadline_is_honoured(self) -> None:
        import time

        with Stub(task_stub(until_nth=10_000, terminal="completed")) as stub:
            client = client_for(stub)
            ref = client.network.create_network("prod")
            with self.assertRaises(TimeoutError):
                client.wait_for(ref, deadline=time.monotonic() + 0.05)

    def test_resource_tasks_pass_resource_id(self) -> None:
        with Stub(lambda _r: (200, {"tasks": [{"id": "t1", "status": "completed"}]})) as stub:
            tasks = client_for(stub).tasks.list_for_resource("inst-1")
            self.assertEqual(len(tasks), 1)
            self.assertEqual(stub.requests[0].query, "resource_id=inst-1")


if __name__ == "__main__":
    unittest.main()


class ResourceInUseTest(unittest.TestCase):
    """A refused delete is recognised by its code, not by its message."""

    MESSAGE = "detach volume(s) data before deleting the instance"

    def _failure(self, **extra):  # type: ignore[no-untyped-def]
        from nemau import Task

        return NemauTaskFailedError(
            Task.from_dict(
                {
                    "id": "t1",
                    "taskType": "compute:delete",
                    "status": "failed",
                    "error": self.MESSAGE,
                    **extra,
                }
            )
        )

    def test_recognises_the_code(self) -> None:
        from nemau import is_resource_in_use

        failure = self._failure(errorCode="resource_in_use")
        self.assertTrue(is_resource_in_use(failure))
        self.assertIn("detach volume(s) data", str(failure))

    def test_ignores_everything_else(self) -> None:
        from nemau import is_resource_in_use

        self.assertFalse(is_resource_in_use(self._failure()))
        self.assertFalse(is_resource_in_use(self._failure(errorCode="something_else")))
        self.assertFalse(is_resource_in_use(Exception(self.MESSAGE)))
