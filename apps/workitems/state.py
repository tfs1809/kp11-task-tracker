"""Derived, time-based state used by the task and event interface.

Event expiration is deliberately calculated on read.  This keeps the existing
infrastructure unchanged (no scheduler or migration is required) while making
the transition deterministic for every request.
"""
from datetime import timedelta

from django.utils import timezone


EVENT_CHAT_GRACE = timedelta(days=1)


def event_chat_locked(item, now=None):
    """Return whether an event chat is read-only because its grace period ended."""
    now = now or timezone.now()
    return bool(
        item.kind == 'event'
        and item.event_at
        and now >= item.event_at + EVENT_CHAT_GRACE
    )


def event_auto_completed(item, now=None):
    """Return whether an event is automatically moved to completed for an assignee."""
    return event_chat_locked(item, now)


def assignment_completed(assignment, now=None):
    """Effective completion, including the lazy event expiration transition."""
    return bool(assignment.completed_at or event_auto_completed(assignment.work_item, now))


def assignment_can_restore(assignment, now=None):
    """Completed events cannot be restored after their chat grace period."""
    return not event_auto_completed(assignment.work_item, now)


def item_sort_value(item):
    """The interface sorts tasks by due date and events by event date."""
    return item.event_at if item.kind == 'event' else item.due_at
