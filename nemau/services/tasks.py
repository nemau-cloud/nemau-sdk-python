"""/tasks: the status of queued operations."""

from __future__ import annotations

import urllib.parse

from nemau.client import _unwrap
from nemau.models import Task
from nemau.services import Service


class TasksService(Service):
    """Reads one task, or the tasks of one resource.

    The API has no organization-wide task list and no cancellation.
    """

    def get(self, task_id: str) -> Task:
        payload = self._client.request("GET", f"/tasks/{urllib.parse.quote(task_id, safe='')}")
        return Task.from_dict(payload or {})

    def list_for_resource(self, resource_id: str) -> list[Task]:
        """Operations recorded for one resource. ``resource_id`` is required."""
        payload = self._client.request("GET", "/tasks", query={"resource_id": resource_id})
        return [Task.from_dict(item) for item in _unwrap(payload, "tasks")]
