import os
import subprocess
import sys
import threading
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, normalize_corporate_email
from apps.access.models import AccessGuard
from apps.access.policies import has_permission
from apps.access.services import actor_transaction, guarded_transaction
from apps.audit.models import AuditEntry
from apps.audit.services import record_action


def employee(email="employee@corp.kp11.ru", verified=True):
    return User.objects.create_user(
        email=email, full_name="Тестовый сотрудник", password=None,
        email_verified_at=timezone.now() if verified else None,
    )


class UserSchemaTests(TestCase):
    def test_corporate_normalization_and_unique_email(self):
        user = employee(" Employee@CORP.KP11.RU ")
        self.assertEqual(user.email, "employee@corp.kp11.ru")
        self.assertFalse(user.has_usable_password())
        with self.assertRaises(ValidationError):
            employee("EMPLOYEE@corp.kp11.ru")

    def test_similar_and_subdomains_rejected(self):
        for address in ["name@sub.corp.kp11.ru", "name@corp.kp11.ru.example.org", "name@сorp.kp11.ru", "not-an-address"]:
            with self.subTest(address=address), self.assertRaises(ValidationError):
                normalize_corporate_email(address)

    def test_database_rejects_unnormalized_and_foreign_email(self):
        for email in ["NAME@corp.kp11.ru", "name@example.org"]:
            with self.subTest(email=email), self.assertRaises(IntegrityError), transaction.atomic():
                User.objects.create(email=email, full_name="Тест")

    def test_email_backend_authenticates_without_granting_permissions(self):
        user = employee()
        user.set_password("stage1-long-valid-test-password")
        user.save()
        self.assertEqual(authenticate(email=user.email, password="stage1-long-valid-test-password").pk, user.pk)
        self.assertFalse(has_permission(user, "accounts.manage_users"))


class AccessHTTPTests(TestCase):
    def setUp(self):
        self.actor = employee()

    def login_session(self):
        self.client.force_login(self.actor, backend="apps.accounts.backends.EmailBackend")
        session = self.client.session
        session["auth_version"] = self.actor.auth_version
        session.save()

    def test_anonymous_root_redirects_and_login_is_honest(self):
        self.assertRedirects(self.client.get("/"), "/accounts/login/?next=/")
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "Доступ только по приглашению администратора")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertNotContains(response, "employee@corp.kp11.ru")

    def test_anonymous_json_returns_401(self):
        self.assertEqual(self.client.get("/", HTTP_ACCEPT="application/json").status_code, 401)

    def test_untrusted_host_rejected_even_on_public_page(self):
        with override_settings(DEBUG=False):
            response = self.client.get("/accounts/login/", HTTP_HOST="attacker.example.org")
        self.assertEqual(response.status_code, 400)

    def test_verified_server_session_reads_only_own_profile(self):
        other = employee("other@corp.kp11.ru")
        other.full_name = "Другой сотрудник"
        other.save()
        self.login_session()
        response = self.client.get("/")
        self.assertContains(response, "Тестовый сотрудник")
        self.assertNotContains(response, other.full_name)

    def test_session_without_epoch_denied(self):
        self.client.force_login(self.actor, backend="apps.accounts.backends.EmailBackend")
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_auth_version_revocation_applies_to_existing_session(self):
        self.login_session()
        User.objects.filter(pk=self.actor.pk).update(auth_version=2)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_disabled_or_unverified_account_cannot_read(self):
        self.login_session()
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertEqual(self.client.get("/").status_code, 302)
        User.objects.filter(pk=self.actor.pk).update(is_active=True, email_verified_at=None)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_admin_crud_is_absent_and_errors_are_russian(self):
        with override_settings(DEBUG=False):
            response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Страница не найдена", status_code=404)

    def test_csrf_rejects_unsafe_request(self):
        response = Client(enforce_csrf_checks=True).post("/accounts/login/", {"email": self.actor.email})
        self.assertEqual(response.status_code, 403)


@override_settings(HEALTHCHECK_TOKEN="test-internal-health-key")
class HealthTests(TestCase):
    def test_health_is_not_public(self):
        for path in ["/health/live/", "/health/ready/"]:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_readiness_uses_postgres_and_guard(self):
        self.assertEqual(connection.vendor, "postgresql")
        response = self.client.get("/health/ready/", HTTP_X_HEALTHCHECK_KEY="test-internal-health-key")
        self.assertEqual(response.content, b"ok\n")
        self.assertEqual(response.status_code, 200)

    def test_db_failure_is_503_without_details(self):
        with patch("core.views.connection.cursor", side_effect=DatabaseError("do-not-display-internals")):
            response = self.client.get("/health/ready/", HTTP_X_HEALTHCHECK_KEY="test-internal-health-key")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable\n")

    def test_liveness_does_not_need_database(self):
        with patch("core.views.connection.cursor", side_effect=DatabaseError):
            response = self.client.get("/health/live/", HTTP_X_HEALTHCHECK_KEY="test-internal-health-key")
        self.assertEqual(response.status_code, 200)


