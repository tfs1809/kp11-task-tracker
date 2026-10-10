from datetime import datetime, timedelta, timezone as datetime_timezone

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import RateLimitBucket


class RateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


def consume_limits(rules):
    """Вся группа лимитов атомарна; единый порядок строк исключает deadlock."""
    now = timezone.now()
    buckets = []
    with transaction.atomic():
        for scope, key, maximum, seconds in sorted(rules):
            digest = salted_hmac("kp11.rate-limit", str(key), algorithm="sha256").hexdigest()
            start = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, tz=datetime_timezone.utc)
            bucket, _ = RateLimitBucket.objects.get_or_create(
                scope=scope, key_digest=digest, window_start=start,
                defaults={"expires_at": start + timedelta(seconds=seconds)},
            )
            bucket = RateLimitBucket.objects.select_for_update().get(pk=bucket.pk)
            if bucket.count >= maximum:
                raise RateLimited(max(1, int((bucket.expires_at - now).total_seconds())))
            buckets.append(bucket)
        for bucket in buckets:
            bucket.count += 1
            bucket.save(update_fields=["count"])


def login_limits(email, ip):
    consume_limits([("login.address-ip", email.strip().lower() + "|" + ip, 5, 300), ("login.ip", ip, 30, 300)])


def recovery_limits(email, ip):
    consume_limits([("reset.address", email.strip().lower(), 3, 3600), ("reset.ip", ip, 20, 3600)])


def invitation_limits(email, actor_id):
    consume_limits([("invite.address", email, 3, 3600), ("invite.actor", str(actor_id), 60, 3600)])
