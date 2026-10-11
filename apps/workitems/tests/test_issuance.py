import threading
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.access.models import Role, RolePermission, UserRole
from apps.access.policies import has_permission
from apps.access.services import actor_transaction
from apps.accounts.administration import edit_employee
from apps.accounts.models import User
from apps.accounts.tests.test_stage2 import employee, force_login, seed
from apps.audiences.models import AudienceGroup, Membership
from apps.audit.models import AuditEntry
from apps.workitems.forms import IssueForm
from apps.workitems.models import Assignment, AssignmentSource, AudienceSelection, AudienceSelectionGroup, AudienceSelectionRole, AudienceSelectionUser, MutationReceipt, Subtask, WorkItem
from apps.workitems.selectors import readable_items, recipient_directory
from apps.workitems.services import IssueConflict, PreviewChanged, SALT, issue_item, preview_issue


class IssuanceFixture:
    def prepare(self):
        seed()
        self.admin = employee('admin@kp11.ru', admin=True)
        self.manager = employee('manager@kp11.ru')
        UserRole.objects.create(user=self.manager, role=Role.objects.get(key='manager'))
        self.recipient = employee('recipient@kp11.ru')
        self.recipient.full_name = 'Получатель пересечений'
        self.recipient.invited_at = timezone.now()
        self.recipient.save()
        self.roles = list(Role.objects.filter(key__in=['teacher', 'curator']).order_by('key'))
        self.groups = list(AudienceGroup.objects.all()[:2])
        for role in self.roles:
            UserRole.objects.create(user=self.recipient, role=role)
        for group in self.groups:
            Membership.objects.create(user=self.recipient, group=group)
        self.payload = dict(kind='task', title='Проверяемая выдача', description='Описание <script>evil()</script>', due_at='2026-10-12T20:59:59.999999+00:00', event_at=None, subtasks=['Первая подзадача', 'Вторая подзадача'], users=[str(self.recipient.pk)], roles=[str(role.pk) for role in self.roles], groups=[str(group.pk) for group in self.groups])
        self.key = uuid.uuid4()
        self.kwargs = dict(actor_id=self.manager.pk, auth_version=self.manager.auth_version, request_key=self.key, payload=self.payload)

    def preview(self, **overrides):
        return preview_issue(**{**self.kwargs, **overrides})

    def issue(self, preview=None, **overrides):
        preview = preview or self.preview(**overrides)
        return issue_item(**{**self.kwargs, **overrides}, preview_token=preview['token'])

    def login(self, user):
        force_login(self.client, user)

    def edit(self, user, **overrides):
        args = dict(actor_id=self.admin.pk, auth_version=self.admin.auth_version, user_id=user.pk, expected_version=user.version, full_name=user.full_name, is_active=user.is_active, role_ids=list(user.user_roles.values_list('role_id', flat=True)), group_ids=list(user.memberships.values_list('group_id', flat=True)))
        return edit_employee(**{**args, **overrides})


