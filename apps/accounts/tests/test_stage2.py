import importlib
import json
import re
import threading
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.apps import apps
from django.contrib.auth import authenticate
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.limits import RateLimited, consume_limits
from apps.accounts.models import Invitation, PasswordResetGrant, RateLimitBucket, User
from apps.accounts.services import activate_employee, bootstrap_administrator, invite_employee, request_recovery, resend_invitation, reset_password
from apps.access.administration import VersionConflict, save_role
from apps.accounts.administration import edit_employee
from apps.audiences.services import save_group
from apps.access.catalog import PERMISSIONS
from apps.access.models import AccessGuard, Permission, Role, RolePermission, UserRole
from apps.access.policies import has_permission
from apps.audit.models import AuditEntry
from apps.audiences.models import AudienceGroup, Membership

PASSWORD = 'Test-only-complex-password-2026!'
NEW_PASSWORD = 'Another-test-only-password-2026!'


def seed():
    AccessGuard.objects.get_or_create(pk=1)
    importlib.import_module('apps.access.migrations.0004_seed_and_protection').seed(apps, None)


def employee(email='employee@kp11.ru', admin=False):
    user = User.objects.create_user(email=email, full_name='Тестовый сотрудник', password=PASSWORD, email_verified_at=timezone.now())
    if admin:
        UserRole.objects.create(user=user, role=Role.objects.get(is_system_admin=True))
    return user


def force_login(client, user):
    client.force_login(user, backend='apps.accounts.backends.EmailBackend')
    session = client.session
    session['auth_version'] = user.auth_version
    session.save()


def latest_token():
    url = re.search(r'https?://\S+', mail.outbox[-1].body).group(0)
    return parse_qs(urlsplit(url).query)['token'][0]


