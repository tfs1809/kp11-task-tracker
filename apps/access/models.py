import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class AccessGuard(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="access_guard_singleton")]


class Permission(models.Model):
    code = models.CharField(max_length=80, primary_key=True)
    name = models.CharField(max_length=255)
    admin_only = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Role(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=80, unique=True, editable=False)
    name = models.CharField("Название", max_length=255)
    is_active = models.BooleanField("Действует", default=True)
    is_system_admin = models.BooleanField(default=False, editable=False)
    version = models.PositiveIntegerField(default=1)
    permissions = models.ManyToManyField(Permission, through="RolePermission")

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="role_name_ci_unique"),
            models.UniqueConstraint(fields=["is_system_admin"], condition=models.Q(is_system_admin=True), name="one_system_admin_role"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="role_version_positive"),
        ]

    def __str__(self):
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    permission = models.ForeignKey(Permission, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "permission"], name="role_permission_unique")]


class UserRole(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="user_roles")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="role_grants")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "role"], name="user_role_unique")]