class IssuanceTests(IssuanceFixture, TestCase):
    def setUp(self):
        self.prepare()

    def test_union_deduplicates_and_preserves_all_five_reasons(self):
        preview = self.preview()
        self.assertEqual(len(preview['recipients']), 1)
        self.assertEqual(preview['recipients'][0]['name'], self.recipient.full_name)
        item, created = self.issue(preview)
        self.assertTrue(created)
        self.assertEqual(item.assignments.count(), 1)
        self.assertEqual(AssignmentSource.objects.count(), 5)
        self.assertEqual(AudienceSelectionUser.objects.count(), 1)
        self.assertEqual(AudienceSelectionRole.objects.count(), 2)
        self.assertEqual(AudienceSelectionGroup.objects.count(), 2)
        self.assertEqual(list(item.subtasks.values_list('position', 'text')), [(1, 'Первая подзадача'), (2, 'Вторая подзадача')])
        self.assertFalse(item.assignments.filter(user=self.manager).exists())
        self.assertEqual(AuditEntry.objects.filter(action='workitems.issued').count(), 1)
        self.assertEqual(MutationReceipt.objects.count(), 1)

    def test_role_and_group_union_includes_distinct_members(self):
        second = User.objects.create_user(email='pending@kp11.ru', full_name='Ожидает активации', invited_at=timezone.now())
        Membership.objects.create(user=second, group=self.groups[0])
        preview = self.preview()
        self.assertEqual({row['id'] for row in preview['recipients']}, {str(self.recipient.pk), str(second.pk)})
        item, _ = self.issue(preview)
        self.assertEqual(item.assignments.count(), 2)

    def test_uninvited_and_inactive_are_excluded(self):
        inactive = User.objects.create_user(email='inactive@kp11.ru', full_name='Отключенный', is_active=False, invited_at=timezone.now())
        unknown = User.objects.create_user(email='unknown@kp11.ru', full_name='Не приглашенный')
        for user in (inactive, unknown):
            Membership.objects.create(user=user, group=self.groups[0])
        self.assertEqual(len(self.preview()['recipients']), 1)
        for user in (inactive, unknown):
            payload = {**self.payload, 'users': [str(user.pk)]}
            with self.assertRaises(ValidationError):
                self.preview(payload=payload)

    def test_empty_recipients_and_inactive_sources_fail_without_writes(self):
        with self.assertRaises(ValidationError):
            self.preview(payload={**self.payload, 'users': [], 'roles': [], 'groups': []})
        self.groups[0].is_active = False
        self.groups[0].save()
        with self.assertRaises(ValidationError):
            self.preview()
        self.assertFalse(WorkItem.objects.exists())

    def test_membership_change_after_issuance_does_not_recalculate(self):
        item, _ = self.issue()
        original = item.assignments.get()
        self.edit(self.recipient, role_ids=[], group_ids=[])
        second = User.objects.create_user(email='second@kp11.ru', full_name='Новый член', invited_at=timezone.now())
        Membership.objects.create(user=second, group=self.groups[0])
        original_group_name = self.groups[0].name
        self.groups[0].name = 'Переименованная группа'
        self.groups[0].is_active = False
        self.groups[0].save()
        self.assertEqual(list(item.assignments.values_list('pk', flat=True)), [original.pk])
        self.assertEqual(AudienceSelectionGroup.objects.get(group=self.groups[0]).label_snapshot, original_group_name)
        self.login(self.recipient)
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[item.pk])).status_code, 200)

    def test_membership_change_before_confirmation_requires_new_preview(self):
        preview = self.preview()
        second = User.objects.create_user(email='second@kp11.ru', full_name='Новый получатель', invited_at=timezone.now())
        Membership.objects.create(user=second, group=self.groups[0])
        with self.assertRaises(PreviewChanged) as caught:
            self.issue(preview)
        self.assertEqual(len(caught.exception.preview['recipients']), 2)
        self.assertFalse(WorkItem.objects.exists())
        item, _ = self.issue(caught.exception.preview)
        self.assertEqual(item.assignments.count(), 2)

    def test_rename_is_visible_and_requires_reconfirmation(self):
        preview = self.preview()
        self.edit(self.recipient, full_name='Актуальное ФИО')
        with self.assertRaises(PreviewChanged) as caught:
            self.issue(preview)
        self.assertEqual(caught.exception.preview['recipients'][0]['name'], 'Актуальное ФИО')
        self.assertFalse(WorkItem.objects.exists())

    def test_expired_preview_requires_reconfirmation_but_receipt_can_replay(self):
        preview = self.preview()
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 601):
            with self.assertRaises(PreviewChanged):
                self.issue(preview)
        item, _ = self.issue(preview)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 601):
            replay, created = self.issue(preview)
        self.assertEqual(item.pk, replay.pk)
        self.assertFalse(created)

    def test_repeat_does_not_duplicate_or_change_assigned_at(self):
        preview = self.preview()
        item, _ = self.issue(preview)
        first = item.assignments.get().assigned_at
        self.edit(self.recipient, role_ids=[], group_ids=[])
        replay, created = self.issue(preview)
        self.assertFalse(created)
        self.assertEqual(replay.pk, item.pk)
        self.assertEqual(item.assignments.get().assigned_at, first)
        self.assertEqual(AssignmentSource.objects.count(), 5)
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action='workitems.issued').count(), 1)

    def test_same_key_different_payload_is_conflict(self):
        self.issue()
        with self.assertRaises(IssueConflict):
            self.issue(payload={**self.payload, 'title': 'Другой заголовок'})
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_token_tampering_actor_key_or_content_is_rejected(self):
        preview = self.preview()
        for overrides in [{'request_key': uuid.uuid4()}, {'actor_id': self.admin.pk, 'auth_version': self.admin.auth_version}, {'payload': {**self.payload, 'title': 'Подмена'}}]:
            with self.assertRaises(ValidationError):
                self.issue(preview, **overrides)
        with self.assertRaises(ValidationError):
            issue_item(**self.kwargs, preview_token=preview['token'] + 'x')
        self.assertFalse(WorkItem.objects.exists())

    def test_failure_rolls_back_every_entity_and_audit(self):
        preview = self.preview()
        with patch('apps.workitems.services.record_action', side_effect=RuntimeError('Test failure')):
            with self.assertRaises(RuntimeError):
                self.issue(preview)
        for model in (WorkItem, Assignment, Subtask, AudienceSelection, AssignmentSource, MutationReceipt):
            self.assertEqual(model.objects.count(), 0)
        self.assertFalse(AuditEntry.objects.filter(action='workitems.issued').exists())

    def test_replay_after_author_role_revoked_is_forbidden(self):
        preview = self.preview()
        self.issue(preview)
        self.edit(self.manager, role_ids=[])
        with self.assertRaises(PermissionDenied):
            self.issue(preview)
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_create_dependency_is_enforced_and_groups_do_not_grant_it(self):
        role = Role.objects.create(key='create-only', name='Только создание')
        RolePermission.objects.create(role=role, permission_id='workitems.create')
        UserRole.objects.create(user=self.recipient, role=role)
        with self.assertRaises(PermissionDenied):
            self.preview(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version)
        self.login(self.recipient)
        self.assertEqual(self.client.get(reverse('workitem_issue')).status_code, 403)
        self.assertNotContains(self.client.get('/'), 'Выдать задание')

    def test_author_cannot_read_others_and_executor_cannot_read_roster(self):
        item, _ = self.issue()
        self.login(self.recipient)
        response = self.client.get(reverse('workitem_detail', args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Все исполнители')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>evil()')
        self.assertEqual(self.client.get(reverse('workitem_roster', args=[item.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('workitems_issued')).status_code, 403)
        self.assertEqual(self.client.get(reverse('workitems_all')).status_code, 403)
        other, _ = self.issue(actor_id=self.admin.pk, auth_version=self.admin.auth_version, request_key=uuid.uuid4())
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[other.pk])).status_code, 404)
        self.assertContains(self.client.get(reverse('workitems_issued')), item.title)
        self.assertEqual(set(readable_items(self.manager).values_list('pk', flat=True)), {item.pk})

    def test_view_all_is_read_only_and_edit_all_does_not_open_objects(self):
        item, _ = self.issue()
        reader = Role.objects.create(key='reader', name='Читатель')
        RolePermission.objects.create(role=reader, permission_id='workitems.view_all')
        UserRole.objects.create(user=self.recipient, role=reader)
        self.assertTrue(has_permission(self.recipient, 'workitems.read_roster', item))
        self.assertFalse(has_permission(self.recipient, 'workitems.edit', item))
        UserRole.objects.filter(user=self.recipient, role=reader).delete()
        editor = Role.objects.create(key='editor', name='Редактор')
        RolePermission.objects.create(role=editor, permission_id='workitems.edit_all')
        UserRole.objects.create(user=self.manager, role=editor)
        other, _ = self.issue(actor_id=self.admin.pk, auth_version=self.admin.auth_version, request_key=uuid.uuid4())
        self.assertFalse(has_permission(self.manager, 'workitems.read', other))
        self.assertFalse(has_permission(self.manager, 'workitems.edit', other))
        self.login(self.admin)
        self.assertContains(self.client.get(reverse('workitems_all')), item.title)

    def test_own_assignment_survives_role_revocation_but_authorship_alone_does_not(self):
        self.manager.invited_at = timezone.now()
        self.manager.save()
        item, _ = self.issue(payload={**self.payload, 'users': [str(self.manager.pk)], 'roles': [], 'groups': []})
        self.edit(self.manager, role_ids=[])
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[item.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('workitem_roster', args=[item.pk])).status_code, 403)
        other, _ = self.issue(actor_id=self.admin.pk, auth_version=self.admin.auth_version, request_key=uuid.uuid4())
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[other.pk])).status_code, 404)

    def test_event_date_is_independent_of_due_and_task_has_no_chat(self):
        event_time = '2026-10-20T06:30:00+00:00'
        event, _ = self.issue(payload={**self.payload, 'kind': 'event', 'event_at': event_time})
        self.assertNotEqual(event.event_at, event.due_at)
        task, _ = self.issue(request_key=uuid.uuid4())
        self.assertIsNone(task.event_at)
        self.assertIsNone(task.chat_closed_at)
        self.login(self.admin)
        self.assertEqual(self.client.post(f'/workitems/{task.pk}/chat/', {'text': 'Недопустимый чат'}).status_code, 404)
        for payload in ({**self.payload, 'event_at': event_time}, {**self.payload, 'kind': 'event'}, {**self.payload, 'due_at': '2026-10-12T20:59:00'}):
            with self.assertRaises(ValidationError):
                self.preview(payload=payload)

    def test_date_only_moscow_to_utc_and_event_local_time(self):
        with actor_transaction(self.manager.pk) as actor:
            directory = recipient_directory(actor)
        form = IssueForm({'kind': 'event', 'title': 'Событие', 'due_date': '2026-10-12', 'event_at': '2026-10-20T09:30', 'users': [str(self.recipient.pk)], 'request_key': str(self.key)}, directory=directory)
        self.assertTrue(form.is_valid(), form.errors)
        payload = form.payload()
        self.assertEqual(payload['due_at'], '2026-10-12T20:59:59.999999+00:00')
        self.assertEqual(payload['event_at'], '2026-10-20T06:30:00+00:00')
        item, _ = self.issue(payload=payload)
        self.login(self.manager)
        self.assertContains(self.client.get(reverse('workitem_detail', args=[item.pk])), '12.10.2026 23:59:59')
        self.assertContains(self.client.get(reverse('workitem_detail', args=[item.pk])), '20.10.2026 09:30')

    def test_directory_and_preview_never_expose_staff_email(self):
        self.login(self.manager)
        response = self.client.get(reverse('workitem_issue'))
        self.assertContains(response, self.recipient.full_name)
        self.assertNotContains(response, self.recipient.email)
        self.assertNotContains(response, self.admin.email)
        response = self.client.post(reverse('workitem_issue'), {'kind': 'task', 'title': 'Выдача HTTP', 'due_date': '2026-10-12', 'users': [str(self.recipient.pk)], 'request_key': str(self.key)})
        self.assertContains(response, 'Получатели: 1')
        self.assertNotContains(response, self.recipient.email)
        self.assertFalse(WorkItem.objects.exists())
        form = response.context['form']
        confirm = dict(preview=form.initial['preview'], request_key=str(self.key))
        created = self.client.post(reverse('workitem_confirm'), confirm)
        self.assertEqual(created.status_code, 302)
        self.assertEqual(self.client.post(reverse('workitem_confirm'), confirm).url, created.url)
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertNotContains(self.client.get(created.url + 'assignees/'), self.recipient.email)

    def test_http_changed_preview_conflict_and_other_payload_409(self):
        self.login(self.manager)
        preview = self.preview()
        self.edit(self.recipient, full_name='Новое ФИО')
        response = self.client.post(reverse('workitem_confirm'), {'preview': preview['token'], 'request_key': str(self.key)})
        self.assertContains(response, 'Новое ФИО', status_code=409)
        new_token = response.context['form'].initial['preview']
        self.assertFalse(WorkItem.objects.exists())
        self.assertEqual(self.client.post(reverse('workitem_confirm'), {'preview': new_token, 'request_key': str(self.key)}).status_code, 302)
        different = self.preview(payload={**self.payload, 'title': 'Другое содержимое'})
        self.assertEqual(self.client.post(reverse('workitem_confirm'), {'preview': different['token'], 'request_key': str(self.key)}).status_code, 409)

    def test_csrf_and_get_cannot_issue(self):
        client = Client(enforce_csrf_checks=True)
        force_login(client, self.manager)
        self.assertEqual(client.get(reverse('workitem_confirm')).status_code, 405)
        self.assertEqual(client.post(reverse('workitem_issue'), {}).status_code, 403)
        self.assertEqual(client.post(reverse('workitem_confirm'), {}).status_code, 403)
        self.assertFalse(WorkItem.objects.exists())

    def test_db_immutable_identity_and_history(self):
        item, _ = self.issue()
        for values in [{'author': self.admin}, {'kind': 'event', 'event_at': timezone.now()}]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                WorkItem.objects.filter(pk=item.pk).update(**values)
        for model in (AudienceSelection, AudienceSelectionUser, AudienceSelectionRole, AudienceSelectionGroup, AssignmentSource, MutationReceipt):
            with self.assertRaises(DatabaseError), transaction.atomic():
                model.objects.all().delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            Assignment.objects.update(user=self.admin)
        with self.assertRaises(DatabaseError), transaction.atomic():
            MutationReceipt.objects.update(payload_digest='0' * 64)

    def test_db_uniques_event_checks_and_source_cross_item(self):
        item, _ = self.issue()
        assignment = item.assignments.get()
        with self.assertRaises(DatabaseError), transaction.atomic():
            Assignment.objects.create(work_item=item, user=self.recipient, assigned_by=self.manager)
        for values in [{'event_at': timezone.now()}, {'chat_closed_at': timezone.now(), 'chat_closed_by': self.admin}, {'kind': 'wrong'}, {'version': 0}]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                WorkItem.objects.filter(pk=item.pk).update(**values)
        for values in [{'state': 'revoked'}, {'completed_at': timezone.now()}, {'state': 'other'}]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                Assignment.objects.filter(pk=assignment.pk).update(**values)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Subtask.objects.create(work_item=item, position=1, text='Дубликат')
        other, _ = self.issue(request_key=uuid.uuid4())
        foreign_source = AudienceSelectionUser.objects.get(selection__work_item=other)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AssignmentSource.objects.create(assignment=assignment, batch_id=foreign_source.selection.batch_id, selected_user=foreign_source)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AssignmentSource.objects.create(assignment=assignment, batch_id=uuid.uuid4())

    def test_deactivated_recipient_cannot_read_existing_item(self):
        item, _ = self.issue()
        self.login(self.recipient)
        self.edit(self.recipient, is_active=False)
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[item.pk])).status_code, 302)
        self.assertFalse(readable_items(self.manager).filter(pk=uuid.uuid4()).exists())


