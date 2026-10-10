import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Аудит нельзя изменять.")

    def delete(self):
        raise ValueError("Аудит нельзя удалять.")


class AuditEntry(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    actor_label = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)
    changes = models.JSONField(default=dict)
    reason = models.CharField(max_length=500, blank=True)
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["object_type", "object_id", "id"], name="audit_object_idx"),
            models.Index(fields=["actor", "occurred_at", "id"], name="audit_actor_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Аудит нельзя изменять.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Аудит нельзя удалять.")