class AuditAndPrivilegeTests(TestCase):
    def test_audit_rolls_back_with_failed_action(self):
        with self.assertRaises(RuntimeError):
            with guarded_transaction(write=True):
                record_action(actor=None, action="environment.probe_created", object_type="environment", object_id="rollback", changes={"marker": "rollback"})
                raise RuntimeError("rollback")
        self.assertFalse(AuditEntry.objects.filter(object_id="rollback").exists())

    def test_catalog_rejects_sensitive_payload(self):
        with guarded_transaction(write=True), self.assertRaises(ValueError):
            record_action(actor=None, action="environment.probe_created", object_type="environment", object_id="blocked", changes={"password": "do-not-store"})
        self.assertFalse(AuditEntry.objects.exists())

    def test_audit_immutable_at_orm_and_database(self):
        with guarded_transaction(write=True):
            entry = record_action(actor=None, action="environment.probe_created", object_type="environment", object_id="immutable")
        with self.assertRaises(ValueError):
            AuditEntry.objects.filter(pk=entry.pk).update(reason="changed")
        with self.assertRaises(ValueError):
            entry.delete()
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_auditentry WHERE id = %s", [entry.pk])
        self.assertTrue(AuditEntry.objects.filter(pk=entry.pk).exists())

    def test_runtime_role_is_limited(self):
        role = connection.ops.quote_name(os.environ["DB_APP_USER"])
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {role}")
            cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")
            self.assertEqual(cursor.fetchone(), (False, False, False))
            cursor.execute("SELECT has_schema_privilege(current_user,'public','CREATE'), has_table_privilege(current_user,'audit_auditentry','UPDATE'), has_table_privilege(current_user,'audit_auditentry','DELETE'), has_table_privilege(current_user,'audit_auditentry','INSERT'), has_table_privilege(current_user,'django_migrations','INSERT')")
            self.assertEqual(cursor.fetchone(), (False, False, False, True, False))
        # SET LOCAL сбросится по окончании внешней TestCase-транзакции; вернуть явно.
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")


class GuardTransactionTests(TransactionTestCase):
    def setUp(self):
        AccessGuard.objects.get_or_create(pk=1)

    def test_actor_is_reloaded_and_inactive_denied(self):
        actor = employee()
        User.objects.filter(pk=actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            with actor_transaction(actor.pk, write=True):
                self.fail("Неактивный пользователь вошел в service.")

    def test_audit_requires_transaction(self):
        with self.assertRaises(RuntimeError):
            record_action(actor=None, action="environment.probe_created", object_type="environment", object_id="outside")

    def test_atomic_without_write_guard_does_not_allow_audit(self):
        with transaction.atomic(), self.assertRaises(RuntimeError):
            record_action(actor=None, action="environment.probe_created", object_type="environment", object_id="unguarded")

    def test_shared_guard_cannot_be_upgraded_by_nested_service(self):
        with guarded_transaction(), self.assertRaises(RuntimeError):
            with guarded_transaction(write=True):
                self.fail("Опасное повышение shared lock.")

    def test_shared_read_blocks_concurrent_write_until_commit(self):
        started = threading.Event()
        acquired = threading.Event()
        errors = []

        def writer():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '4s'")
                started.set()
                with guarded_transaction(write=True):
                    acquired.set()
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                connection.close()

        with guarded_transaction():
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(acquired.wait(.2), "Writer не должен обходить shared lock.")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(acquired.is_set())


class ProductionSettingsTests(TestCase):
    def run_profile(self, changes=None):
        environment = os.environ.copy()
        environment.update({
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "DJANGO_ALLOWED_HOSTS": "tracker.corp.kp11.ru",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://tracker.corp.kp11.ru",
            "DJANGO_DEBUG": "false",
            "EMAIL_HOST": "smtp.example.org", "EMAIL_PORT": "587",
            "PUBLIC_BASE_URL": "https://tracker.corp.kp11.ru",
        })
        environment.update(changes or {})
        code = "import django; django.setup(); from django.conf import settings; assert not settings.DEBUG; assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE; assert settings.EMAIL_USE_TLS; print('production settings OK')"
        return subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=15)

    def test_production_secure_configuration_loads(self):
        result = self.run_profile()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_debug_test_mail_and_wildcard_hosts_are_rejected(self):
        for changes in [{"DJANGO_DEBUG": "true"}, {"EMAIL_HOST": "mailpit"}, {"EMAIL_PORT": "1025"}, {"DJANGO_ALLOWED_HOSTS": "*"}, {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}]:
            with self.subTest(changes=changes):
                self.assertNotEqual(self.run_profile(changes).returncode, 0)
