from copy import deepcopy
from unittest.mock import patch
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.access.administration import VersionConflict
from apps.access.models import Role, RolePermission, UserRole
from apps.accounts.models import User
from apps.accounts.tests.test_stage2 import force_login
from apps.audit.models import AuditEntry
from apps.workitems.models import Assignment, Subtask, SubtaskProgress, WorkItem
from apps.workitems.selectors import item_detail, item_history, statistics
from apps.workitems.workflow import ConsequencesChanged, apply_additions, apply_edit, archive_item, content_snapshot, preview_additions, preview_edit, revoke_assignment, set_completion, set_mark
from .test_issuance import IssuanceFixture


class WorkflowFixture(IssuanceFixture):
    def prepare_workflow(self):
        self.prepare()
        self.item, _ = self.issue()
        self.assignment = self.item.assignments.get()
        self.subtasks = list(self.item.subtasks.all())

    def mark_as(self, assignment, subtask, value=True, **overrides):
        assignment.refresh_from_db()
        assignment.work_item.refresh_from_db()
        progress = assignment.progress.filter(subtask=subtask).first()
        args = dict(actor_id=assignment.user_id, auth_version=assignment.user.auth_version, assignment_id=assignment.pk, subtask_id=subtask.pk, expected_version=assignment.version, expected_item_version=assignment.work_item.version, expected_progress_version=progress.version if progress else 1, value=value)
        return set_mark(**(args | overrides))

    def complete_as(self, assignment, completed=True, **overrides):
        assignment.refresh_from_db()
        assignment.work_item.refresh_from_db()
        args = dict(actor_id=assignment.user_id, auth_version=assignment.user.auth_version, assignment_id=assignment.pk, expected_version=assignment.version, expected_item_version=assignment.work_item.version, completed=completed)
        return set_completion(**(args | overrides))

    def finish(self, assignment=None):
        assignment = assignment or self.assignment
        for subtask in assignment.work_item.subtasks.filter(deleted_at__isnull=True):
            self.mark_as(assignment, subtask)
        self.complete_as(assignment)
        assignment.refresh_from_db()

    def draft(self):
        self.item.refresh_from_db()
        snapshot = content_snapshot(self.item)
        return {key: value for key, value in snapshot.items() if key != 'subtasks'} | {'subtasks': [{'id': row['id'], 'text': row['text']} for row in snapshot['subtasks']]}

    def edit_preview(self, payload, **overrides):
        self.item.refresh_from_db()
        args = dict(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=self.item.version, payload=payload)
        return preview_edit(**(args | overrides))

    def change(self, payload):
        preview = self.edit_preview(payload)
        result = apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=preview['token'])
        self.item.refresh_from_db()
        return result

    def revoke_one(self, assignment=None):
        assignment = assignment or self.assignment
        assignment.refresh_from_db()
        self.item.refresh_from_db()
        return revoke_assignment(actor_id=self.manager.pk, auth_version=self.manager.auth_version, assignment_id=assignment.pk, expected_version=assignment.version, expected_item_version=self.item.version, reason='Тестовый отзыв')

    def add_preview(self, user):
        self.item.refresh_from_db()
        return preview_additions(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=self.item.version, payload={'users': [str(user.pk)], 'roles': [], 'groups': []})

    def add_user(self, user):
        preview = self.add_preview(user)
        apply_additions(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=preview['token'])
        return self.item.assignments.get(user=user)

    def second(self, suffix='second'):
        user = User.objects.create_user(email=f'{suffix}@kp11.ru', full_name='Другой тестовый исполнитель', invited_at=timezone.now(), email_verified_at=timezone.now())
        return self.add_user(user)


