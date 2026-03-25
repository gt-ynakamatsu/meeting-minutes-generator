"""管理者専用 API。"""

import sqlite3

from fastapi import APIRouter, HTTPException

import database as db
from backend.deps import AdminUser
from backend.schemas import AdminCreateUserRequest, AdminPasswordResetRequest, AdminRolePatch, AdminUserRow

router = APIRouter(tags=["admin"])


@router.get("/api/admin/users", response_model=list[AdminUserRow])
def admin_list_users(_admin: AdminUser):
    rows = db.list_registry_users()
    return [
        AdminUserRow(
            email=r["username"],
            is_admin=bool(r["is_admin"]),
            created_at=str(r["created_at"]) if r.get("created_at") is not None else None,
        )
        for r in rows
    ]


@router.post("/api/admin/users", response_model=AdminUserRow)
def admin_create_user(body: AdminCreateUserRequest, _admin: AdminUser):
    u = db.registry_login_normalize(body.email or "")
    pw = (body.password or "").replace("\r", "")
    try:
        db.create_registry_user(u, pw, is_admin=body.is_admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="このメールアドレスは既に使われています") from None
    row = db.get_user_by_username(u)
    if not row:
        raise HTTPException(status_code=500, detail="作成に失敗しました")
    return AdminUserRow(
        email=str(row["username"]),
        is_admin=db.user_is_admin(str(row["username"])),
        created_at=str(row["created_at"]) if row["created_at"] is not None else None,
    )


@router.patch("/api/admin/users/{login_email}/password")
def admin_reset_password(login_email: str, body: AdminPasswordResetRequest, _admin: AdminUser):
    u = db.resolve_registry_username_for_mutation(login_email)
    if not u:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    try:
        ok = db.set_registry_user_password(u, (body.new_password or "").replace("\r", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    return {"ok": True}


@router.patch("/api/admin/users/{login_email}/role")
def admin_set_role(login_email: str, body: AdminRolePatch, _admin: AdminUser):
    u = db.resolve_registry_username_for_mutation(login_email)
    if not u:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    try:
        db.set_registry_user_admin(u, body.is_admin)
    except KeyError:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.delete("/api/admin/users/{login_email}")
def admin_delete_user(login_email: str, _admin: AdminUser):
    target = db.resolve_registry_username_for_mutation(login_email)
    if not target:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    admin_key = db.resolve_registry_username_for_mutation(_admin)
    if admin_key and admin_key == target:
        raise HTTPException(status_code=400, detail="自分自身は削除できません")
    try:
        db.delete_registry_user(target)
    except KeyError:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}
