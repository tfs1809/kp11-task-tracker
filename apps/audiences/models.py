import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class AudienceGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("Название", max_length=255)
    is_active = models.BooleanField("Действует", default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="audience_name_ci_unique"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="audience_version_positive"),
        ]

    def __str__(self):
        return self.name


class Membership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships")
    group = models.ForeignKey(AudienceGroup, on_delete=models.PROTECT, related_name="memberships")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="membership_grants")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "group"], name="membership_unique")]
