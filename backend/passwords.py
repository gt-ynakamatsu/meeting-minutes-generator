import bcrypt


def verify_password(raw: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), stored_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False