class WorkflowTests(WorkflowFixture, TestCase):
    def setUp(self):
        self.prepare_workflow()

    def test_personal_marks_independent_and_partial_cannot_complete(self):
        second = self.second()
        self.mark_as(self.assignment, self.subtasks[0])
        self.assertIsNone(second.progress.get(subtask=self.subtasks[0]).checked_at)
        with self.assertRaises(ValidationError):
            self.complete_as(self.assignment)
        self.assertEqual(statistics(self.item)['completed'], 0)
        self.mark_as(self.assignment, self.subtasks[1])
        self.complete_as(self.assignment)
        self.assertEqual(statistics(self.item), {'total': 2, 'completed': 1, 'percent': 50})

    def test_no_subtasks_complete_immediately_and_overdue_allowed(self):
        self.change(self.draft() | {'subtasks': [], 'due_at': '2020-01-01T00:00:00+00:00'})
        self.assertTrue(item_detail(self.recipient, self.item.pk)['overdue'])
        self.complete_as(self.assignment)
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.completed_at)
        self.assertEqual(self.assignment.completion_epoch, self.item.progress_epoch)

    def test_return_keeps_marks_and_completion_history(self):
        self.finish()
        previous = self.assignment.completed_at
        with self.assertRaises(ValidationError):
            self.mark_as(self.assignment, self.subtasks[0], False)
        self.complete_as(self.assignment, False)
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.completed_at)
        self.assertTrue(all(row.checked_at for row in self.assignment.progress.all()))
        self.assertEqual(AuditEntry.objects.get(action='workitems.returned').changes['previous_completed_at'], previous.isoformat())
        self.assertEqual(AuditEntry.objects.filter(action='workitems.completed').count(), 1)
        self.complete_as(self.assignment)
        self.assertEqual(AuditEntry.objects.filter(action='workitems.completed').count(), 2)

    def test_noop_mark_completion_and_edit_do_not_duplicate_audit(self):
        self.mark_as(self.assignment, self.subtasks[0])
        count = AuditEntry.objects.count()
        self.mark_as(self.assignment, self.subtasks[0])
        self.assertEqual(AuditEntry.objects.count(), count)
        self.finish()
        count = AuditEntry.objects.count()
        self.complete_as(self.assignment)
        self.change(self.draft())
        self.assertEqual(AuditEntry.objects.count(), count)

    def test_title_due_and_reorder_preserve_completion_and_ids(self):
        self.finish()
        completed = self.assignment.completed_at
        draft = self.draft()
        draft.update(title='Новое название', description='Новое описание', due_at='2026-11-01T20:59:59.999999+00:00', subtasks=list(reversed(draft['subtasks'])))
        preview = self.edit_preview(draft)
        self.assertFalse(preview['impact']['reset'])
        self.change(draft)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.completed_at, completed)
        self.assertEqual(self.item.progress_epoch, 1)
        self.assertEqual(list(self.item.subtasks.values_list('pk', flat=True)), [self.subtasks[1].pk, self.subtasks[0].pk])
        self.assertTrue(all(row.checked_at for row in self.assignment.progress.all()))

    def test_changed_text_resets_only_changed_marks_and_active_completions(self):
        second = self.second()
        self.finish()
        self.finish(second)
        self.revoke_one(second)
        old_progress = second.progress.get(subtask=self.subtasks[0]).checked_at
        draft = self.draft()
        draft['subtasks'][0]['text'] = 'Новая редакция текста'
        preview = self.edit_preview(draft)
        self.assertEqual(preview['impact']['completed_count'], 1)
        self.assertEqual(len(preview['impact']['affected']), 1)
        self.change(draft)
        self.assignment.refresh_from_db()
        second.refresh_from_db()
        self.subtasks[0].refresh_from_db()
        self.assertIsNone(self.assignment.completed_at)
        self.assertIsNone(self.assignment.progress.get(subtask=self.subtasks[0]).checked_at)
        self.assertIsNotNone(self.assignment.progress.get(subtask=self.subtasks[1]).checked_at)
        self.assertEqual(second.progress.get(subtask=self.subtasks[0]).checked_at, old_progress)
        self.assertIsNotNone(second.completed_at)
        self.assertEqual(self.subtasks[0].content_revision, 2)
        self.assertEqual(self.item.progress_epoch, 2)

    def test_new_subtask_resets_completion_without_losing_existing_marks(self):
        self.finish()
        draft = self.draft()
        draft['subtasks'].append({'id': None, 'text': 'Новая обязательная подзадача'})
        self.change(draft)
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.completed_at)
        self.assertEqual(self.assignment.progress.filter(checked_at__isnull=False).count(), 2)
        self.assertEqual(self.assignment.progress.filter(checked_at__isnull=True).count(), 1)
        with self.assertRaises(ValidationError):
            self.complete_as(self.assignment)

    def test_soft_delete_keeps_completion_and_never_auto_completes(self):
        second = self.second()
        self.finish()
        self.mark_as(second, self.subtasks[1])
        before = self.assignment.completed_at
        self.change(self.draft() | {'subtasks': [self.draft()['subtasks'][1]]})
        self.assignment.refresh_from_db()
        second.refresh_from_db()
        self.subtasks[0].refresh_from_db()
        self.assertIsNotNone(self.subtasks[0].deleted_at)
        self.assertEqual(self.assignment.completed_at, before)
        self.assertIsNone(second.completed_at)
        self.assertEqual(SubtaskProgress.objects.filter(subtask=self.subtasks[0]).count(), 2)
        self.assertEqual(self.item.progress_epoch, 1)
        self.assertIn(str(self.subtasks[0].pk), str(AuditEntry.objects.get(action='workitems.edited').changes['before']))

    def test_changed_completions_after_preview_require_new_confirmation(self):
        draft = self.draft()
        draft['subtasks'].append({'id': None, 'text': 'Новый пункт'})
        preview = self.edit_preview(draft)
        self.finish()
        with self.assertRaises(ConsequencesChanged) as caught:
            apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=preview['token'])
        self.assertEqual(caught.exception.preview['impact']['completed_count'], 1)
        self.assertEqual(self.item.subtasks.count(), 2)
        apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=caught.exception.preview['token'])
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.completed_at)

    def test_item_version_conflict_does_not_overwrite_content(self):
        draft = self.draft() | {'title': 'Устаревший ввод'}
        old = self.edit_preview(draft)
        self.change(self.draft() | {'title': 'Актуальный ввод'})
        with self.assertRaises(VersionConflict):
            apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=old['token'])
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, 'Актуальный ввод')
        force_login(self.client, self.manager)
        response = self.client.post(reverse('workitem_workflow_confirm', args=[self.item.pk]), {'token': old['token']})
        self.assertContains(response, 'Устаревший ввод', status_code=409)
        self.assertContains(response, 'Ввод сохранен', status_code=409)

    def test_stale_mark_and_progress_versions_and_foreign_subtask_fail(self):
        self.mark_as(self.assignment, self.subtasks[0])
        with self.assertRaises(VersionConflict):
            self.mark_as(self.assignment, self.subtasks[0], False, expected_version=1)
        with self.assertRaises(VersionConflict):
            self.mark_as(self.assignment, self.subtasks[0], False, expected_progress_version=1)
        other, _ = self.issue(request_key=uuid.uuid4())
        from django.http import Http404
        with self.assertRaises(Http404):
            self.mark_as(self.assignment, other.subtasks.first())
        self.change(self.draft() | {'title': 'Изменение версии'})
        with self.assertRaises(VersionConflict):
            self.mark_as(self.assignment, self.subtasks[0], False, expected_item_version=1)

    def test_revoke_immediately_denies_and_restore_preserves_compatible_completion(self):
        self.finish()
        first_date = self.assignment.first_assigned_at
        self.revoke_one()
        force_login(self.client, self.recipient)
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[self.item.pk])).status_code, 404)
        self.assertEqual(statistics(self.item), {'total': 0, 'completed': 0, 'percent': None})
        preview = self.add_preview(self.recipient)
        prior = preview['impact']['recipients'][0]['previous']
        self.assertTrue(prior['restores_completed'])
        self.assertEqual(prior['checked_count'], 2)
        restored = self.add_user(self.recipient)
        self.assertEqual(restored.pk, self.assignment.pk)
        self.assertEqual(restored.first_assigned_at, first_date)
        self.assertIsNotNone(restored.completed_at)
        self.assertEqual(statistics(self.item)['percent'], 100)
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[self.item.pk])).status_code, 200)

    def test_restore_incompatible_revision_does_not_restore_old_completion(self):
        self.finish()
        self.revoke_one()
        draft = self.draft()
        draft['subtasks'][0]['text'] = 'Новый текст после отзыва'
        draft['subtasks'].append({'id': None, 'text': 'Новая подзадача после отзыва'})
        self.change(draft)
        preview = self.add_preview(self.recipient)
        prior = preview['impact']['recipients'][0]['previous']
        self.assertFalse(prior['restores_completed'])
        self.assertEqual(prior['checked_count'], 1)
        restored = self.add_user(self.recipient)
        self.assertIsNone(restored.completed_at)
        self.assertEqual(restored.progress.filter(checked_at__isnull=False).count(), 1)
        self.assertEqual(restored.progress.count(), 3)
        self.assertEqual(AuditEntry.objects.filter(action='workitems.completed').count(), 1)

    def test_new_assignee_empty_progress_and_existing_assignee_noop(self):
        self.finish()
        second = self.second()
        self.assertEqual(second.progress.count(), 2)
        self.assertFalse(second.progress.filter(checked_at__isnull=False).exists())
        count = AuditEntry.objects.count()
        self.add_user(self.recipient)
        self.assertEqual(AuditEntry.objects.count(), count)
        self.assertEqual(self.item.assignments.count(), 2)

    def test_statistics_three_of_five_and_excludes_revoked_inactive(self):
        assignments = [self.assignment] + [self.second(f'worker{i}') for i in range(4)]
        for row in assignments[:3]:
            self.finish(row)
        self.assertEqual(statistics(self.item), {'total': 5, 'completed': 3, 'percent': 60})
        self.revoke_one(assignments[3])
        self.assertEqual(statistics(self.item)['total'], 4)
        disabled = assignments[4].user
        self.edit(disabled, is_active=False)
        self.assertEqual(statistics(self.item), {'total': 3, 'completed': 3, 'percent': 100})

    def test_deactivation_revokes_even_archive_and_reactivation_does_not_restore(self):
        archive_item(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=self.item.version)
        user = self.edit(self.recipient, is_active=False)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.state, 'revoked')
        self.assertEqual(AuditEntry.objects.filter(action='workitems.assignment_revoked').count(), 1)
        self.edit(user, is_active=True)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.state, 'revoked')
        self.recipient.refresh_from_db()
        force_login(self.client, self.recipient)
        self.assertEqual(self.client.get(reverse('workitem_detail', args=[self.item.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('workitems_archive')), self.item.title)

    def test_archive_allows_current_reads_but_blocks_every_mutation(self):
        self.finish()
        draft = self.draft()
        token = self.edit_preview(draft | {'title': 'После архива'})['token']
        add_token = self.add_preview(self.recipient)['token']
        archive_item(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=self.item.version)
        force_login(self.client, self.recipient)
        self.assertContains(self.client.get(reverse('workitem_detail', args=[self.item.pk])), 'Архив — только чтение')
        self.assertNotContains(self.client.get('/'), self.item.title)
        self.assertContains(self.client.get(reverse('workitems_archive')), self.item.title)
        for operation in [lambda: self.complete_as(self.assignment, False), lambda: self.mark_as(self.assignment, self.subtasks[0], False), lambda: self.revoke_one(), lambda: apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=token), lambda: apply_additions(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=add_token)]:
            with self.assertRaises(PermissionDenied):
                operation()
        with self.assertRaises(DatabaseError), transaction.atomic():
            WorkItem.objects.filter(pk=self.item.pk).update(archived_at=None, archived_by=None)

    def test_author_and_admin_cannot_mark_for_another_employee(self):
        for actor in [self.manager, self.admin]:
            force_login(self.client, actor)
            url = reverse('assignment_completion', args=[self.assignment.pk])
            response = self.client.post(url, {'version': 1, 'item_version': self.item.version, 'completed': 'true', 'actor_id': str(self.recipient.pk)})
            self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.assignment.completed_at)

    def test_view_all_cannot_mutate_and_edit_all_requires_reading_basis(self):
        user = User.objects.create_user(email='reader@kp11.ru', full_name='Тестовый читатель', email_verified_at=timezone.now())
        role = Role.objects.create(key='read-all', name='Чтение всех')
        RolePermission.objects.create(role=role, permission_id='workitems.view_all')
        UserRole.objects.create(user=user, role=role)
        force_login(self.client, user)
        self.assertEqual(self.client.get(reverse('workitem_edit', args=[self.item.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('workitem_add', args=[self.item.pk])).status_code, 403)
        RolePermission.objects.filter(role=role).delete()
        RolePermission.objects.create(role=role, permission_id='workitems.edit_all')
        self.assertEqual(self.client.get(reverse('workitem_edit', args=[self.item.pk])).status_code, 404)

    def test_role_revocation_blocks_pending_edit_and_add_confirmations(self):
        edit_token = self.edit_preview(self.draft() | {'title': 'Нельзя сохранять'})['token']
        add_token = self.add_preview(self.recipient)['token']
        self.edit(self.manager, role_ids=[])
        from django.http import Http404
        for operation in [lambda: apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=edit_token), lambda: apply_additions(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=add_token)]:
            with self.assertRaises(Http404):
                operation()
        self.item.refresh_from_db()
        self.assertNotEqual(self.item.title, 'Нельзя сохранять')

    def test_history_is_object_limited_and_executor_does_not_get_others_marks(self):
        second = self.second()
        self.mark_as(second, self.subtasks[0])
        own_history = item_history(self.recipient, self.item.pk)
        self.assertFalse(any(entry['action'] == 'Отметка подзадачи изменена' for entry in own_history['entries']))
        self.assertNotIn(str(second.pk), str(own_history['entries']))
        force_login(self.client, self.recipient)
        self.assertEqual(self.client.get(reverse('workitem_assignee', args=[self.item.pk, second.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('workitem_roster', args=[self.item.pk]) + '?history=1').status_code, 403)
        force_login(self.client, self.manager)
        self.assertContains(self.client.get(reverse('workitem_assignee', args=[self.item.pk, second.pk])), 'отмечено')

    def test_progress_db_composite_foreign_keys_and_checks(self):
        other, _ = self.issue(request_key=uuid.uuid4())
        for values in [{'work_item': other, 'assignment': self.assignment, 'subtask': self.subtasks[0]}, {'work_item': self.item, 'assignment': self.assignment, 'subtask': other.subtasks.first()}]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                SubtaskProgress.objects.create(**values)
        row = self.assignment.progress.first()
        for values in [{'checked_at': timezone.now()}, {'version': 0}]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                SubtaskProgress.objects.filter(pk=row.pk).update(**values)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Subtask.objects.filter(pk=self.subtasks[0].pk).update(work_item=other)

    def test_edit_atomic_rollback_and_confirmation_signature(self):
        draft = self.draft()
        draft['subtasks'].append({'id': None, 'text': 'Добавление'})
        self.finish()
        preview = self.edit_preview(draft)
        with self.assertRaises(ValidationError):
            apply_edit(actor_id=self.admin.pk, auth_version=self.admin.auth_version, item_id=self.item.pk, token=preview['token'])
        with patch('apps.workitems.workflow.record_action', side_effect=RuntimeError('Test rollback')):
            with self.assertRaises(RuntimeError):
                apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=preview['token'])
        self.assignment.refresh_from_db()
        self.item.refresh_from_db()
        self.assertIsNotNone(self.assignment.completed_at)
        self.assertEqual(self.item.subtasks.count(), 2)
        self.assertEqual(self.item.progress_epoch, 1)

    def test_http_forms_edit_progress_and_csrf(self):
        force_login(self.client, self.manager)
        edit_url = reverse('workitem_edit', args=[self.item.pk])
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        data = {'title': 'Через HTTP', 'description': 'Описание', 'due_date': '2026-10-12', 'version': self.item.version, 'subtasks-TOTAL_FORMS': 2, 'subtasks-INITIAL_FORMS': 2, 'subtasks-MIN_NUM_FORMS': 0, 'subtasks-MAX_NUM_FORMS': 50}
        for index, subtask in enumerate(self.subtasks):
            data.update({f'subtasks-{index}-id': str(subtask.pk), f'subtasks-{index}-text': subtask.text, f'subtasks-{index}-position': index + 1})
        response = self.client.post(edit_url, data)
        self.assertContains(response, 'Подтверждение изменений')
        token = response.context['form'].initial['token']
        self.assertEqual(self.client.post(reverse('workitem_workflow_confirm', args=[self.item.pk]), {'token': token}).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, 'Через HTTP')
        client = Client(enforce_csrf_checks=True)
        force_login(client, self.recipient)
        url = reverse('assignment_mark', args=[self.assignment.pk, self.subtasks[0].pk])
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url, {}).status_code, 403)
        client.get(reverse('workitem_detail', args=[self.item.pk]))
        response = client.post(url, {'csrfmiddlewaretoken': client.cookies['csrftoken'].value, 'version': 1, 'item_version': self.item.version, 'progress_version': 1, 'value': 'true'})
        self.assertEqual(response.status_code, 302)

    def test_legacy_deactivation_migration_revokes_and_keeps_history(self):
        import importlib
        from django.apps import apps
        self.recipient.is_active = False
        self.recipient.save(update_fields=['is_active'])
        AuditEntry.objects.create(actor=self.admin, actor_label=self.admin.full_name, action='accounts.employee_changed', object_type='user', object_id=str(self.recipient.pk), changes={'before': {'is_active': True}, 'after': {'is_active': False}})
        importlib.import_module('apps.workitems.migrations.0005_revoke_legacy_inactive').revoke_legacy(apps, None)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.state, 'revoked')
        self.assertEqual(self.assignment.revoked_by_id, self.admin.pk)
        self.assertEqual(self.assignment.progress.count(), 2)
        self.assertEqual(AuditEntry.objects.filter(action='workitems.assignment_revoked', actor__isnull=True).count(), 1)

    def test_legacy_migration_does_not_invent_deactivation_actor(self):
        import importlib
        from django.apps import apps
        self.recipient.is_active = False
        self.recipient.save(update_fields=['is_active'])
        with self.assertRaises(RuntimeError), transaction.atomic():
            importlib.import_module('apps.workitems.migrations.0005_revoke_legacy_inactive').revoke_legacy(apps, None)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.state, 'active')
        self.assertFalse(AuditEntry.objects.filter(action='workitems.assignment_revoked').exists())

    def test_last_admin_deactivation_rolls_back_assignment_revocation(self):
        self.admin.invited_at = timezone.now()
        self.admin.save(update_fields=['invited_at'])
        assignment = self.add_user(self.admin)
        assignment.work_item.refresh_from_db()
        version = assignment.work_item.version
        with self.assertRaises(ValidationError):
            self.edit(self.admin, is_active=False)
        assignment.refresh_from_db()
        assignment.work_item.refresh_from_db()
        self.assertEqual(assignment.state, 'active')
        self.assertEqual(assignment.work_item.version, version)
        self.assertFalse(AuditEntry.objects.filter(action='workitems.assignment_revoked').exists())


