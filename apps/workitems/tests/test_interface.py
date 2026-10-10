import uuid
from django.test import TestCase
from django.utils import timezone

from apps.accounts.tests.test_stage2 import employee
from apps.workitems.tests.test_issuance import IssuanceFixture
from apps.workitems.models import Assignment, WorkItem
from apps.workitems import workflow


class InterfaceTests(IssuanceFixture, TestCase):
    def setUp(self):
        self.prepare()
        self.manager.full_name = 'Доступный автор'
        self.manager.save(update_fields=['full_name'])
        self.item, _ = self.issue(payload={**self.payload, 'title': 'Ближайшая задача', 'due_at': '2020-01-01T20:59:59+00:00'})
        self.event, _ = self.issue(request_key=uuid.uuid4(), payload={**self.payload, 'kind': 'event', 'title': 'Дальнее событие', 'due_at': '2030-01-01T20:59:59+00:00', 'event_at': '2029-12-30T12:00:00+00:00'})
        self.other = employee('unrelated@corp.kp11.ru')
        self.other.full_name = 'Скрытый исполнитель'
        self.other.invited_at = timezone.now()
        self.other.save()
        self.hidden, _ = self.issue(request_key=uuid.uuid4(), payload={**self.payload, 'title': 'Чужая тайная задача', 'users': [str(self.other.pk)], 'roles': [], 'groups': []})
        self.login(self.recipient)

    def finish(self, item):
        assignment = item.assignments.get(user=self.recipient)
        for row in assignment.progress.select_related('subtask'):
            workflow.set_mark(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version, assignment_id=assignment.pk, subtask_id=row.subtask_id, expected_version=assignment.version, expected_item_version=item.version, expected_progress_version=row.version, value=True)
            assignment.refresh_from_db()
        workflow.set_completion(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version, assignment_id=assignment.pk, expected_version=assignment.version, expected_item_version=item.version, completed=True)

    def test_personal_filters_order_and_author_directory_are_scoped(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.pk for row in response.context['assignments']], list(Assignment.objects.filter(work_item__in=[self.item, self.event]).order_by('work_item__due_at').values_list('pk', flat=True)))
        self.assertContains(response, 'Просрочено')
        self.assertNotContains(response, self.hidden.title)
        self.assertNotContains(response, self.other.full_name)
        self.assertNotContains(response, 'Все исполнители')
        self.assertEqual(list(self.client.get('/?sort=-due').context['assignments'])[0].work_item_id, self.event.pk)
        self.assertEqual(list(self.client.get('/?kind=event').context['assignments'])[0].work_item_id, self.event.pk)
        self.assertEqual(len(self.client.get('/?author=' + str(self.manager.pk)).context['assignments']), 2)
        response = self.client.get('/?author=' + str(self.admin.pk))
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, self.admin.full_name, status_code=400)
        self.assertFalse(response.context['assignments'])

    def test_tabs_completion_return_and_filter_preservation(self):
        self.finish(self.event)
        response = self.client.get('/?kind=event&sort=-due')
        self.assertEqual(response.context['counts'], {'assigned': 0, 'completed': 1})
        self.assertIn('kind=event', response.context['tab_links']['completed'])
        self.assertFalse(response.context['assignments'])
        response = self.client.get('/?tab=completed&kind=event')
        self.assertEqual(len(response.context['assignments']), 1)
        self.assertContains(response, 'Выполнено')
        assignment = self.event.assignments.get(user=self.recipient)
        workflow.set_completion(actor_id=self.recipient.pk, auth_version=self.recipient.auth_version, assignment_id=assignment.pk, expected_version=assignment.version, expected_item_version=self.event.version, completed=False)
        self.assertFalse(self.client.get('/?tab=completed').context['assignments'])
        response = self.client.get('/?kind=event')
        self.assertEqual(response.context['assignments'][0].checked_count, 2)

    def test_invalid_filters_never_return_unfiltered_objects(self):
        for query in ['kind=unknown', 'sort=__password', 'author=not-a-uuid', 'tab=unknown']:
            with self.subTest(query=query):
                response = self.client.get('/?' + query)
                self.assertEqual(response.status_code, 400)
                self.assertNotContains(response, self.item.title, status_code=400)
                self.assertContains(response, 'role="alert"', status_code=400)

    def test_revoked_and_archived_items_disappear_from_personal_lists(self):
        assignment = self.item.assignments.get(user=self.recipient)
        workflow.revoke_assignment(actor_id=self.manager.pk, auth_version=self.manager.auth_version, assignment_id=assignment.pk, expected_version=assignment.version, expected_item_version=self.item.version, reason='Проверка')
        workflow.archive_item(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.event.pk, expected_version=self.event.version)
        self.assertFalse(self.client.get('/').context['assignments'])
        self.assertNotContains(self.client.get('/workitems/archive/'), self.item.title)
        self.assertContains(self.client.get('/workitems/archive/'), self.event.title)

    def test_personal_pagination_preserves_filters_and_scoping(self):
        for i in range(21):
            self.issue(request_key=uuid.uuid4(), payload={**self.payload, 'title': f'Страница {i}', 'subtasks': []})
        response = self.client.get('/?kind=task&sort=-due&page=2')
        self.assertEqual(response.context['page_obj'].number, 2)
        self.assertEqual(response.context['page_obj'].paginator.count, 22)
        self.assertEqual(len(response.context['assignments']), 2)
        self.assertContains(response, 'kind=task')
        self.assertNotContains(response, self.hidden.title)

    def test_issued_all_and_archive_enforce_scope_and_navigation(self):
        self.assertEqual(self.client.get('/workitems/issued/').status_code, 403)
        self.assertEqual(self.client.get('/workitems/all/?kind=task').status_code, 403)
        self.assertNotContains(self.client.get('/'), 'href="/administration/"')
        self.login(self.manager)
        response = self.client.get('/workitems/issued/?kind=event')
        self.assertContains(response, self.event.title)
        self.assertNotContains(response, self.item.title)
        self.assertEqual(self.client.get('/workitems/all/').status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get('/workitems/all/').context['page_obj'].paginator.count, 3)

    def test_issue_preview_back_preserves_draft_without_mutation(self):
        preview = self.preview()
        self.login(self.manager)
        response = self.client.post('/workitems/confirm/', {'preview': preview['token'], 'request_key': self.key, 'back': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['title'], self.payload['title'])
        self.assertContains(response, 'action="/workitems/new/"')
        self.assertEqual(response.context['form'].initial['subtasks'], '\n'.join(self.payload['subtasks']))
        self.assertEqual(WorkItem.objects.count(), 3)
        self.login(self.admin)
        response = self.client.post('/workitems/confirm/', {'preview': preview['token'], 'request_key': self.key, 'back': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, self.payload['description'], status_code=400)

    def test_edit_and_addition_preview_back_preserve_draft_and_check_actor(self):
        payload = workflow.content_snapshot(self.item)
        payload = {name: payload[name] for name in ('title', 'description', 'due_at', 'event_at')} | {'subtasks': [{'id': str(row.pk), 'text': 'Сохраненный текст'} for row in self.item.subtasks.all()]}
        preview = workflow.preview_edit(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=self.item.version, payload=payload)
        self.login(self.manager)
        response = self.client.post(f'/workitems/{self.item.pk}/confirm-change/', {'token': preview['token'], 'back': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Сохраненный текст')
        self.assertContains(response, f'action="/workitems/{self.item.pk}/edit/"')
        self.item.refresh_from_db()
        self.assertEqual(self.item.version, 1)
        add = workflow.preview_additions(actor_id=self.manager.pk, auth_version=self.manager.auth_version, item_id=self.item.pk, expected_version=self.item.version, payload={'users': [str(self.other.pk)], 'roles': [], 'groups': []})
        response = self.client.post(f'/workitems/{self.item.pk}/confirm-change/', {'token': add['token'], 'back': '1'})
        self.assertEqual(response.context['form'].initial['users'], [str(self.other.pk)])
        self.assertContains(response, f'action="/workitems/{self.item.pk}/add/"')
        self.login(self.admin)
        response = self.client.post(f'/workitems/{self.item.pk}/confirm-change/', {'token': preview['token'], 'back': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, 'Сохраненный текст', status_code=400)

    def test_role_revocation_hides_links_and_forbids_direct_list(self):
        self.login(self.manager)
        self.assertContains(self.client.get('/'), 'href="/workitems/issued/"')
        self.edit(self.manager, role_ids=[])
        self.assertNotContains(self.client.get('/'), 'href="/workitems/issued/"')
        self.assertEqual(self.client.get('/workitems/issued/').status_code, 403)
