import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from .administration import resolve_choices, set_memberships
from apps.access.models import Role, UserRole
from apps.access.policies import require_permission
from apps.access.services import actor_transaction, guarded_transaction, require_write_transaction
from apps.audit.models import AuditEntry
from apps.audit.services import record_action

from .limits import invitation_limits
from .models import Invitation, PasswordResetGrant, User, normalize_corporate_email

INVALID_LINK = "Ссылка недействительна или срок ее действия истек. Запросите новую."


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Delivery:
    sent = False


def deliver_after_commit(subject, path, token, email):
    outcome = Delivery()
    url = settings.PUBLIC_BASE_URL + path + "?" + urlencode({"token": token})

    def deliver():
        try:
            send_mail(subject, "Откройте ссылку и задайте собственный пароль:\n" + url + "\nЕсли вы не запрашивали доступ, проигнорируйте письмо.", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
            outcome.sent = True
        except Exception:
            # Никаких traceback: SMTP exception может содержать письмо с секретом.
            outcome.sent = False

    transaction.on_commit(deliver)
    return outcome


def issue_invitation(user, actor):
    require_write_transaction()
    now = timezone.now()
    Invitation.objects.filter(user=user, used_at__isnull=True, revoked_at__isnull=True).update(revoked_at=now)
    invitation = Invitation(user=user, email_snapshot=user.email, created_by=actor, expires_at=now + timedelta(hours=48))
    token = signing.dumps({"id": str(invitation.pk), "nonce": secrets.token_urlsafe(32)}, salt="kp11.invitation")
    invitation.token_digest = digest(token)
    invitation.save()
    record_action(actor=actor, action="accounts.invitation_sent", object_type="user", object_id=user.pk, changes={"invitation_id": str(invitation.pk), "expires_at": invitation.expires_at.isoformat()})
    return deliver_after_commit("Приглашение в трекер КП11", "/accounts/activate/", token, user.email)


def invite_employee(*, actor_id, auth_version, email, full_name, role_ids=(), group_ids=()):
    email = normalize_corporate_email(email)
    # Проверка отправителя до расходования квоты адреса; повторяется под write guard.
    with actor_transaction(actor_id, session_auth_version=auth_version) as actor:
        for code in ("accounts.manage_users", "access.manage_roles", "audiences.manage_groups"):
            require_permission(actor, code)
    invitation_limits(email, actor_id)
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        for code in ("accounts.manage_users", "access.manage_roles", "audiences.manage_groups"):
            require_permission(actor, code)
        roles, groups = resolve_choices(role_ids, group_ids)
        user = User.objects.create_user(email=email, full_name=full_name.strip(), invited_at=timezone.now())
        set_memberships(user, roles, groups, actor)
        record_action(actor=actor, action="accounts.employee_invited", object_type="user", object_id=user.pk, changes={"full_name": user.full_name, "email": user.email, "roles": list(map(str, role_ids)), "groups": list(map(str, group_ids))})
        return user, issue_invitation(user, actor)


def resend_invitation(*, actor_id, auth_version, user_id):
    with actor_transaction(actor_id, session_auth_version=auth_version) as actor:
        require_permission(actor, "accounts.manage_users")
        email = User.objects.get(pk=user_id).email
    invitation_limits(email, actor_id)
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        require_permission(actor, "accounts.manage_users")
        user = User.objects.get(pk=user_id)
        if not user.is_active or user.email_verified_at:
            raise ValidationError("Повторное приглашение доступно только действующему неактивированному сотруднику.")
        return issue_invitation(user, actor)


def bootstrap_administrator(*, email, full_name, resend=False):
    email = normalize_corporate_email(email)
    invitation_limits(email, "local-bootstrap")
    with guarded_transaction(write=True):
        started = AuditEntry.objects.filter(action="accounts.bootstrap_started").first()
        if started:
            user = User.objects.get(pk=started.object_id)
            if not resend or user.email != email or user.email_verified_at or not user.is_active:
                raise ValidationError("Bootstrap уже выполнен. Для первого неактивированного адреса допустим только --resend.")
        else:
            if resend or UserRole.objects.filter(role__is_system_admin=True).exists():
                raise ValidationError("Bootstrap недоступен: администратор уже назначен или приглашение еще не создано.")
            user = User.objects.create_user(email=email, full_name=full_name.strip(), invited_at=timezone.now())
            UserRole.objects.create(user=user, role=Role.objects.get(is_system_admin=True, is_active=True))
            record_action(actor=None, action="accounts.bootstrap_started", object_type="user", object_id=user.pk, changes={"email": user.email, "full_name": user.full_name})
        return issue_invitation(user, None)


def valid_invitation(token):
    try:
        data = signing.loads(token, salt="kp11.invitation", max_age=48 * 3600)
        invitation = Invitation.objects.select_related("user").get(pk=data["id"])
    except (signing.BadSignature, Invitation.DoesNotExist, ValueError, KeyError, TypeError, ValidationError):
        raise ValidationError(INVALID_LINK) from None
    user = invitation.user
    if invitation.used_at or invitation.revoked_at or invitation.expires_at <= timezone.now() or not constant_time_compare(invitation.token_digest, digest(token)) or not user.is_active or user.email_verified_at or user.email != invitation.email_snapshot:
        raise ValidationError(INVALID_LINK)
    return invitation


def activate_employee(*, token, password):
    candidate = valid_invitation(token).user
    validate_password(password, candidate)
    encoded = make_password(password)
    with guarded_transaction(write=True):
        invitation = valid_invitation(token)
        user = invitation.user
        validate_password(password, user)
        user.password = encoded
        user.email_verified_at = timezone.now()
        user.auth_version += 1
        user.version += 1
        user.save()
        invitation.used_at = timezone.now()
        invitation.version += 1
        invitation.save(update_fields=["used_at", "version"])
        record_action(actor=user, action="accounts.activated", object_type="user", object_id=user.pk)
        return user


def login_employee(request, *, email, password):
    candidate = authenticate(request, email=email, password=password)
    if candidate is None:
        raise ValidationError("Неверная почта или пароль, либо доступ еще не активирован.")
    with actor_transaction(candidate.pk, write=True) as actor:
        if actor.password != candidate.password:
            raise ValidationError("Учетные данные изменились. Повторите вход.")
        login(request, actor, backend="apps.accounts.backends.EmailBackend")
        request.session["auth_version"] = actor.auth_version
        record_action(actor=actor, action="accounts.login", object_type="user", object_id=actor.pk)


def logout_employee(request):
    with actor_transaction(request.user.pk, write=True, session_auth_version=request.session.get("auth_version")) as actor:
        record_action(actor=actor, action="accounts.logout", object_type="user", object_id=actor.pk)
        logout(request)


def request_recovery(email):
    try:
        email = normalize_corporate_email(email)
    except ValidationError:
        return
    with guarded_transaction(write=True):
        user = User.objects.filter(email=email, is_active=True, email_verified_at__isnull=False).first()
        if user is None or not user.has_usable_password():
            return
        now = timezone.now()
        PasswordResetGrant.objects.filter(user=user, used_at__isnull=True, revoked_at__isnull=True).update(revoked_at=now)
        grant = PasswordResetGrant(user=user, auth_version=user.auth_version, expires_at=now + timedelta(hours=1))
        token = str(grant.pk) + ":" + default_token_generator.make_token(user)
        grant.token_digest = digest(token)
        grant.save()
        record_action(actor=None, action="accounts.recovery_requested", object_type="user", object_id=user.pk)
        deliver_after_commit("Восстановление доступа к трекеру КП11", "/accounts/reset/", token, user.email)


def valid_reset(token):
    try:
        grant_id, native_token = token.split(":", 1)
        grant = PasswordResetGrant.objects.select_related("user").get(pk=grant_id)
    except (ValueError, ValidationError, PasswordResetGrant.DoesNotExist):
        raise ValidationError(INVALID_LINK) from None
    user = grant.user
    if grant.used_at or grant.revoked_at or grant.expires_at <= timezone.now() or not user.is_active or not user.email_verified_at or grant.auth_version != user.auth_version or not constant_time_compare(grant.token_digest, digest(token)) or not default_token_generator.check_token(user, native_token):
        raise ValidationError(INVALID_LINK)
    return grant


def reset_password(*, token, password):
    candidate = valid_reset(token).user
    validate_password(password, candidate)
    encoded = make_password(password)
    with guarded_transaction(write=True):
        grant = valid_reset(token)
        user = grant.user
        validate_password(password, user)
        user.password = encoded
        user.auth_version += 1
        user.version += 1
        user.save()
        grant.used_at = timezone.now()
        grant.save(update_fields=["used_at"])
        record_action(actor=user, action="accounts.password_reset", object_type="user", object_id=user.pk)


def change_password(request, *, old_password, password):
    candidate = User.objects.get(pk=request.user.pk)
    if not check_password(old_password, candidate.password):
        raise ValidationError("Текущий пароль неверен.")
    validate_password(password, candidate)
    encoded = make_password(password)
    with actor_transaction(candidate.pk, write=True, session_auth_version=request.session.get("auth_version")) as actor:
        if actor.password != candidate.password:
            raise ValidationError("Пароль уже изменился. Обновите страницу.")
        validate_password(password, actor)
        actor.password = encoded
        actor.auth_version += 1
        actor.version += 1
        actor.save()
        PasswordResetGrant.objects.filter(user=actor, revoked_at__isnull=True, used_at__isnull=True).update(revoked_at=timezone.now())
        update_session_auth_hash(request, actor)
        request.session["auth_version"] = actor.auth_version
        record_action(actor=actor, action="accounts.password_changed", object_type="user", object_id=actor.pk)
