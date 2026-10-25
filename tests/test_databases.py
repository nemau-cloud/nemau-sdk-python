"""Managed PostgreSQL: exact bodies, one-time passwords, and refusals before any request."""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from nemau import Database, DatabaseCredentials, DatabaseStatus
from tests.helpers import RecordedRequest, Stub, client_for

SUBNET = "0b5f3c1e-2d4a-4f6b-8c9d-1e2f3a4b5c6d"
SG = "7a8b9c0d-1e2f-4a3b-9c8d-7e6f5a4b3c2d"
DB = "11111111-2222-4333-8444-555555555555"
PASSWORD = "Qx7mP2vL9nR4tY6wZ1aB3cD5eF8gH0jK2lM4nP6rS8tU"
ACCEPTED = (202, {"task_id": "t-1", "status": "pending"})

VALID_CREATE: dict[str, Any] = {
    "name": "orders-db",
    "version": "17",
    "flavor": "db-medium",
    "storage_gb": 50,
    "subnet_id": SUBNET,
    "admin_username": "app_admin",
}


def _has_header(request: RecordedRequest, name: str) -> bool:
    return any(key.lower() == name.lower() for key in request.headers)


def _refused_locally(test: unittest.TestCase, call: Any, message: str) -> None:
    with Stub(lambda _r: ACCEPTED) as stub:
        with test.assertRaisesRegex(ValueError, message):
            call(client_for(stub))
        test.assertEqual(stub.requests, [], "no request must leave the client")


class ReadTest(unittest.TestCase):
    def test_list_get_and_ca_certificate_read_their_routes(self) -> None:
        def handler(request: RecordedRequest) -> tuple[int, Any]:
            if request.path.endswith("/ca-certificate"):
                return 200, {"ca_certificate_pem": "-----BEGIN CERTIFICATE-----\nAAA\n"}
            if request.path == "/api/v1/databases":
                return 200, {"databases": [{"id": DB, "name": "orders-db"}], "count": 1}
            return 200, {"id": DB, "name": "orders-db", "port": 5432, "status": "ACTIVE"}

        with Stub(handler) as stub:
            client = client_for(stub)
            self.assertEqual(client.databases.list()[0].id, DB)
            self.assertEqual(client.databases.get(DB).port, 5432)
            self.assertIn("BEGIN CERTIFICATE", client.databases.ca_certificate())
            self.assertEqual(
                [f"{r.method} {r.path}" for r in stub.requests],
                [
                    "GET /api/v1/databases",
                    f"GET /api/v1/databases/{DB}",
                    "GET /api/v1/databases/ca-certificate",
                ],
            )


