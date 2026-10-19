"""Client for the Nemau Cloud public API."""

from __future__ import annotations

import contextlib
import contextvars
import email.utils
import json as jsonlib
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator, Mapping
from typing import Any, Union

from nemau.auth import ENV_API_URL, ENV_PROJECT, Credential, credential_from_env
from nemau.errors import NemauApiError, NemauTaskFailedError, api_error_from_body
from nemau.models import Task, TaskRef

#: The production API. Override it with ``NEMAU_API_URL`` or the ``base_url``
#: argument to target another environment.
DEFAULT_BASE_URL = "https://api.nemau-cloud.com"

#: The API version prefix. It is not configurable because every path in this
#: SDK targets v1.
API_PREFIX = "/api/v1"

VERSION = "0.2.0"

#: Names the project a request acts on. Sent only when one is selected;
#: otherwise the credential's own scope applies.
PROJECT_SCOPE_HEADER = "X-Project-Id"

QueryValue = Union[str, int, None]


#: Header that makes a queued write replayable. The server remembers the key
#: for 24 hours: a replay with the same parameters answers 202 with the
#: original task id, the same key with other parameters answers 422.
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"

#: Response header set on a 202 that replays an earlier write.
IDEMPOTENT_REPLAYED_HEADER = "Idempotent-Replayed"

_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

#: Key chosen by :meth:`NemauClient.idempotency_key`, per thread and per task.
_key_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nemau_idempotency_key", default=None
)


def validate_idempotency_key(key: str) -> str:
    """Returns ``key`` when the server would accept it (1 to 128 characters of
    letters, digits, ``.``, ``_``, ``:`` and ``-``), and raises ``ValueError``
    otherwise. The server validates the key as well."""
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key or ""):
        raise ValueError(
            "nemau: an idempotency key is 1 to 128 characters of letters, digits, "
            "'.', '_', ':' or '-'"
        )
    return key


class RetryPolicy:
    """Retry policy for retryable failures.

    Retries ``429``, ``5xx`` and transport errors. Reads are always retried. A
    write (POST, PUT, PATCH, DELETE) is retried only when it carries an
    ``Idempotency-Key``, as every queued write does; without a key, a retry
    could do the work twice.

    On ``429`` and ``503``, the server's ``Retry-After`` sets the minimum delay
    before the next attempt. If it asks for more than ``max_retry_after``
    seconds, the error is raised immediately and the caller decides whether to
    wait.
    """

    __slots__ = ("max_attempts", "base_delay", "max_delay", "max_retry_after")

    def __init__(
        self,
        max_attempts: int = 4,
        base_delay: float = 0.3,
        max_delay: float = 5.0,
        max_retry_after: float = 30.0,
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retry_after = max_retry_after

    def backoff(self, attempt: int) -> float:
        """Delay before attempt ``attempt`` (the first one is 1).

        Uses full jitter, so that many clients retrying at the same time do not
        retry in step.
        """
        base = self.base_delay if self.base_delay > 0 else 0.3
        ceiling = self.max_delay if self.max_delay > 0 else 5.0
        # Spreading retries is not a security use of randomness.
        return random.uniform(0, min(base * (2 ** (attempt - 1)), ceiling))


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, moment.timestamp() - time.time())


#: Verbs that never change state. Anything else is a write.
_READ_ONLY = frozenset({"GET", "HEAD"})

#: Statuses that carry a ``Retry-After``: the server refused before doing
#: anything (rate limit, backend unavailable).
_RETRY_AFTER_STATUSES = frozenset({429, 503})