class WorkflowConcurrencyTests(WorkflowFixture, TransactionTestCase):
    def setUp(self):
        self.prepare_workflow()

    def race(self, functions):
        import threading
        from django.db import close_old_connections, connection
        from django.http import Http404
        barrier = threading.Barrier(len(functions))
        results, errors = [], []
        def run(fn):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '8s'")
                barrier.wait(timeout=10)
                results.append(('ok', fn()))
            except (ValidationError, PermissionDenied, Http404) as exc:
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
        self.assertFalse(any(thread.is_alive() for thread in threads), 'Worker timeout')
        self.assertFalse(errors, errors)
        return results

    def test_completion_vs_new_subtask_cannot_keep_invalid_completion(self):
        for subtask in self.subtasks:
            self.mark_as(self.assignment, subtask)
        self.assignment.refresh_from_db()
        draft = self.draft()
        draft['subtasks'].append({'id': None, 'text': 'Конкурентная новая подзадача'})
        preview = self.edit_preview(draft)
        version = self.assignment.version
        result = self.race([lambda: set_completion(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version, assignment_id=self.assignment.pk, expected_version=version, expected_item_version=self.item.version, completed=True), lambda: apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=preview['token'])])
        self.assertEqual(len(result), 2)
        for status, value in result:
            if isinstance(value, ConsequencesChanged):
                apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=value.preview['token'])
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.completed_at)
        self.assertEqual(self.item.subtasks.filter(deleted_at__isnull=True).count(), 3)

    def test_completion_vs_revoke_ends_revoked_and_cannot_replay(self):
        for subtask in self.subtasks:
            self.mark_as(self.assignment, subtask)
        self.assignment.refresh_from_db()
        version = self.assignment.version
        def revoke_with_current_version():
            # HTTP form stale version may conflict; retry means an explicit new current form.
            row = Assignment.objects.get(pk=self.assignment.pk)
            return revoke_assignment(actor_id=self.manager.pk, auth_version=self.manager.auth_version, assignment_id=row.pk, expected_version=row.version, expected_item_version=self.item.version, reason='Конкурентный отзыв')
        result = self.race([lambda: set_completion(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version, assignment_id=self.assignment.pk, expected_version=version, expected_item_version=self.item.version, completed=True), revoke_with_current_version])
        self.assignment.refresh_from_db()
        if self.assignment.state == 'active':
            self.revoke_one()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.state, 'revoked')
        from django.http import Http404
        with self.assertRaises(Http404):
            self.complete_as(self.assignment)
        self.assertEqual(AuditEntry.objects.filter(action='workitems.assignment_revoked').count(), 1)
        self.assertLessEqual(AuditEntry.objects.filter(action='workitems.completed').count(), 1)
        self.assertEqual(len(result), 2)

    def test_two_marks_from_same_versions_only_one_changes(self):
        args = dict(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version, assignment_id=self.assignment.pk, subtask_id=self.subtasks[0].pk, expected_version=1, expected_item_version=1, expected_progress_version=1, value=True)
        result = self.race([lambda: set_mark(**args), lambda: set_mark(**args)])
        self.assertEqual(sorted(status for status, _ in result), ['denied', 'ok'])
        self.assertEqual(AuditEntry.objects.filter(action='workitems.mark_set').count(), 1)
        self.assertEqual(self.assignment.progress.get(subtask=self.subtasks[0]).version, 2)

    def test_two_edits_one_version_do_not_overwrite_each_other(self):
        first = self.edit_preview(self.draft() | {'title': 'Первый вариант'})
        second = self.edit_preview(self.draft() | {'title': 'Второй вариант'})
        result = self.race([lambda token=token: apply_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, token=token) for token in [first['token'], second['token']]])
        self.assertEqual(sorted(status for status, _ in result), ['denied', 'ok'])
        self.assertEqual(AuditEntry.objects.filter(action='workitems.edited').count(), 1)

    def test_archive_vs_mark_never_allows_mutation_after_archive(self):
        result = self.race([lambda: self.mark_as(self.assignment, self.subtasks[0]), lambda: archive_item(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=1)])
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.archived_at)
        with self.assertRaises(PermissionDenied):
            self.mark_as(self.assignment, self.subtasks[1])
        self.assertEqual(AuditEntry.objects.filter(action='workitems.archived').count(), 1)
        self.assertEqual(len(result), 2)