class IssuanceConcurrencyTests(IssuanceFixture, TransactionTestCase):
    def setUp(self):
        self.prepare()

    def race(self, functions):
        barrier = threading.Barrier(len(functions))
        results, errors = [], []
        def run(fn):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '8s'")
                barrier.wait(timeout=10)
                results.append(('ok', fn()))
            except (ValidationError, PermissionDenied) as exc:
                results.append(('denied', exc))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        threads = [threading.Thread(target=run, args=(fn,)) for fn in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        self.assertFalse(any(thread.is_alive() for thread in threads), 'Worker timed out')
        self.assertFalse(errors, errors)
        return results

    def test_simultaneous_same_key_creates_exactly_one_item(self):
        preview = self.preview()
        result = self.race([lambda: self.issue(preview), lambda: self.issue(preview)])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(status == 'ok' for status, _ in result))
        self.assertEqual(sorted(value[1] for _, value in result), [False, True])
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertEqual(Assignment.objects.count(), 1)
        self.assertEqual(AssignmentSource.objects.count(), 5)
        self.assertEqual(MutationReceipt.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action='workitems.issued').count(), 1)

    def test_simultaneous_different_payload_same_key_one_conflict(self):
        first = self.preview()
        other_payload = {**self.payload, 'title': 'Конкурентный вариант'}
        second = self.preview(payload=other_payload)
        results = self.race([lambda: self.issue(first), lambda: self.issue(second, payload=other_payload)])
        self.assertEqual(sorted(status for status, _ in results), ['denied', 'ok'])
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertTrue(any(isinstance(value, IssueConflict) for status, value in results if status == 'denied'))

    def test_issuance_vs_membership_change_is_serialized(self):
        preview = self.preview()
        new_user = User.objects.create_user(email='new@kp11.ru', full_name='Новый адресат', invited_at=timezone.now())
        results = self.race([lambda: self.issue(preview), lambda: self.edit(new_user, group_ids=[self.groups[0].pk])])
        self.assertEqual(len(results), 2)
        if WorkItem.objects.exists():
            self.assertEqual(Assignment.objects.count(), 1)
            self.assertEqual(AuditEntry.objects.filter(action='workitems.issued').count(), 1)
        else:
            self.assertTrue(any(isinstance(value, PreviewChanged) for status, value in results if status == 'denied'))
            self.assertEqual(MutationReceipt.objects.count(), 0)

    def test_issuance_vs_author_permission_revocation_is_serialized(self):
        preview = self.preview()
        self.race([lambda: self.issue(preview), lambda: self.edit(self.manager, role_ids=[])])
        self.assertEqual(WorkItem.objects.count(), AuditEntry.objects.filter(action='workitems.issued').count())
        self.assertLessEqual(WorkItem.objects.count(), 1)
        with self.assertRaises(PermissionDenied):
            self.issue(preview)

    def test_http_concurrent_confirm_posts_return_same_location(self):
        preview = self.preview()
        clients = [Client(enforce_csrf_checks=True), Client(enforce_csrf_checks=True)]
        data = []
        for client in clients:
            force_login(client, self.manager)
            self.assertEqual(client.get(reverse('workitem_issue')).status_code, 200)
            data.append({'preview': preview['token'], 'request_key': str(self.key), 'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        results = self.race([lambda i=i: clients[i].post(reverse('workitem_confirm'), data[i]) for i in range(2)])
        self.assertTrue(all(status == 'ok' for status, _ in results))
        self.assertEqual([response.status_code for _, response in results], [302, 302])
        self.assertEqual(len({response.url for _, response in results}), 1)
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertEqual(Assignment.objects.count(), 1)
