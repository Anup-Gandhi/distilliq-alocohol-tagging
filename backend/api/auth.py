"""Accounts in Postgres, JWT sign-in, and sessions that are revoked on logout."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Request
from sqlalchemy import func, insert, select, update

from tagger.store import sessions, users

TOKEN_HOURS = 12


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret or len(secret) < 16:
        raise SystemExit("Set JWT_SECRET in .env (any random string of 16+ characters)")
    return secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def public(user) -> dict:
    return {"id": user.id, "email": user.email, "first_name": user.first_name, "last_name": user.last_name}


def seed_admin(engine) -> None:
    """Create the account named by EMAIL / PASSWORD / FIRST_NAME / LAST_NAME in .env, if it doesn't exist yet."""
    email, password = (os.getenv("EMAIL") or "").strip().lower(), os.getenv("PASSWORD") or ""
    if not email or not password:
        return
    with engine.begin() as conn:
        if conn.execute(select(users.c.id).where(users.c.email == email)).first():
            return
        conn.execute(insert(users).values(email=email, password_hash=hash_password(password),
                                          first_name=os.getenv("FIRST_NAME") or "",
                                          last_name=os.getenv("LAST_NAME") or ""))
    print(f"Created account {email}", flush=True)


def login(engine, email: str, password: str) -> dict:
    with engine.begin() as conn:
        user = conn.execute(select(users).where(users.c.email == email.strip().lower())).first()
        if not user or not check_password(password, user.password_hash):
            raise HTTPException(401, "Wrong email or password")
        jti = str(uuid.uuid4())
        conn.execute(insert(sessions).values(id=jti, user_id=user.id))
    expires = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    token = jwt.encode({"sub": str(user.id), "jti": jti, "exp": expires}, _secret(), algorithm="HS256")
    return {"token": token, "user": public(user)}


def logout(engine, jti: str) -> None:
    with engine.begin() as conn:
        conn.execute(update(sessions).where(sessions.c.id == jti).values(revoked_at=func.now()))


def current_user(request: Request):
    """FastAPI dependency: the signed-in user, or 401. Sets request.state.jti for logout."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Not signed in")
    try:
        claims = jwt.decode(header[7:], _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired, sign in again")
    engine = request.app.state.store.engine
    with engine.connect() as conn:
        session = conn.execute(select(sessions).where(sessions.c.id == claims.get("jti"))).first()
        if not session or session.revoked_at is not None:
            raise HTTPException(401, "Signed out, sign in again")
        user = conn.execute(select(users).where(users.c.id == session.user_id)).first()
    if not user:
        raise HTTPException(401, "Account no longer exists")
    request.state.jti = session.id
    return user
