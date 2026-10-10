import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models.functions import Lower, Trim


def normalize_corporate_email(value):
    normalized = value.strip().lower()
    if not normalized.isascii():
        raise ValidationError("Используйте корпоративный адрес corp.kp11.ru.")
    validate_email(normalized)
    if normalized.rsplit("@", 1)[-1] != "corp.kp11.ru":
        raise ValidationError("Допустим только точный домен corp.kp11.ru.")
    return normalized


class UserManager(BaseUserManager):
    def create_user(self, email, full_name, password=None, **extra_fields):
        user = self.model(email=normalize_corporate_email(email), full_name=full_name, **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField("ФИО", max_length=255)
    email = models.EmailField("Корпоративная почта", unique=True)
    is_active = models.BooleanField(default=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    invited_at = models.DateTimeField(null=True, blank=True)
    auth_version = models.PositiveIntegerField(default=1)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"
        constraints = [
            models.CheckConstraint(condition=models.Q(email=Lower(Trim("email"))), name="user_email_normalized"),
            models.CheckConstraint(condition=models.Q(email__endswith="@corp.kp11.ru"), name="user_email_corporate"),
            models.CheckConstraint(condition=models.Q(auth_version__gte=1), name="user_auth_version_positive"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="user_version_positive"),
        ]

    def clean(self):
        super().clean()
        self.email = normalize_corporate_email(self.email)

    def __str__(self):
        return self.full_name


class Invitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    email_snapshot = models.EmailField()
    token_digest = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, related_name="sent_invitations")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user"], condition=models.Q(used_at__isnull=True, revoked_at__isnull=True), name="one_live_invitation")]


class PasswordResetGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    token_digest = models.CharField(max_length=64, unique=True)
    auth_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user"], condition=models.Q(used_at__isnull=True, revoked_at__isnull=True), name="one_live_reset_grant")]


class RateLimitBucket(models.Model):
    scope = models.CharField(max_length=40)
    key_digest = models.CharField(max_length=64)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope", "key_digest", "window_start"], name="rate_limit_window_unique")]
