import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class WorkItem(models.Model):
    class Kind(models.TextChoices):
        TASK = 'task', 'Задача'
        EVENT = 'event', 'Событие'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=5, choices=Kind.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, max_length=10000)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='issued_items')
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    due_at = models.DateTimeField()
    event_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    chat_closed_at = models.DateTimeField(null=True, blank=True)
    chat_closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    version = models.PositiveIntegerField(default=1)
    progress_epoch = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(kind='event', event_at__isnull=False) | Q(kind='task', event_at__isnull=True, chat_closed_at__isnull=True, chat_closed_by__isnull=True), name='workitem_kind_fields'),
            models.CheckConstraint(condition=Q(version__gte=1, progress_epoch__gte=1), name='workitem_positive_versions'),
            models.CheckConstraint(condition=Q(archived_at__isnull=True, archived_by__isnull=True) | Q(archived_at__isnull=False, archived_by__isnull=False), name='workitem_archive_pair'),
            models.CheckConstraint(condition=Q(chat_closed_at__isnull=True, chat_closed_by__isnull=True) | Q(chat_closed_at__isnull=False, chat_closed_by__isnull=False), name='workitem_chat_pair'),
        ]
        indexes = [models.Index(fields=['author', 'archived_at', 'due_at', 'id']), models.Index(fields=['archived_at', 'kind', 'due_at', 'id'])]
        ordering = ['due_at', 'id']


class Assignment(models.Model):
    class State(models.TextChoices):
        ACTIVE = 'active', 'Действует'
        REVOKED = 'revoked', 'Отозвано'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name='assignments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assignments')
    state = models.CharField(max_length=7, choices=State.choices, default=State.ACTIVE)
    first_assigned_at = models.DateTimeField(default=timezone.now)
    assigned_at = models.DateTimeField(default=timezone.now)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    revoked_reason = models.CharField(max_length=500, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_epoch = models.PositiveIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['work_item', 'user'], name='assignment_one_per_user'),
            models.UniqueConstraint(fields=['id', 'work_item'], name='assignment_id_item'),
            models.CheckConstraint(condition=Q(state='active', revoked_at__isnull=True, revoked_by__isnull=True, revoked_reason='') | (Q(state='revoked', revoked_at__isnull=False, revoked_by__isnull=False) & ~Q(revoked_reason='')), name='assignment_state_fields'),
            models.CheckConstraint(condition=Q(completed_at__isnull=True, completion_epoch__isnull=True) | Q(completed_at__isnull=False, completion_epoch__isnull=False, completion_epoch__gte=1), name='assignment_completion_pair'),
            models.CheckConstraint(condition=Q(version__gte=1), name='assignment_positive_version'),
        ]
        indexes = [models.Index(fields=['user', 'state', 'completed_at', 'work_item']), models.Index(fields=['work_item', 'state', 'completed_at'])]


class Subtask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name='subtasks')
    text = models.CharField(max_length=1000)
    position = models.PositiveIntegerField()
    content_revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['position', 'id']
        constraints = [models.UniqueConstraint(fields=['id', 'work_item'], name='subtask_id_item'), models.UniqueConstraint(fields=['work_item', 'position'], condition=Q(deleted_at__isnull=True), name='subtask_current_position'), models.CheckConstraint(condition=Q(content_revision__gte=1, position__gte=1), name='subtask_positive_numbers')]
        indexes = [models.Index(fields=['work_item', 'deleted_at', 'position'])]


class AudienceSelection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name='selections')
    batch_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    selected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    selected_at = models.DateTimeField(default=timezone.now)


class AudienceSelectionUser(models.Model):
    selection = models.ForeignKey(AudienceSelection, on_delete=models.PROTECT, related_name='users')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    label_snapshot = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['selection', 'user'], name='selection_unique_user')]


class AudienceSelectionRole(models.Model):
    selection = models.ForeignKey(AudienceSelection, on_delete=models.PROTECT, related_name='roles')
    role = models.ForeignKey('access.Role', on_delete=models.PROTECT, related_name='+')
    label_snapshot = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['selection', 'role'], name='selection_unique_role')]


class AudienceSelectionGroup(models.Model):
    selection = models.ForeignKey(AudienceSelection, on_delete=models.PROTECT, related_name='groups')
    group = models.ForeignKey('audiences.AudienceGroup', on_delete=models.PROTECT, related_name='+')
    label_snapshot = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['selection', 'group'], name='selection_unique_group')]


class AssignmentSource(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name='sources')
    batch_id = models.UUIDField()
    selected_user = models.ForeignKey(AudienceSelectionUser, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    selected_role = models.ForeignKey(AudienceSelectionRole, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    selected_group = models.ForeignKey(AudienceSelectionGroup, null=True, blank=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(selected_user__isnull=False, selected_role__isnull=True, selected_group__isnull=True) | Q(selected_user__isnull=True, selected_role__isnull=False, selected_group__isnull=True) | Q(selected_user__isnull=True, selected_role__isnull=True, selected_group__isnull=False), name='source_exactly_one'),
            *[models.UniqueConstraint(fields=['assignment', field], name=f'source_unique_{suffix}') for field, suffix in [('selected_user', 'user'), ('selected_role', 'role'), ('selected_group', 'group')]],
        ]


class MutationReceipt(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    operation = models.CharField(max_length=80)
    request_key = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor', 'operation', 'request_key'], name='receipt_unique_request')]