class PopulatedUpgradeTests(TransactionTestCase):
    def test_real_migration_from_populated_stage_three_schema(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.core.management import call_command
        from apps.accounts.tests.test_stage2 import seed
        seed()
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        executor.migrate([('workitems', '0002_history_protection')])
        try:
            old = executor.loader.project_state([('workitems', '0002_history_protection')]).apps
            Employee = old.get_model('accounts', 'User')
            Item = old.get_model('workitems', 'WorkItem')
            Assigned = old.get_model('workitems', 'Assignment')
            Child = old.get_model('workitems', 'Subtask')
            admin = Employee.objects.create(email='migration-admin@kp11.ru', full_name='Администратор миграционного теста', password='!', email_verified_at=timezone.now())
            user = Employee.objects.create(email='migration-user@kp11.ru', full_name='Ранее отключенный сотрудник', password='!', is_active=False, invited_at=timezone.now())
            item = Item.objects.create(author=admin, title='Задание до этапа 4', kind='task', due_at=timezone.now())
            assignment = Assigned.objects.create(work_item=item, user=user, assigned_by=admin)
            Child.objects.create(work_item=item, text='Существующая подзадача', position=1)
            AuditEntry.objects.create(actor_id=admin.pk, actor_label=admin.full_name, action='accounts.employee_changed', object_type='user', object_id=str(user.pk), changes={'before': {'is_active': True}, 'after': {'is_active': False}})
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            self.assertEqual(SubtaskProgress.objects.filter(assignment_id=assignment.pk).count(), 1)
            self.assertEqual(Assignment.objects.get(pk=assignment.pk).state, 'revoked')
            self.assertEqual(AuditEntry.objects.filter(action='workitems.assignment_revoked').count(), 1)
        finally:
            MigrationExecutor(connection).migrate(latest)
            call_command('grant_runtime_access', verbosity=0)
