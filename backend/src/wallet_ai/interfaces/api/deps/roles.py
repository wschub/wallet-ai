from __future__ import annotations

from fastapi import Depends, HTTPException

from .auth import get_current_user


def require_role(*roles: str):
    async def checker(user=Depends(get_current_user)):
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return checker