class NemauClient:
    """Client for the Nemau Cloud public API.

    Writes are asynchronous. A create, a delete or an action answers 202 with a
    :class:`~nemau.models.TaskRef`, and the platform does the work in the
    background. :meth:`wait_for` waits until the task finishes.

    The organization comes from the credential. The project comes from the
    ``project`` argument when it is set, and from the credential otherwise. A
    service account pinned to a project cannot leave it: the API answers 403.

    The client uses only the standard library.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        credential: Credential | None = None,
        retry: RetryPolicy | None = None,
        poll_interval: float = 2.0,
        timeout: float = 60.0,
        user_agent: str | None = None,
        project: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        import os

        source: Mapping[str, str] = env if env is not None else os.environ
        raw = base_url or source.get(ENV_API_URL, "").strip() or DEFAULT_BASE_URL
        parsed = urllib.parse.urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f'nemau: base_url "{raw}" must be absolute (scheme://host)')
        self.base_url = raw.rstrip("/")

        self._credential = credential if credential is not None else credential_from_env(env)
        #: Selected project, empty when the credential's own scope applies.
        self.project = (project if project is not None else source.get(ENV_PROJECT, "")).strip()
        self._retry = retry or RetryPolicy()
        self.poll_interval = poll_interval
        self._timeout = timeout
        self._user_agent_prefix = user_agent
        self._user_agent = (
            f"{user_agent} nemau-sdk-python/{VERSION}"
            if user_agent
            else f"nemau-sdk-python/{VERSION}"
        )

        # Imported late: the services import the client for typing.
        from nemau.services.compute import ComputeService
        from nemau.services.dns import DNSService
        from nemau.services.iam import IamService
        from nemau.services.kubernetes import KubernetesService
        from nemau.services.loadbalancer import LoadBalancerService
        from nemau.services.metering import MeteringService
        from nemau.services.network import NetworkService
        from nemau.services.projects import ProjectsService
        from nemau.services.quotas import QuotasService
        from nemau.services.regions import RegionsService
        from nemau.services.storage import StorageService
        from nemau.services.tasks import TasksService

        self.compute = ComputeService(self)
        self.network = NetworkService(self)
        self.load_balancer = LoadBalancerService(self)
        self.storage = StorageService(self)
        self.kubernetes = KubernetesService(self)
        self.dns = DNSService(self)
        self.metering = MeteringService(self)
        self.regions = RegionsService(self)
        self.tasks = TasksService(self)
        self.projects = ProjectsService(self)
        self.iam = IamService(self)
        self.quotas = QuotasService(self)

    def for_project(self, project: str) -> NemauClient:
        """Returns a client for another project.

        The new client uses the same credential and settings. The original
        client is not modified, so calls already in progress on it keep their
        project.

        A service account pinned to a project cannot leave it: the API answers
        403.
        """
        return NemauClient(
            base_url=self.base_url,
            credential=self._credential,
            retry=self._retry,
            poll_interval=self.poll_interval,
            timeout=self._timeout,
            user_agent=self._user_agent_prefix,
            project=project,
            env={},
        )

    # ----------------------------------------------------------------- #
    # Transport
    # ----------------------------------------------------------------- #

    def describe_credential(self) -> str:
        """Redacted description of the credential, for a ``whoami``."""
        return self._credential.describe() if self._credential else "none"

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        json: Any = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        """Performs a request and decodes the JSON response."""
        status, raw, _ = self._send(
            method, path, query=query, json=json, body=body, content_type=content_type
        )
        if status == 204 or not raw:
            return None
        return jsonlib.loads(raw)

    def request_raw(
        self, method: str, path: str, *, query: Mapping[str, QueryValue] | None = None
    ) -> bytes:
        """Performs a request and returns the raw body (downloads)."""
        _, raw, _ = self._send(method, path, query=query, decode=False)
        return raw

    @contextlib.contextmanager
    def idempotency_key(self, key: str) -> Iterator[str]:
        """Pins the ``Idempotency-Key`` of the writes made inside the block.

        Every queued write already sends a random key, which makes the SDK's own
        retries safe. Set a key yourself to make a whole run safe to repeat:
        after a crash, the same call with the same key and parameters returns
        the original task instead of queueing a new one. Reusing a key with
        different parameters is refused with 422
        (:func:`~nemau.errors.is_idempotency_key_mismatch`).

        Use one block per write: two writes with different parameters under the
        same key conflict.
        """
        token = _key_override.set(validate_idempotency_key(key))
        try:
            yield key
        finally:
            _key_override.reset(token)

    def submit(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
    ) -> TaskRef:
        """Performs a write and decodes the 202 envelope.

        The request carries an ``Idempotency-Key``: ``idempotency_key`` when
        given, else the one pinned by :meth:`idempotency_key`, else a random one.
        The same key is sent on every retry. ``TaskRef.replayed`` is true when
        the server answered with the task of an earlier request.

        A response without a task id raises :class:`~nemau.errors.NemauApiError`:
        the request did not reach a queued write, and there is no task to wait
        for.
        """
        if idempotency_key is None:
            idempotency_key = _key_override.get() or uuid.uuid4().hex
        key = validate_idempotency_key(idempotency_key)
        status, raw, response_headers = self._send(
            method, path, query=query, json=json, idempotency_key=key
        )
        payload = jsonlib.loads(raw) if status != 204 and raw else None
        ref = TaskRef.from_dict(payload if isinstance(payload, dict) else {})
        ref.replayed = _header(response_headers, IDEMPOTENT_REPLAYED_HEADER).lower() == "true"
        if not ref.task_id:
            raise NemauApiError(
                502,
                "the response carries no task id",
                method=method,
                path=API_PREFIX + path,
            )
        return ref

    def _url(self, path: str, query: Mapping[str, QueryValue] | None) -> str:
        url = self.base_url + API_PREFIX + path
        if query:
            pairs = [(k, str(v)) for k, v in query.items() if v is not None and v != ""]
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        return url

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        json: Any = None,
        body: bytes | None = None,
        content_type: str | None = None,
        decode: bool = True,
        idempotency_key: str | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        url = self._url(path, query)
        # A raw byte body (an object upload) is sent once and never retried.
        attempts = 1 if body is not None else max(1, self._retry.max_attempts)
        # A write is retried only when the server can recognise the retry.
        replayable = method in _READ_ONLY or idempotency_key is not None

        payload: bytes | None = body
        headers: dict[str, str] = {"Accept": "application/json", "User-Agent": self._user_agent}
        if body is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
            # The API refuses an object upload without a Content-Length.
            headers["Content-Length"] = str(len(body))
        elif json is not None:
            payload = jsonlib.dumps(json).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers[IDEMPOTENCY_KEY_HEADER] = idempotency_key
        if self.project:
            headers[PROJECT_SCOPE_HEADER] = self.project
        if self._credential:
            self._credential.apply(headers)

        last_error: BaseException | None = None
        wait = 0.0
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                time.sleep(wait)

            request = urllib.request.Request(url, data=payload, method=method)
            for key, value in headers.items():
                request.add_header(key, value)

            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    raw = response.read()
                    return response.status, raw, dict(response.headers)
            except urllib.error.HTTPError as http_error:
                raw = http_error.read()
                api_error = api_error_from_body(
                    http_error.code,
                    raw.decode("utf-8", "replace") if decode else "",
                    method=method,
                    path=urllib.parse.urlparse(url).path,
                )
                retry_after = None
                if http_error.code in _RETRY_AFTER_STATUSES:
                    retry_after = _parse_retry_after(http_error.headers.get("Retry-After"))
                    api_error.retry_after = retry_after
                if not api_error.retryable or not replayable or attempt == attempts:
                    raise api_error from None
                if retry_after is not None and retry_after > self._retry.max_retry_after:
                    raise api_error from None
                wait = max(self._retry.backoff(attempt), retry_after or 0.0)
                last_error = api_error
            except urllib.error.URLError as url_error:
                # A write whose response was lost may have been applied, so it is
                # retried only with its idempotency key.
                last_error = url_error
                if not replayable or attempt == attempts:
                    raise
                wait = self._retry.backoff(attempt)

        assert last_error is not None
        raise last_error

    # ----------------------------------------------------------------- #
    # Tasks
    # ----------------------------------------------------------------- #

    def wait_for_task(self, task_id: str, *, deadline: float | None = None) -> Task:
        """Polls a task until it reaches a terminal state.

        Returns the successful task, and raises a
        :class:`~nemau.errors.NemauTaskFailedError` carrying the task on failure
        or cancellation.

        ``deadline`` is a :func:`time.monotonic` timestamp rather than a
        duration, so several waits in a row can share one deadline.
        """
        if not task_id:
            raise ValueError("nemau: wait_for_task called without a task id")
        while True:
            try:
                task = self.tasks.get(task_id)
            except NemauApiError as error:
                # Right after the 202 the task may not be readable yet, so a
                # 404 means "not yet" and the polling continues.
                if not error.not_found:
                    raise
                task = None
            if task is not None and task.terminal:
                if not task.succeeded:
                    raise NemauTaskFailedError(task)
                return task
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"nemau: task {task_id} did not finish before the deadline")
            time.sleep(self.poll_interval)

    def wait_for(self, ref: TaskRef, *, deadline: float | None = None) -> Task:
        """:meth:`wait_for_task` for the reference a write returns."""
        return self.wait_for_task(ref.task_id, deadline=deadline)

    def wait_for_resource_id(
        self, ref: TaskRef, *keys: str, deadline: float | None = None
    ) -> tuple[str, Task]:
        """Runs a write to completion and returns the id it produced.

        The id is the task's ``resource_id`` when the platform set one, and
        otherwise the first non-empty value among the ``result`` keys the caller
        names.
        """
        task = self.wait_for(ref, deadline=deadline)
        if task.resource_id:
            return task.resource_id, task
        identifier = task.result_str(*keys)
        if identifier:
            return identifier, task
        raise NemauApiError(
            502,
            f"task {task.id} finished without a resource id (keys looked up: {', '.join(keys)})",
            method="GET",
            path=f"{API_PREFIX}/tasks/{task.id}",
        )


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return str(value)
    return ""


def _unwrap(payload: Any, key: str) -> list[Any]:
    """Extracts a list from a wrapped response, tolerating a missing key."""
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []
