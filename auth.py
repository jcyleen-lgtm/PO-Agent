"""
PO Agent — simple single-user login.

How it works
- One account, configured with 3 environment variables (in .env locally, in Vercel → Settings → Environment Variables in production):
    APP_USERNAME       the username to log in with
    APP_PASSWORD_HASH  output of `python make_password_hash.py` (the plain password is never stored)
    SESSION_SECRET     long random string, also printed by make_password_hash.py
- After a successful login the server sets an HttpOnly, signed cookie. No server-side session storage is needed,
  which is what Vercel's serverless functions require.
- Every /api/* route except /api/auth/login and /api/auth/logout returns 401 without a valid cookie.
- If any of the 3 variables is missing, login is refused (fail closed) instead of letting everyone in.
- Changing APP_PASSWORD_HASH or SESSION_SECRET logs out every existing session.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

COOKIE_NAME = "po_session"
SESSION_HOURS = 12        # without "Remember me"
REMEMBER_DAYS = 30        # with "Remember me"
PBKDF2_ITERATIONS = 310_000
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/logout"}

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return 'pbkdf2_sha256:<iterations>:<salt>:<hash>'."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256:{iterations}:{_b64e(salt)}:{_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.strip().split(":")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64d(salt), int(iterations))
        return hmac.compare_digest(_b64e(dk), expected)
    except (ValueError, TypeError):
        return False


def _signing_key():
    secret = os.getenv("SESSION_SECRET", "")
    pw_hash = os.getenv("APP_PASSWORD_HASH", "")
    if len(secret) < 32 or not pw_hash:
        return None
    # Mixing in the password hash means a password change invalidates old cookies.
    return hashlib.sha256((secret + "|" + pw_hash).encode("utf-8")).digest()


def make_token(username: str, ttl_seconds: int) -> str:
    key = _signing_key()
    payload = _b64e(json.dumps({"u": username, "exp": int(time.time()) + ttl_seconds}).encode())
    sig = _b64e(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_token(token):
    """Return the username if the cookie is valid and not expired, else None."""
    key = _signing_key()
    if not token or not key or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = _b64e(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_b64d(payload))
    except (ValueError, TypeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data.get("u")


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


# ── middleware: protect /api/* ────────────────────────────────

async def require_login(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/") or path in PUBLIC_API_PATHS:
        return await call_next(request)
    if not read_token(request.cookies.get(COOKIE_NAME)):
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    return await call_next(request)


# ── routes ────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str
    remember: bool = False


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    # Plain `def` (not async): FastAPI runs it in a thread so the slow hash doesn't block other requests.
    username = os.getenv("APP_USERNAME", "").strip()
    pw_hash = os.getenv("APP_PASSWORD_HASH", "").strip()
    if not username or not pw_hash or not _signing_key():
        return JSONResponse(
            {"detail": "Login is not set up on the server yet (APP_USERNAME, APP_PASSWORD_HASH, SESSION_SECRET)."},
            status_code=500,
        )

    user_ok = hmac.compare_digest(body.username.strip().lower(), username.lower())
    pass_ok = verify_password(body.password, pw_hash)  # always runs, so timing doesn't reveal the username
    if not (user_ok and pass_ok):
        return JSONResponse({"detail": "Incorrect username or password."}, status_code=401)

    ttl = REMEMBER_DAYS * 86400 if body.remember else SESSION_HOURS * 3600
    resp = JSONResponse({"username": username})
    resp.set_cookie(
        COOKIE_NAME,
        make_token(username, ttl),
        max_age=ttl if body.remember else None,  # no max_age = cookie ends when the browser closes
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/api/auth/me")
def me(request: Request):
    # Only reached with a valid cookie (the middleware blocks the rest).
    return {"username": read_token(request.cookies.get(COOKIE_NAME))}