class CreateTest(unittest.TestCase):
    def test_create_sends_exactly_the_fields_given_and_returns_the_password(self) -> None:
        with Stub(
            lambda _r: (202, {"task_id": "t-1", "username": "app_admin", "password": PASSWORD})
        ) as stub:
            out = client_for(stub).databases.create(
                **VALID_CREATE,
                security_group_ids=[SG],
                backup_retention_days=14,
                maintenance_window={"weekday": 0, "hour": 3},
            )
            self.assertEqual(out.password, PASSWORD)
            self.assertEqual(out.task_id, "t-1")
            request = stub.requests[0]
            self.assertEqual((request.method, request.path), ("POST", "/api/v1/databases"))
            self.assertEqual(
                request.json(),
                {
                    **VALID_CREATE,
                    "security_group_ids": [SG],
                    "backup_retention_days": 14,
                    "maintenance_window": {"weekday": 0, "hour": 3},
                },
            )

    def test_a_password_route_never_carries_an_idempotency_key(self) -> None:
        with Stub(
            lambda _r: (202, {"task_id": "t", "username": "app_admin", "password": PASSWORD})
        ) as stub:
            client_for(stub).databases.create(**VALID_CREATE)
            self.assertFalse(_has_header(stub.requests[0], "Idempotency-Key"))

    def test_the_password_is_left_out_of_repr_and_a_missing_one_is_a_generic_error(self) -> None:
        creds = DatabaseCredentials.from_dict(
            {"task_id": "t", "username": "u", "password": PASSWORD}
        )
        self.assertNotIn(PASSWORD, repr(creds))
        self.assertNotIn(PASSWORD, str(creds))
        with Stub(lambda _r: (202, {"task_id": "t", "username": "app_admin"})) as stub:
            with self.assertRaisesRegex(RuntimeError, "without a database password") as ctx:
                client_for(stub).databases.create(**VALID_CREATE)
            self.assertNotIn("app_admin", str(ctx.exception))

    def test_create_refuses_out_of_range_and_reserved_values_before_any_request(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"name": "Orders"}, "database name"),
            ({"name": "1db"}, "database name"),
            ({"storage_gb": 9}, "storage_gb must be between 10 and 2000"),
            ({"storage_gb": 2001}, "storage_gb"),
            ({"admin_username": "postgres"}, "reserved"),
            ({"admin_username": "pg_monitor"}, "reserved"),
            ({"admin_username": "nemau_ops"}, "reserved"),
            ({"admin_username": "ab"}, "admin_username"),
            ({"subnet_id": "not-a-uuid"}, "subnet_id must be a UUID"),
            ({"security_group_ids": [SG] * 6}, "at most 5"),
            ({"security_group_ids": ["nope"]}, "must hold UUIDs"),
            ({"backup_retention_days": 0}, "backup_retention_days must be between 1 and 35"),
            ({"backup_retention_days": 36}, "backup_retention_days"),
            ({"maintenance_window": {"weekday": 7, "hour": 3}}, "maintenance_window"),
            ({"maintenance_window": {"weekday": 1, "hour": 24}}, "maintenance_window"),
            ({"maintenance_window": {"weekday": 1}}, "maintenance_window"),
            ({"description": "line\nbreak"}, "control characters"),
            ({"description": "x" * 257}, "too long"),
        ]
        for patch, message in cases:
            with self.subTest(patch=patch):
                kwargs = {**VALID_CREATE, **patch}
                _refused_locally(self, lambda c, k=kwargs: c.databases.create(**k), message)


class ChangeTest(unittest.TestCase):
    def test_update_sends_only_the_fields_given(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).databases.update(DB, backup_retention_days=30)
            request = stub.requests[0]
            self.assertEqual((request.method, request.path), ("PATCH", f"/api/v1/databases/{DB}"))
            self.assertEqual(request.json(), {"backup_retention_days": 30})

    def test_update_refuses_an_empty_change(self) -> None:
        _refused_locally(self, lambda c: c.databases.update(DB), "no field to change")
        _refused_locally(
            self, lambda c: c.databases.update(DB, backup_retention_days=0), "between 1 and 35"
        )

    def test_resize_sends_the_given_fields_and_refuses_bad_ones(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).databases.resize(DB, flavor="db-large", apply_at="maintenance_window")
            request = stub.requests[0]
            self.assertEqual(request.path, f"/api/v1/databases/{DB}/resize")
            self.assertEqual(
                request.json(), {"flavor": "db-large", "apply_at": "maintenance_window"}
            )
        _refused_locally(
            self, lambda c: c.databases.resize(DB), "needs a flavor, a storage_gb, or both"
        )
        _refused_locally(self, lambda c: c.databases.resize(DB, storage_gb=5), "storage_gb")
        _refused_locally(
            self, lambda c: c.databases.resize(DB, flavor="db-large", apply_at="later"), "apply_at"
        )

    def test_rotate_password_returns_the_new_password_without_an_idempotency_key(self) -> None:
        body = {"task_id": "t", "database_id": DB, "username": "app_admin", "password": PASSWORD}
        with Stub(lambda _r: (202, body)) as stub:
            out = client_for(stub).databases.rotate_password(DB)
            self.assertEqual(out.password, PASSWORD)
            self.assertEqual(out.database_id, DB)
            self.assertEqual(stub.requests[0].path, f"/api/v1/databases/{DB}/rotate-password")
            self.assertFalse(_has_header(stub.requests[0], "Idempotency-Key"))


