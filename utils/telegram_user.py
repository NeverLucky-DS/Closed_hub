from __future__ import annotations


def user_display_handle(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    parts = [getattr(user, "first_name", None) or "", getattr(user, "last_name", None) or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or str(getattr(user, "id", ""))
