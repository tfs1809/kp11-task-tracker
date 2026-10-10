from django.db import models


class AccessGuard(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="access_guard_singleton")]
