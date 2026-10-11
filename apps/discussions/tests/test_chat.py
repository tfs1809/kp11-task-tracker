import threading
import json
import uuid
from datetime import datetime, timezone as utc
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.http import Http404
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.access.administration import VersionConflict
from apps.access.models import Role, RolePermission, UserRole
from apps.accounts.limits import RateLimited
from apps.accounts.models import RateLimitBucket
from apps.accounts.tests.test_stage2 import employee, force_login
from apps.audiences.models import AudienceGroup, Membership
from apps.audit.models import AuditEntry
from apps.workitems.models import Assignment, MutationReceipt, WorkItem
from apps.workitems.tests.test_issuance import IssuanceFixture
from apps.workitems.workflow import archive_item, revoke_assignment, set_completion
from apps.discussions import selectors, services
from apps.discussions.models import EventMessage


class ChatFixture(IssuanceFixture):
    def prepare_chat(self):
        self.prepare()
        self.payload.update(kind='event', event_at='2026-10-12T15:00:00+00:00', subtasks=[])
        self.item, _ = self.issue()
        self.assignment = self.item.assignments.get(user=self.recipient)

    def send(self, actor=None, **overrides):
        actor = actor or self.recipient
        return services.send(**(dict(actor_id=actor.pk, auth_version=actor.auth_version, item_id=self.item.pk, request_key=uuid.uuid4(), text='Обычный текст') | overrides))

    def hide(self, message, actor=None, **overrides):
        actor = actor or self.admin
        return services.hide(**(dict(actor_id=actor.pk, auth_version=actor.auth_version, item_id=self.item.pk, message_id=message.pk, reason='Правила общения') | overrides))

    def close(self, actor=None, **overrides):
        actor = actor or self.manager
        return services.close(**(dict(actor_id=actor.pk, auth_version=actor.auth_version, item_id=self.item.pk, expected_version=self.item.version) | overrides))

    def privileged(self, codes):
        user = employee(f'{uuid.uuid4()}@kp11.ru')
        role = Role.objects.create(key=str(uuid.uuid4()), name=f'Роль {uuid.uuid4()}')
        UserRole.objects.create(user=user, role=role)
        for code in codes:
            RolePermission.objects.create(role=role, permission_id=code)
        return user

    def revoke(self):
        row = Assignment.objects.get(pk=self.assignment.pk)
        item = WorkItem.objects.get(pk=self.item.pk)
        return revoke_assignment(actor_id=self.manager.pk, auth_version=self.manager.auth_version, assignment_id=row.pk, expected_version=row.version, expected_item_version=item.version, reason='Отзыв доступа')

    def url(self, name='event_chat', **values):
        return reverse(name, kwargs={'item_id': self.item.pk, **values})

    def bulk_messages(self, count, start=0):
        # Fixtures still obey the service's atomic receipts/audit/guard rules;
        # quota is omitted solely to construct pagination-sized test histories.
        with patch('apps.discussions.services.consume_limits'):
            return [self.send(text=f'Сообщение {i}')[0] for i in range(start, start + count)]


