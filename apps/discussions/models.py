from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class EventMessage(models.Model):
    work_item = models.ForeignKey('workitems.WorkItem', on_delete=models.PROTECT, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    text = models.TextField(max_length=4000)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    hidden_reason = models.CharField(max_length=500, blank=True)
    last_change = models.ForeignKey('audit.AuditEntry', on_delete=models.PROTECT, related_name='+', db_index=False)

    class Meta:
        ordering = ['id']
        constraints = [models.CheckConstraint(condition=Q(hidden_at__isnull=True, hidden_by__isnull=True, hidden_reason='') | (Q(hidden_at__isnull=False, hidden_by__isnull=False) & ~Q(hidden_reason='')), name='message_hidden_pair')]
        indexes = [models.Index(fields=['work_item', 'id']), models.Index(fields=['work_item', 'last_change'])]
