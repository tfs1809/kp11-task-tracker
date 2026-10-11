"""Database-backed permission checks for the native HTTP layer."""

from .db import one


def has_permission(conn, user_id, code):
    return one(
        conn,
        """
        SELECT 1
        FROM access_userrole ur
        JOIN access_role r ON r.id=ur.role_id AND r.is_active
        JOIN access_rolepermission rp ON rp.role_id=r.id
        JOIN access_permission p ON p.code=rp.permission_id
        WHERE ur.user_id=%s AND (p.code=%s OR r.is_system_admin)
        LIMIT 1
        """,
        (user_id, code),
    ) is not None


def require_permission(conn, user_id, code):
    if not has_permission(conn, user_id, code):
        raise PermissionError("Действие запрещено.")