class RestoreTest(unittest.TestCase):
    def test_restore_posts_to_the_source_and_formats_the_time_as_rfc3339_utc(self) -> None:
        with Stub(
            lambda _r: (202, {"task_id": "t", "username": "app_admin", "password": PASSWORD})
        ) as stub:
            client_for(stub).databases.restore(
                DB,
                name="orders-restored",
                target_time=datetime(2026, 10, 20, 12, 30, tzinfo=timezone(timedelta(hours=2))),
                flavor="db-medium",
                storage_gb=50,
                subnet_id=SUBNET,
                admin_username="app_admin",
            )
            request = stub.requests[0]
            self.assertEqual(request.path, f"/api/v1/databases/{DB}/restore")
            self.assertEqual(
                request.json(),
                {
                    "name": "orders-restored",
                    "target_time": "2026-10-20T10:30:00Z",
                    "flavor": "db-medium",
                    "storage_gb": 50,
                    "subnet_id": SUBNET,
                    "admin_username": "app_admin",
                },
            )
            self.assertFalse(_has_header(request, "Idempotency-Key"))

    def test_restore_refuses_a_future_or_naive_or_malformed_time(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        base = {
            "name": "x",
            "flavor": "db-medium",
            "storage_gb": 50,
            "subnet_id": SUBNET,
            "admin_username": "app_admin",
        }
        _refused_locally(
            self,
            lambda c: c.databases.restore(DB, target_time=future, **base),
            "not be in the future",
        )
        _refused_locally(
            self,
            lambda c: c.databases.restore(DB, target_time=datetime(2026, 1, 1), **base),
            "timezone",
        )
        _refused_locally(
            self, lambda c: c.databases.restore(DB, target_time="yesterday", **base), "RFC 3339"
        )


class DeleteTest(unittest.TestCase):
    def test_delete_keeps_backups_unless_purge_is_asked(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client = client_for(stub)
            client.databases.delete(DB)
            client.databases.delete(DB, purge_backups=True)
            self.assertEqual(stub.requests[0].method, "DELETE")
            self.assertEqual(stub.requests[0].query, "")
            self.assertEqual(stub.requests[1].query, "purge_backups=true")


class CatalogAndRestartTest(unittest.TestCase):
    def test_flavors_and_versions_read_the_region_catalogs(self) -> None:
        def handler(request: RecordedRequest) -> tuple[int, Any]:
            if request.path.endswith("/database-flavors"):
                return 200, {"flavors": [{"name": "db-small", "vcpus": 2, "memory_mb": 4096}]}
            return 200, {"versions": ["16", "17"]}

        with Stub(handler) as stub:
            client = client_for(stub)
            flavors = client.databases.flavors()
            self.assertEqual(
                [(f.name, f.vcpus, f.memory_mb) for f in flavors], [("db-small", 2, 4096)]
            )
            self.assertEqual(client.databases.versions(), ["16", "17"])
            self.assertEqual(
                [f"{r.method} {r.path}" for r in stub.requests],
                ["GET /api/v1/database-flavors", "GET /api/v1/database-versions"],
            )

    def test_restart_posts_to_its_route_with_no_body(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).databases.restart(DB)
            request = stub.requests[0]
            self.assertEqual(
                (request.method, request.path), ("POST", f"/api/v1/databases/{DB}/restart")
            )
            self.assertEqual(request.body, b"")

    def test_update_clears_the_maintenance_window_with_an_explicit_null(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).databases.update(DB, maintenance_window=None)
            self.assertEqual(stub.requests[0].json(), {"maintenance_window": None})
        _refused_locally(
            self,
            lambda c: c.databases.update(DB, maintenance_window={"weekday": 7, "hour": 3}),
            "maintenance_window needs weekday 0-6",
        )


if __name__ == "__main__":
    unittest.main()


class RegressionTest(unittest.TestCase):
    def test_fractional_seconds_of_any_length_are_accepted_as_restore_points(self) -> None:
        from nemau.services.databases import _restore_time

        self.assertEqual(_restore_time("2026-10-20T10:30:00.5Z"), "2026-10-20T10:30:00Z")
        self.assertEqual(_restore_time("2026-10-20T10:30:00.123456789Z"), "2026-10-20T10:30:00Z")

    def test_a_trailing_newline_is_refused_as_part_of_the_value(self) -> None:
        _refused_locally(
            self,
            lambda c: c.databases.create(**{**VALID_CREATE, "name": "orders\n"}),
            "database name",
        )
        _refused_locally(
            self,
            lambda c: c.databases.create(**{**VALID_CREATE, "subnet_id": SUBNET + "\n"}),
            "subnet_id",
        )


VIEW: dict[str, Any] = {
    "id": DB,
    "name": "orders-db",
    "description": "Orders",
    "status": "active",
    "version": "17",
    "flavor": "db-medium",
    "storage_gb": 50,
    "subnet_id": SUBNET,
    "security_group_ids": [SG],
    "admin_username": "app_admin",
    "address": "10.20.0.12",
    "port": 5432,
    "backup_retention_days": 7,
    "maintenance_window": {"weekday": 0, "hour": 3},
    "earliest_restorable_time": "2026-10-18T00:00:00Z",
    "latest_restorable_time": "2026-10-25T09:12:00Z",
    "pending_resize": {"flavor": "db-large", "storage_gb": 100, "apply_at": "2026-10-26T03:00:00Z"},
    "created_at": "2026-10-01T10:00:00Z",
    "updated_at": "2026-10-25T09:00:00Z",
}


class ViewTest(unittest.TestCase):
    def test_the_view_decodes_status_restore_window_and_pending_resize(self) -> None:
        with Stub(lambda _r: (200, VIEW)) as stub:
            db = client_for(stub).databases.get(DB)
        self.assertIsInstance(db, Database)
        self.assertEqual(db.status, DatabaseStatus.ACTIVE)
        self.assertEqual(db.description, "Orders")
        self.assertEqual(db.earliest_restorable_time, "2026-10-18T00:00:00Z")
        self.assertIsNone(db.restorable_until)
        self.assertIsNotNone(db.pending_resize)
        self.assertEqual(db.pending_resize.apply_at, "2026-10-26T03:00:00Z")  # type: ignore[union-attr]

    def test_a_deleted_database_closes_its_window_and_holds_no_resize(self) -> None:
        gone = {
            **VIEW,
            "status": "deleted",
            "pending_resize": None,
            "restorable_until": "2026-11-01T00:00:00Z",
        }
        with Stub(lambda _r: (200, gone)) as stub:
            db = client_for(stub).databases.get(DB)
        self.assertEqual(db.status, DatabaseStatus.DELETED)
        self.assertIsNone(db.pending_resize)
        self.assertEqual(db.restorable_until, "2026-11-01T00:00:00Z")

    def test_resize_sends_no_apply_at_by_default_so_the_server_applies_it_now(self) -> None:
        with Stub(lambda _r: ACCEPTED) as stub:
            client_for(stub).databases.resize(DB, storage_gb=60)
            self.assertEqual(stub.requests[0].json(), {"storage_gb": 60})


class PasswordLeakTest(unittest.TestCase):
    def test_asdict_and_the_fields_never_carry_the_password(self) -> None:
        creds = DatabaseCredentials.from_dict(
            {"task_id": "t", "username": "u", "password": PASSWORD}
        )
        self.assertEqual(creds.password, PASSWORD)
        self.assertNotIn(PASSWORD, str(dataclasses.asdict(creds)))
        self.assertNotIn("password", {f.name for f in dataclasses.fields(creds)})
        self.assertNotIn(PASSWORD, repr(creds))

    def test_a_decoded_model_never_keeps_the_password_as_unknown_data(self) -> None:
        creds = DatabaseCredentials.from_dict(
            {"task_id": "t", "username": "u", "password": PASSWORD}
        )
        self.assertEqual(creds.extra, {})
        self.assertNotIn(PASSWORD, repr(creds.__dict__.get("extra")))

    def test_the_password_object_redacts_its_repr_and_str(self) -> None:
        from nemau.models import OneTimePassword

        held = OneTimePassword(PASSWORD)
        self.assertEqual(held.value, PASSWORD)
        self.assertNotIn(PASSWORD, repr(held))
        self.assertNotIn(PASSWORD, str(held))
        self.assertNotIn(PASSWORD, f"{held}")