class ChatTests(ChatFixture, TestCase):
    def setUp(self):
        self.prepare_chat()

    def test_author_assignee_and_completed_assignment_can_send(self):
        self.send(self.manager)
        set_completion(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version, assignment_id=self.assignment.pk, expected_version=1, expected_item_version=1, completed=True)
        self.send()
        self.assertEqual(EventMessage.objects.count(), 2)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.chat_closed_at)

    def test_global_view_is_read_only_and_send_permission_needs_read_basis(self):
        viewer = self.privileged(['workitems.view_all'])
        self.assertEqual(selectors.history(viewer, self.item.pk)['rows'], [])
        with self.assertRaises(PermissionDenied):
            self.send(viewer)
        inaccessible = self.privileged(['discussions.send_accessible', 'discussions.moderate'])
        with self.assertRaises(Http404):
            self.send(inaccessible)
        sender = self.privileged(['workitems.view_all', 'discussions.send_accessible'])
        message, _ = self.send(sender)
        with self.assertRaises(PermissionDenied):
            self.hide(message, sender)

    def test_moderation_and_sending_are_distinct_actions(self):
        moderator = self.privileged(['workitems.view_all', 'discussions.moderate'])
        with self.assertRaises(PermissionDenied):
            self.send(moderator)
        message, _ = self.send()
        self.hide(message, moderator)
        with self.assertRaises(PermissionDenied):
            selectors.original(moderator, self.item.pk, message.pk)
        with self.assertRaises(PermissionDenied):
            selectors.original(self.privileged(['workitems.view_all', 'audit.view']), self.item.pk, message.pk)

    def test_author_still_requires_current_read_basis(self):
        UserRole.objects.filter(user=self.manager).delete()
        with self.assertRaises(Http404):
            self.send(self.manager)

    def test_plain_text_and_current_author_groups_without_email(self):
        message, _ = self.send(text='<script>window.evil=1</script>\nВторая строка')
        self.login(self.recipient)
        response = self.client.get(self.url())
        self.assertContains(response, '&lt;script&gt;window.evil=1&lt;/script&gt;')
        self.assertNotContains(response, '<script>window.evil=1</script>')
        self.assertNotContains(response, self.recipient.email)
        Membership.objects.filter(user=self.recipient).delete()
        group = AudienceGroup.objects.create(name='Новая текущая группа')
        Membership.objects.create(user=self.recipient, group=group)
        data = selectors.poll(self.recipient, self.item.pk, message.last_change_id, str(message.pk))
        self.assertEqual(data['messages'], [])
        self.assertEqual(data['refresh'][0]['groups'], [group.name])
        group.is_active = False
        group.save()
        self.assertEqual(selectors.poll(self.recipient, self.item.pk, message.last_change_id, str(message.pk))['refresh'][0]['groups'], [])

    def test_invalid_text_and_keys_do_not_write_or_consume_limits(self):
        for text in ['', ' \n\t ', 'x' * 4001, '\x00', None]:
            with self.assertRaises(ValidationError):
                self.send(text=text)
        with self.assertRaises(ValidationError):
            self.send(request_key='invalid')
        self.assertEqual(EventMessage.objects.count(), 0)
        self.assertEqual(RateLimitBucket.objects.filter(scope__startswith='chat.').count(), 0)
        self.send(text='x' * 4000)

    def test_retry_conflict_and_receipt_atomicity(self):
        key = uuid.uuid4()
        first, created = self.send(request_key=key, text='  Повтор  ')
        repeated, created_again = self.send(request_key=key, text='Повтор')
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, repeated.pk)
        with self.assertRaises(VersionConflict):
            self.send(request_key=key, text='Другое сообщение')
        self.assertEqual(EventMessage.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action='discussions.sent').count(), 1)
        self.assertEqual(MutationReceipt.objects.filter(operation='discussions.send').count(), 1)
        self.assertEqual(list(RateLimitBucket.objects.filter(scope__startswith='chat.').values_list('count', flat=True)), [1, 1])

    def test_revoke_immediately_blocks_poll_read_send_and_retry(self):
        key = uuid.uuid4()
        message, _ = self.send(request_key=key)
        self.login(self.recipient)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.revoke()
        for name in ['event_chat', 'event_chat_changes']:
            self.assertEqual(self.client.get(self.url(name), HTTP_ACCEPT='application/json').status_code, 404)
        for key_value in [key, uuid.uuid4()]:
            response = self.client.post(self.url('event_chat_send'), {'request_key': key_value, 'text': message.text}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(message.text, response.content.decode())
        self.assertEqual(EventMessage.objects.count(), 1)

    def test_hidden_original_excluded_from_html_json_audit_and_replay(self):
        secret = 'Оригинал не должен попасть в обычный ответ'
        key = uuid.uuid4()
        message, _ = self.send(text=secret, request_key=key)
        watermark = message.last_change_id
        self.hide(message)
        self.login(self.recipient)
        for name in ['event_chat', 'event_chat_changes']:
            response = self.client.get(self.url(name), {'changes_after': watermark, 'visible': message.pk}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 200)
            body = json.dumps(response.json(), ensure_ascii=False) if name == 'event_chat_changes' else response.content.decode()
            self.assertNotIn(secret, body)
            self.assertIn('Правила общения', body)
        replay = self.client.post(self.url('event_chat_send'), {'request_key': key, 'text': secret}, HTTP_ACCEPT='application/json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()['message']['text'], '')
        self.assertNotIn(secret, str(list(AuditEntry.objects.values('changes', 'reason'))))
        self.assertEqual(self.client.get(self.url('event_message_original', message_id=message.pk)).status_code, 403)
        self.login(self.admin)
        self.assertContains(self.client.get(self.url('event_message_original', message_id=message.pk)), secret)

    def test_closed_read_only_including_admin_but_moderation_allowed(self):
        message, _ = self.send()
        self.close()
        for actor in [self.admin, self.manager, self.recipient]:
            self.assertTrue(selectors.history(actor, self.item.pk)['state']['closed'])
            with self.assertRaises(PermissionDenied):
                self.send(actor)
        self.hide(message)
        self.assertEqual(AuditEntry.objects.filter(action='discussions.closed').count(), 1)
        with self.assertRaises(PermissionDenied):
            self.close()

    def test_close_permissions_and_stale_version(self):
        for actor in [self.recipient, self.privileged(['workitems.view_all', 'discussions.moderate'])]:
            with self.assertRaises(PermissionDenied):
                self.close(actor)
        with self.assertRaises(VersionConflict):
            self.close(expected_version=99)
        editor = self.privileged(['workitems.view_all', 'workitems.edit_all'])
        self.close(editor)

    def test_archive_read_only_and_revoked_archive_not_readable(self):
        message, _ = self.send()
        archive_item(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=1)
        self.assertTrue(selectors.history(self.recipient, self.item.pk)['state']['archived'])
        for operation in [lambda: self.send(self.admin), lambda: self.hide(message), lambda: self.close(self.admin)]:
            with self.assertRaises(PermissionDenied):
                operation()
        # System deactivation revokes even archived assignments.
        self.edit(self.recipient, is_active=False)
        self.recipient.refresh_from_db()
        self.edit(self.recipient, is_active=True)
        self.recipient.refresh_from_db()
        with self.assertRaises(Http404):
            selectors.history(self.recipient, self.item.pk)

    def test_task_direct_routes_and_foreign_message_ids(self):
        task, _ = self.issue(payload=self.payload | {'kind': 'task', 'event_at': None}, request_key=uuid.uuid4())
        message, _ = self.send()
        self.login(self.admin)
        for name in ['event_chat', 'event_chat_changes']:
            self.assertEqual(self.client.get(reverse(name, kwargs={'item_id': task.pk})).status_code, 404)
        self.assertEqual(self.client.post(reverse('event_chat_send', kwargs={'item_id': task.pk}), {'request_key': uuid.uuid4(), 'text': 'Текст'}).status_code, 404)
        other, _ = self.issue(request_key=uuid.uuid4())
        with self.assertRaises(Http404):
            self.hide(message, item_id=other.pk)
        self.assertEqual(selectors.poll(self.admin, other.pk, 0, str(message.pk))['refresh'], [])

    def test_pagination_watermark_and_old_message_hiding(self):
        rows = self.bulk_messages(57)
        latest = selectors.history(self.recipient, self.item.pk)
        self.assertEqual([row['id'] for row in latest['rows']], [str(row.pk) for row in rows[7:]])
        older = selectors.history(self.recipient, self.item.pk, latest['before_id'])
        self.assertEqual([row['id'] for row in older['rows']], [str(row.pk) for row in rows[:7]])
        self.assertEqual(older['before_id'], '')
        watermark = latest['cursor']
        self.hide(rows[0])
        new = self.bulk_messages(1, 57)[0]
        changes = selectors.poll(self.recipient, self.item.pk, watermark)
        self.assertEqual([row['id'] for row in changes['messages']], [str(rows[0].pk), str(new.pk)])
        self.assertTrue(changes['messages'][0]['hidden'])

    def test_change_cursor_does_not_skip_page_boundary_or_rehidden_old_row(self):
        rows = self.bulk_messages(53)
        first = selectors.poll(self.recipient, self.item.pk, 0)
        self.assertTrue(first['more'])
        self.assertEqual(len(first['messages']), 50)
        self.assertEqual(first['cursor'], str(rows[49].last_change_id))
        self.hide(rows[0])
        second = selectors.poll(self.recipient, self.item.pk, first['cursor'])
        self.assertEqual([row['id'] for row in second['messages']], [str(row.pk) for row in rows[50:]] + [str(rows[0].pk)])
        self.assertTrue(second['messages'][-1]['hidden'])
        self.assertFalse(second['more'])

    def test_poll_cursor_validation_and_batch_bounds(self):
        self.login(self.recipient)
        for value in ['-1', '1e3', '9' * 30, '１２', 'bad']:
            self.assertEqual(self.client.get(self.url('event_chat_changes'), {'changes_after': value}).status_code, 400)
            self.assertEqual(self.client.get(self.url(), {'before_id': value}).status_code, 400)
        self.assertEqual(self.client.get(self.url('event_chat_changes'), {'visible': ','.join(['1'] * 51)}).status_code, 400)

    def test_common_per_event_and_total_rate_limit_with_retry_after(self):
        with patch('apps.accounts.limits.timezone.now', return_value=datetime(2026, 10, 11, 0, 0, 5, tzinfo=utc.utc)):
            keys = [uuid.uuid4() for _ in range(10)]
            for key in keys:
                self.send(request_key=key)
            self.send(request_key=keys[0])  # Replays do not consume another quota.
            self.login(self.recipient)
            response = self.client.post(self.url('event_chat_send'), {'request_key': uuid.uuid4(), 'text': 'Лишнее'}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response['Retry-After'], '55')
            for _ in range(2):
                other, _ = self.issue(request_key=uuid.uuid4())
                for _ in range(10):
                    self.send(item_id=other.pk)
            other, _ = self.issue(request_key=uuid.uuid4())
            with self.assertRaises(RateLimited):
                self.send(item_id=other.pk)
        self.assertEqual(EventMessage.objects.count(), 30)

    def test_audit_failure_rolls_back_send_receipt_limits_hide_and_close(self):
        with patch('apps.discussions.services.record_action', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.send()
        self.assertEqual(EventMessage.objects.count(), 0)
        self.assertEqual(MutationReceipt.objects.filter(operation='discussions.send').count(), 0)
        self.assertEqual(RateLimitBucket.objects.filter(scope__startswith='chat.').count(), 0)
        message, _ = self.send()
        with patch('apps.discussions.services.record_action', side_effect=RuntimeError('Audit unavailable')):
            for action in [lambda: self.hide(message), lambda: self.close()]:
                with self.assertRaises(RuntimeError):
                    action()
        message.refresh_from_db()
        self.item.refresh_from_db()
        self.assertIsNone(message.hidden_at)
        self.assertIsNone(self.item.chat_closed_at)

    def test_hide_reason_and_noop_audit(self):
        message, _ = self.send()
        for reason in ['', ' \n ', 'x' * 501]:
            with self.assertRaises(ValidationError):
                self.hide(message, reason=reason)
        self.hide(message)
        self.hide(message, reason='Другая причина не заменяет историю')
        self.assertEqual(AuditEntry.objects.filter(action='discussions.hidden').count(), 1)
        message.refresh_from_db()
        self.assertEqual(message.hidden_reason, 'Правила общения')

    def test_post_csrf_no_javascript_and_json_authentication(self):
        client = Client(enforce_csrf_checks=True)
        force_login(client, self.recipient)
        response = client.get(self.url())
        self.assertEqual(response.status_code, 200)
        payload = {'request_key': uuid.uuid4(), 'text': 'Без JavaScript'}
        self.assertEqual(client.post(self.url('event_chat_send'), payload).status_code, 403)
        payload['csrfmiddlewaretoken'] = client.cookies['csrftoken'].value
        self.assertEqual(client.post(self.url('event_chat_send'), payload).status_code, 302)
        self.assertContains(client.get(self.url()), 'Без JavaScript')
        unauth = Client().get(self.url('event_chat_changes'), HTTP_ACCEPT='application/json')
        self.assertEqual(unauth.status_code, 401)

    def test_open_session_role_revoke_and_deactivation(self):
        viewer = self.privileged(['workitems.view_all', 'discussions.send_accessible'])
        force_login(self.client, viewer)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        UserRole.objects.filter(user=viewer).delete()
        self.assertEqual(self.client.get(self.url('event_chat_changes'), HTTP_ACCEPT='application/json').status_code, 404)
        self.login(self.recipient)
        self.edit(self.recipient, is_active=False)
        self.assertEqual(self.client.get(self.url('event_chat_changes'), HTTP_ACCEPT='application/json').status_code, 401)

    def test_database_content_delete_and_close_are_immutable(self):
        message, _ = self.send()
        for operation in [lambda: EventMessage.objects.filter(pk=message.pk).update(text='Изменение'), lambda: EventMessage.objects.filter(pk=message.pk).delete()]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        self.close()
        with self.assertRaises(DatabaseError), transaction.atomic():
            WorkItem.objects.filter(pk=self.item.pk).update(chat_closed_at=None, chat_closed_by=None)

    def test_database_task_message_and_foreign_receipt_rejected(self):
        message, _ = self.send()
        task, _ = self.issue(payload=self.payload | {'kind': 'task', 'event_at': None}, request_key=uuid.uuid4())
        for values in [dict(work_item=task, author=self.recipient, text='Текст', last_change=message.last_change), dict(work_item=self.item, author=self.recipient, text='x' * 4001, last_change=message.last_change)]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                EventMessage.objects.create(**values)
        with self.assertRaises(DatabaseError), transaction.atomic():
            MutationReceipt.objects.create(actor=self.manager, operation='discussions.send', request_key=uuid.uuid4(), payload_digest='a' * 64, work_item=self.item, message=message)

    def test_runtime_message_delete_and_truncate_privileges_revoked(self):
        import os
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_table_privilege(%s, 'discussions_eventmessage', 'DELETE'), has_table_privilege(%s, 'discussions_eventmessage', 'TRUNCATE'), has_table_privilege(%s, 'discussions_eventmessage', 'UPDATE')", [os.environ['DB_APP_USER']] * 3)
            self.assertEqual(cursor.fetchone(), (False, False, True))


class ChatConcurrencyTests(ChatFixture, TransactionTestCase):
    def setUp(self):
        self.prepare_chat()

    def race(self, functions):
        barrier = threading.Barrier(len(functions))
        results, errors = [], []
        def worker(fn):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '12s'")
                barrier.wait(timeout=15)
                results.append(('ok', fn()))
            except (PermissionDenied, ValidationError, Http404, RateLimited) as exc:
                results.append(('denied', type(exc).__name__))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        threads = [threading.Thread(target=worker, args=(fn,)) for fn in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors, errors)
        return results

    def test_concurrent_retries_create_one_message_and_one_audit(self):
        key = uuid.uuid4()
        result = self.race([lambda: self.send(request_key=key) for _ in range(4)])
        self.assertEqual(sum(value[1] for status, value in result if status == 'ok'), 1)
        self.assertEqual(EventMessage.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action='discussions.sent').count(), 1)

    def test_shared_quota_across_workers(self):
        with patch('apps.accounts.limits.timezone.now', return_value=datetime(2026, 10, 11, 0, 0, 5, tzinfo=utc.utc)):
            result = self.race([lambda: self.send() for _ in range(12)])
        self.assertEqual(sum(status == 'ok' for status, _ in result), 10)
        self.assertEqual(EventMessage.objects.count(), 10)
        self.assertEqual(list(RateLimitBucket.objects.filter(scope__startswith='chat.').values_list('count', flat=True)), [10, 10])

    def test_send_vs_revoke_never_writes_after_committed_revoke(self):
        result = self.race([lambda: self.send(), lambda: self.revoke()])
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.state, 'revoked')
        with self.assertRaises(Http404):
            self.send()
        self.assertEqual(len(result), 2)
        sent = AuditEntry.objects.filter(action='discussions.sent').first()
        revoked = AuditEntry.objects.get(action='workitems.assignment_revoked')
        if sent:
            self.assertLess(sent.pk, revoked.pk)

    def test_send_vs_close_and_hide_vs_archive(self):
        result = self.race([lambda: self.send(), lambda: self.close()])
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.chat_closed_at)
        with self.assertRaises(PermissionDenied):
            self.send(self.admin)
        self.assertEqual(len(result), 2)
        # A separate open event tests moderation/archival ordering.
        self.item, _ = self.issue(request_key=uuid.uuid4())
        message, _ = self.send()
        self.race([lambda: self.hide(message), lambda: archive_item(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=1)])
        with self.assertRaises(PermissionDenied):
            self.hide(message)
        hidden = AuditEntry.objects.filter(action='discussions.hidden').first()
        archived = AuditEntry.objects.get(action='workitems.archived')
        if hidden:
            self.assertLess(hidden.pk, archived.pk)