class StageTwoTests(TestCase):
    def setUp(self):
        seed()
        self.admin = employee('admin@kp11.ru', admin=True)
        self.actor = dict(actor_id=self.admin.pk, auth_version=self.admin.auth_version)

    def invitation(self, email='invitee@kp11.ru'):
        with self.captureOnCommitCallbacks(execute=True):
            user, delivery = invite_employee(**self.actor, email=email, full_name='Приглашенный сотрудник')
        self.assertTrue(delivery.sent)
        return user, latest_token()

    def edit(self, user, **overrides):
        data = dict(self.actor, user_id=user.pk, expected_version=user.version, full_name=user.full_name, is_active=user.is_active, role_ids=list(user.user_roles.values_list('role_id', flat=True)), group_ids=list(user.memberships.values_list('group_id', flat=True)))
        data.update(overrides)
        return edit_employee(**data)

    def test_seed_exact_catalog_roles_groups_and_union(self):
        self.assertEqual(Role.objects.count(), 4)
        self.assertEqual(AudienceGroup.objects.count(), 3)
        self.assertEqual(set(Permission.objects.values_list('code', flat=True)), set(PERMISSIONS))
        user = employee()
        custom = save_role(**self.actor, name='Наблюдатель', is_active=True, permission_codes=['workitems.view_all'])
        UserRole.objects.create(user=user, role=Role.objects.get(key='manager'))
        UserRole.objects.create(user=user, role=custom)
        self.assertTrue(has_permission(user, 'workitems.create'))
        self.assertTrue(has_permission(user, 'workitems.view_all'))
        self.assertFalse(has_permission(user, 'workitems.edit_all'))
        self.assertFalse(has_permission(user, 'accounts.manage_users'))
        self.assertFalse(has_permission(user, 'unknown.permission'))
        custom.is_active = False
        custom.save()
        self.assertFalse(has_permission(user, 'workitems.view_all'))

    def test_membership_never_grants_permissions_or_automatic_group(self):
        user = employee()
        UserRole.objects.create(user=user, role=Role.objects.get(key='curator'))
        self.assertFalse(user.memberships.exists())
        Membership.objects.create(user=user, group=AudienceGroup.objects.first())
        for code in PERMISSIONS:
            self.assertFalse(has_permission(user, code))

    def test_admin_only_permissions_and_role_identity_protected(self):
        with self.assertRaises(ValidationError):
            save_role(**self.actor, name='Обход', is_active=True, permission_codes=['accounts.manage_users'])
        role = Role.objects.get(key='teacher')
        with self.assertRaises(DatabaseError), transaction.atomic():
            RolePermission.objects.create(role=role, permission_id='access.manage_roles')
        with self.assertRaises(DatabaseError), transaction.atomic():
            Role.objects.filter(pk=role.pk).update(is_system_admin=True)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Role.objects.filter(pk=role.pk).update(key='forged')

    def test_permission_catalog_runtime_read_only(self):
        with transaction.atomic(), connection.cursor() as cursor:
            import os
            cursor.execute('SET LOCAL ROLE '+connection.ops.quote_name(os.environ['DB_APP_USER']))
            cursor.execute("SELECT has_table_privilege(current_user,'access_permission','SELECT'), has_table_privilege(current_user,'access_permission','INSERT'), has_table_privilege(current_user,'access_permission','UPDATE')")
            self.assertEqual(cursor.fetchone(), (True, False, False))
        with connection.cursor() as cursor:
            cursor.execute('RESET ROLE')

    def test_invitation_normalizes_reserves_email_and_has_no_password(self):
        user, token = self.invitation(' Mixed@KP11.RU ')
        self.assertEqual(user.email, 'mixed@kp11.ru')
        self.assertFalse(user.has_usable_password())
        self.assertIsNone(authenticate(email=user.email, password=PASSWORD))
        with self.assertRaises(ValidationError):
            self.invitation('MIXED@kp11.ru')
        stored = Invitation.objects.get(user=user)
        self.assertNotEqual(stored.token_digest, token)
        self.assertNotIn(token, json.dumps(list(AuditEntry.objects.values('changes'))))
        for address in ['x@sub.corp.kp11.ru', 'x@kp11.ru.evil.test', 'x@example.org']:
            with self.assertRaises(ValidationError):
                invite_employee(**self.actor, email=address, full_name='Тест')

    def test_activation_requires_secret_own_valid_password_and_post(self):
        user, token = self.invitation()
        self.assertEqual(self.client.get('/accounts/activate/', {'token':user.email}).status_code, 400)
        self.assertEqual(self.client.get('/accounts/activate/', {'token':token}).status_code, 200)
        self.assertIsNone(Invitation.objects.get(user=user).used_at)
        with self.assertRaises(ValidationError):
            activate_employee(token=token, password='123')
        activated = activate_employee(token=token, password=PASSWORD)
        self.assertTrue(activated.check_password(PASSWORD))
        self.assertIsNotNone(activated.email_verified_at)
        self.assertIsNotNone(Invitation.objects.get(user=user).used_at)
        with self.assertRaises(ValidationError):
            activate_employee(token=token, password=PASSWORD)

    def test_expiry_resend_revocation_and_disabled_activation(self):
        user, old = self.invitation()
        with self.captureOnCommitCallbacks(execute=True):
            resend_invitation(**self.actor, user_id=user.pk)
        new = latest_token()
        self.assertNotEqual(old, new)
        with self.assertRaises(ValidationError):
            activate_employee(token=old, password=PASSWORD)
        Invitation.objects.filter(user=user, revoked_at__isnull=True).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            activate_employee(token=new, password=PASSWORD)
        with self.captureOnCommitCallbacks(execute=True):
            resend_invitation(**self.actor, user_id=user.pk)
        newest = latest_token()
        self.edit(user, is_active=False)
        with self.assertRaises(ValidationError):
            activate_employee(token=newest, password=PASSWORD)

    def test_smtp_failure_preserves_invite_and_explicit_resend(self):
        with patch('apps.accounts.services.send_mail', side_effect=RuntimeError('secret-not-to-log')), self.captureOnCommitCallbacks(execute=True):
            user, delivery = invite_employee(**self.actor, email='retry@kp11.ru', full_name='Повтор')
        self.assertFalse(delivery.sent)
        self.assertTrue(Invitation.objects.filter(user=user).exists())
        with self.captureOnCommitCallbacks(execute=True):
            result = resend_invitation(**self.actor, user_id=user.pk)
        self.assertTrue(result.sent)
        activate_employee(token=latest_token(), password=PASSWORD)

    def test_bootstrap_single_use_and_pending_resend_no_public_route(self):
        # Existing admin forbids bootstrap; no HTTP shortcut exists.
        with self.assertRaises(ValidationError):
            bootstrap_administrator(email='bootstrap@kp11.ru', full_name='Первый')
        self.assertEqual(self.client.get('/accounts/bootstrap/').status_code, 404)
        self.assertEqual(self.client.get('/accounts/register/').status_code, 404)

    def test_login_logout_csrf_safe_redirect_and_session_epoch(self):
        response = self.client.post('/accounts/login/', {'email':' ADMIN@KP11.RU ', 'password':PASSWORD, 'next':'https://evil.test/'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')
        self.assertEqual(self.client.session['auth_version'], self.admin.auth_version)
        self.assertContains(self.client.get('/'), 'Задач пока нет')
        self.assertEqual(self.client.get('/accounts/logout/').status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/accounts/login/', {'email':self.admin.email, 'password':PASSWORD}).status_code, 403)
        self.assertEqual(self.client.post('/accounts/logout/').status_code, 302)
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_disable_and_role_revocation_apply_to_open_session(self):
        second = employee('second@kp11.ru', admin=True)
        client = Client();force_login(client, second)
        self.assertEqual(client.get('/administration/').status_code, 200)
        self.edit(second, role_ids=[])
        self.assertEqual(client.get('/administration/').status_code, 403)
        self.assertEqual(client.get('/').status_code, 200)
        second.refresh_from_db()
        self.edit(second, is_active=False)
        self.assertEqual(client.get('/').status_code, 302)
        self.assertIsNone(authenticate(email=second.email, password=PASSWORD))
        second.refresh_from_db()
        self.edit(second, is_active=True)
        self.assertEqual(client.get('/').status_code, 302)

    def test_last_admin_and_system_role_disabling_rollback(self):
        count = AuditEntry.objects.count()
        with self.assertRaises(ValidationError):
            self.edit(self.admin, is_active=False)
        with self.assertRaises(ValidationError):
            self.edit(self.admin, role_ids=[])
        role = Role.objects.get(is_system_admin=True)
        with self.assertRaises(ValidationError):
            save_role(**self.actor, role_id=role.pk, expected_version=role.version, name='Переименовано', is_active=False, permission_codes=list(PERMISSIONS))
        with self.assertRaises(ValidationError):
            save_role(**self.actor, role_id=role.pk, expected_version=role.version, name='Переименовано', is_active=True, permission_codes=[])
        self.assertEqual(AuditEntry.objects.count(), count)
        self.admin.refresh_from_db();self.assertTrue(self.admin.is_active)
        role.refresh_from_db();self.assertTrue(role.is_active)
        renamed = save_role(**self.actor, role_id=role.pk, expected_version=role.version, name='Системный администратор', is_active=True, permission_codes=list(PERMISSIONS))
        self.assertTrue(renamed.is_system_admin)
        self.assertTrue(has_permission(self.admin, 'accounts.manage_users'))

    def test_direct_post_and_service_cannot_escalate_privileges(self):
        user = employee();force_login(self.client, user)
        for path, payload in [('/administration/invite/', {'email':'attacker@kp11.ru', 'full_name':'Атакующий'}), ('/administration/roles/new/', {'name':'Обход','permissions':['access.manage_roles']}), ('/administration/groups/new/', {'name':'Обход'}), (f'/administration/employees/{user.pk}/', {'full_name':'Атакующий','is_active':'on','version':user.version,'roles':[str(Role.objects.get(is_system_admin=True).pk)]})]:
            self.assertEqual(self.client.post(path, payload).status_code, 403)
        with self.assertRaises(PermissionDenied):
            invite_employee(actor_id=user.pk, auth_version=user.auth_version, email='another@kp11.ru', full_name='Тест')
        self.assertFalse(user.user_roles.exists())
        self.assertFalse(User.objects.filter(email='attacker@kp11.ru').exists())

    def test_versions_conflict_preserves_input_and_changes_audited(self):
        target = employee()
        self.edit(target, full_name='Новое имя')
        with self.assertRaises(VersionConflict):
            self.edit(target, full_name='Старая форма')
        force_login(self.client,self.admin)
        response = self.client.post(f'/administration/employees/{target.pk}/', {'version':target.version, 'full_name':'Сохраненный ввод','is_active':'on'})
        self.assertContains(response,'Сохраненный ввод',status_code=409)
        self.assertEqual(AuditEntry.objects.filter(action='accounts.employee_changed').count(),1)
        group = save_group(**self.actor,name='Дополнительная',is_active=True)
        updated = save_group(**self.actor,group_id=group.pk,expected_version=group.version,name='Переименованная',is_active=False)
        self.assertFalse(updated.is_active)
        with self.assertRaises(VersionConflict):
            save_group(**self.actor,group_id=group.pk,expected_version=group.version,name='Старая',is_active=True)
        self.assertEqual(AuditEntry.objects.filter(action='audiences.group_saved').count(),2)

    def test_revocation_during_invalid_post_prevents_profile_render(self):
        from apps.accounts.forms import EmployeeForm

        second=employee('second@kp11.ru',admin=True)
        target=employee('private@kp11.ru')
        force_login(self.client,self.admin)
        def revoke_before_validation(*args,**kwargs):
            edit_employee(actor_id=second.pk,auth_version=second.auth_version,user_id=self.admin.pk,expected_version=self.admin.version,full_name=self.admin.full_name,is_active=True,role_ids=[],group_ids=[])
            return EmployeeForm(*args,**kwargs)
        with patch('apps.accounts.views.EmployeeForm',side_effect=revoke_before_validation):
            response=self.client.post(f'/administration/employees/{target.pk}/',{'full_name':'','version':target.version})
        self.assertEqual(response.status_code,403)
        self.assertNotIn(target.email,response.content.decode())

    def test_management_forms_create_update_and_email_immutable(self):
        force_login(self.client,self.admin)
        self.assertEqual(self.client.get('/administration/').status_code,200)
        response = self.client.post('/administration/roles/new/', {'name':'Читатель','is_active':'on','permissions':['workitems.view_all']})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.post('/administration/groups/new/', {'name':'Новая группа','is_active':'on'}).status_code,302)
        with self.assertRaises(DatabaseError),transaction.atomic():
            User.objects.filter(pk=self.admin.pk).update(email='changed@kp11.ru')

    def test_generic_recovery_no_leak_for_unknown_inactive_or_unverified(self):
        inactive=employee('inactive@kp11.ru');inactive.is_active=False;inactive.save()
        unverified=User.objects.create_user(email='pending@kp11.ru',full_name='Ожидает')
        responses=[]
        for email in [self.admin.email,'unknown@kp11.ru',inactive.email,unverified.email,'foreign@example.org']:
            with self.captureOnCommitCallbacks(execute=True):
                responses.append(self.client.post('/accounts/recovery/', {'email':email}))
        self.assertEqual({r.status_code for r in responses},{200})
        self.assertTrue(all(r.content==responses[0].content for r in responses))
        self.assertEqual(PasswordResetGrant.objects.count(),1)
        self.assertEqual(len(mail.outbox),1)

    def test_reset_revokes_all_old_sessions_and_is_single_use(self):
        first=Client();second=Client();force_login(first,self.admin);force_login(second,self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            request_recovery(self.admin.email)
        token=latest_token()
        self.assertEqual(self.client.get('/accounts/reset/', {'token':token}).status_code,200)
        self.assertIsNone(PasswordResetGrant.objects.get().used_at)
        reset_password(token=token,password=NEW_PASSWORD)
        self.assertEqual(first.get('/').status_code,302)
        self.assertEqual(second.get('/').status_code,302)
        with self.assertRaises(ValidationError):
            reset_password(token=token,password=PASSWORD)
        self.assertIsNone(authenticate(email=self.admin.email,password=PASSWORD))
        self.assertIsNotNone(authenticate(email=self.admin.email,password=NEW_PASSWORD))

    def test_reset_expired_replaced_disabled_and_password_change_invalidates(self):
        with self.captureOnCommitCallbacks(execute=True):request_recovery(self.admin.email)
        old=latest_token()
        with self.captureOnCommitCallbacks(execute=True):request_recovery(self.admin.email)
        token=latest_token()
        with self.assertRaises(ValidationError):reset_password(token=old,password=NEW_PASSWORD)
        PasswordResetGrant.objects.filter(revoked_at__isnull=True).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError):reset_password(token=token,password=NEW_PASSWORD)
        target=employee()
        with self.captureOnCommitCallbacks(execute=True):request_recovery(target.email)
        token=latest_token();self.edit(target,is_active=False)
        with self.assertRaises(ValidationError):reset_password(token=token,password=NEW_PASSWORD)

    def test_password_change_preserves_current_session_revokes_other(self):
        force_login(self.client,self.admin)
        other=Client();force_login(other,self.admin)
        with self.captureOnCommitCallbacks(execute=True):request_recovery(self.admin.email)
        token=latest_token()
        payload={'old_password':PASSWORD,'password':NEW_PASSWORD,'password_confirm':NEW_PASSWORD}
        self.assertEqual(self.client.post('/accounts/password/',payload).status_code,200)
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(other.get('/').status_code,302)
        with self.assertRaises(ValidationError):reset_password(token=token,password=PASSWORD)
        self.assertNotIn(NEW_PASSWORD,json.dumps(list(AuditEntry.objects.values('changes'))))

    def test_auth_pages_no_store_referrer_no_token_mutation_on_get(self):
        user,token=self.invitation()
        for url in ['/accounts/login/','/accounts/recovery/','/accounts/activate/?token='+token]:
            response=self.client.get(url)
            self.assertEqual(response['Cache-Control'],'no-store')
            self.assertEqual(response['Referrer-Policy'],'strict-origin')
        self.assertEqual(Client(enforce_csrf_checks=True).post('/accounts/activate/',{'token':token,'password':PASSWORD,'password_confirm':PASSWORD}).status_code,403)
        self.assertIsNone(Invitation.objects.get(user=user).used_at)

    @override_settings(DEBUG=True)
    def test_auth_exception_does_not_expose_debug_secrets(self):
        with patch('apps.accounts.views.login_employee',side_effect=RuntimeError('exception-secret-token')):
            response=self.client.post('/accounts/login/',{'email':self.admin.email,'password':PASSWORD})
        self.assertEqual(response.status_code,500)
        for secret in [PASSWORD,'exception-secret-token','Traceback']:
            self.assertNotIn(secret,response.content.decode())

    def test_csrf_all_mutating_auth_and_admin_endpoints(self):
        client=Client(enforce_csrf_checks=True);force_login(client,self.admin)
        for path in ['/accounts/logout/','/accounts/password/','/accounts/reset/','/administration/invite/','/administration/roles/new/','/administration/groups/new/',f'/administration/employees/{self.admin.pk}/']:
            self.assertEqual(client.post(path,{'password':PASSWORD}).status_code,403)
        self.admin.refresh_from_db();self.assertTrue(self.admin.is_active)
        self.assertEqual(AuditEntry.objects.count(),0)

    def test_invitation_limits_shared_per_address_and_actor(self):
        user,_=self.invitation()
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):resend_invitation(**self.actor,user_id=user.pk)
        with self.assertRaises(RateLimited):resend_invitation(**self.actor,user_id=user.pk)
        self.assertEqual(Invitation.objects.filter(user=user).count(),3)

    def test_limits_apply_normalized_address_and_recovery_generic(self):
        for attempt in range(5):
            self.assertEqual(self.client.post('/accounts/login/', {'email':' ADMIN@KP11.RU ','password':'wrong'}).status_code,400)
        response=self.client.post('/accounts/login/', {'email':self.admin.email,'password':PASSWORD})
        self.assertEqual(response.status_code,429);self.assertIn('Retry-After',response)
        for attempt in range(3):self.assertEqual(self.client.post('/accounts/recovery/',{'email':'unknown@kp11.ru'}).status_code,200)
        self.assertEqual(self.client.post('/accounts/recovery/',{'email':' UNKNOWN@KP11.RU '}).status_code,429)
        self.assertFalse(RateLimitBucket.objects.filter(key_digest__contains='admin').exists())


class StageTwoConcurrencyTests(TransactionTestCase):
    def setUp(self):
        seed()

    def concurrent(self, operations):
        barrier=threading.Barrier(len(operations))
        results=[]
        lock=threading.Lock()
        def run(operation):
            close_old_connections()
            try:
                with connection.cursor() as cursor:cursor.execute("SET statement_timeout = '8s'")
                barrier.wait(4)
                operation()
                result='ok'
            except (ValidationError,PermissionDenied,RateLimited) as error:
                result=type(error).__name__
            except Exception as error:
                result='unexpected:'+type(error).__name__
            finally:
                connection.close()
            with lock:results.append(result)
        threads=[threading.Thread(target=run,args=(op,)) for op in operations]
        for thread in threads:thread.start()
        for thread in threads:thread.join(15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(any(result.startswith('unexpected:') for result in results),results)
        return results

    def test_smtp_callback_runs_after_guard_commit(self):
        admin=employee('admin@kp11.ru',admin=True)
        from apps.access.services import guarded_transaction
        observed=[]
        def smtp(*args,**kwargs):
            def acquire():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:cursor.execute("SET statement_timeout = '2s'")
                    with guarded_transaction(write=True):observed.append(True)
                finally:connection.close()
            thread=threading.Thread(target=acquire);thread.start();thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(observed,[True])
            return 1
        with patch('apps.accounts.services.send_mail',side_effect=smtp):
            _,outcome=invite_employee(actor_id=admin.pk,auth_version=1,email='outside@kp11.ru',full_name='Вне блокировки')
        self.assertTrue(outcome.sent)

    def test_concurrent_activation_exactly_once(self):
        admin=employee('admin@kp11.ru',admin=True)
        user,delivery=invite_employee(actor_id=admin.pk,auth_version=1,email='invited@kp11.ru',full_name='Приглашенный')
        self.assertTrue(delivery.sent);token=latest_token()
        results=self.concurrent_password_posts('/accounts/activate/',token,PASSWORD)
        self.assertEqual(sorted(results),['ValidationError','ok'])
        self.assertEqual(AuditEntry.objects.filter(action='accounts.activated').count(),1)
        self.assertIsNotNone(Invitation.objects.get(user=user).used_at)

    def test_concurrent_password_reset_exactly_once(self):
        user=employee();request_recovery(user.email);token=latest_token()
        results=self.concurrent_password_posts('/accounts/reset/',token,NEW_PASSWORD)
        self.assertEqual(sorted(results),['ValidationError','ok'])
        self.assertEqual(AuditEntry.objects.filter(action='accounts.password_reset').count(),1)

    def concurrent_password_posts(self, path, token, password):
        clients=[Client(enforce_csrf_checks=True) for _ in range(2)]
        for client in clients:
            self.assertEqual(client.get(path,{'token':token}).status_code,200)
        def post(client):
            def operation():
                response=client.post(path,{'token':token,'password':password,'password_confirm':password,'csrfmiddlewaretoken':client.cookies['csrftoken'].value})
                if response.status_code == 400:
                    raise ValidationError('Ссылка уже использована.')
                self.assertContains(response,'Пароль сохранен',status_code=200)
            return operation
        return self.concurrent([post(client) for client in clients])

    def assert_admin_race(self, remove_role):
        first=employee('first@kp11.ru',admin=True)
        second=employee('second@kp11.ru',admin=True)
        def disable(user):
            return lambda:edit_employee(actor_id=user.pk,auth_version=user.auth_version,user_id=user.pk,expected_version=user.version,full_name=user.full_name,is_active=remove_role,role_ids=[] if remove_role else list(user.user_roles.values_list('role_id',flat=True)),group_ids=[])
        results=self.concurrent([disable(first),disable(second)])
        self.assertEqual(sorted(results),['ValidationError','ok'])
        self.assertEqual(User.objects.filter(is_active=True,email_verified_at__isnull=False,user_roles__role__is_system_admin=True,user_roles__role__is_active=True).count(),1)
        self.assertEqual(AuditEntry.objects.filter(action='accounts.employee_changed').count(),1)

    def test_concurrent_admin_deactivation_leaves_one(self):
        self.assert_admin_race(False)

    def test_concurrent_admin_role_removal_leaves_one(self):
        self.assert_admin_race(True)

    def test_shared_rate_limits_multiple_connections_never_exceed(self):
        operation=lambda:consume_limits([('concurrency','address',5,3600)])
        results=self.concurrent([operation]*10)
        self.assertEqual(results.count('ok'),5,results)
        self.assertEqual(results.count('RateLimited'),5,results)
        self.assertEqual(RateLimitBucket.objects.get().count,5)

    def test_bootstrap_once_pending_resend_and_after_activation_forbidden(self):
        outcome=bootstrap_administrator(email='first@kp11.ru',full_name='Первый')
        self.assertTrue(outcome.sent)
        old=latest_token()
        outcome=bootstrap_administrator(email='first@kp11.ru',full_name='Первый',resend=True)
        self.assertTrue(outcome.sent)
        with self.assertRaises(ValidationError):activate_employee(token=old,password=PASSWORD)
        activate_employee(token=latest_token(),password=PASSWORD)
        with self.assertRaises(ValidationError):bootstrap_administrator(email='other@kp11.ru',full_name='Другой')
        self.assertEqual(AuditEntry.objects.filter(action='accounts.bootstrap_started').count(),1)

    def test_concurrent_resend_and_activation_consistent(self):
        admin=employee('admin@kp11.ru',admin=True)
        user,_=invite_employee(actor_id=admin.pk,auth_version=1,email='invite@kp11.ru',full_name='Приглашенный')
        token=latest_token()
        results=self.concurrent([lambda:activate_employee(token=token,password=PASSWORD),lambda:resend_invitation(actor_id=admin.pk,auth_version=1,user_id=user.pk)])
        user.refresh_from_db()
        self.assertEqual(results.count('ok'),1,results)
        if user.email_verified_at:
            self.assertEqual(AuditEntry.objects.filter(action='accounts.activated').count(),1)
        else:
            self.assertTrue(Invitation.objects.filter(user=user,revoked_at__isnull=False).exists())
            activate_employee(token=latest_token(),password=PASSWORD)

    def test_concurrent_system_role_disable_and_admin_disable_cannot_remove_all(self):
        admin=employee('admin@kp11.ru',admin=True)
        other=employee('other@kp11.ru',admin=True)
        role=Role.objects.get(is_system_admin=True)
        results=self.concurrent([
            lambda:save_role(actor_id=admin.pk,auth_version=1,role_id=role.pk,expected_version=role.version,name=role.name,is_active=False,permission_codes=list(PERMISSIONS)),
            lambda:edit_employee(actor_id=other.pk,auth_version=1,user_id=other.pk,expected_version=1,full_name=other.full_name,is_active=False,role_ids=[role.pk],group_ids=[]),
        ])
        self.assertEqual(sorted(results),['ValidationError','ok'])
        self.assertTrue(User.objects.filter(is_active=True,user_roles__role__is_system_admin=True,user_roles__role__is_active=True).exists())
