"""Private API for the Albayda Court Android client.

The Telegram bot remains a separate process. This API never exposes its Telegram
credential and reuses the existing case-record parsing/search/PDF functions.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from case_records import (
    CaseRecordsError,
    available_case_types,
    available_judges,
    generate_case_pdf,
    generate_sessions_pdf,
    load_case_records,
    normalize_judge_name,
    search_case_records,
    search_sessions_by_date,
)

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("albayda_api")
APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("APP_DATABASE_PATH", str(APP_DIR / "data" / "app.sqlite3")))
TOKEN_TTL_SECONDS = 8 * 60 * 60
PASSWORD_HASHER = PasswordHasher()
DUMMY_HASH = PASSWORD_HASHER.hash("not-a-real-user-password")
BEARER = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Albayda Court API",
    version="1.0.0",
    description="Private API for the Albayda Court Android application.",
    docs_url="/api/docs" if os.getenv("API_DOCS_ENABLED", "false").lower() == "true" else None,
    redoc_url=None,
)


class RegistrationInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=24)
    password: str = Field(min_length=10, max_length=128)


class LoginInput(BaseModel):
    phone: str = Field(min_length=7, max_length=24)
    password: str = Field(min_length=1, max_length=128)


class UserDecision(BaseModel):
    approve: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _database_path() -> Path:
    return DB_PATH


@contextmanager
def _db():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _normalize_phone(value: str) -> str:
    digits = []
    for char in value.strip():
        if char.isdecimal():
            digits.append(str(unicodedata.decimal(char)))
        elif char == "+" and not digits:
            digits.append(char)
        elif char in " -().":
            continue
        else:
            raise HTTPException(status_code=422, detail="رقم الجوال غير صالح.")
    normalized = "".join(digits)
    numeric = normalized[1:] if normalized.startswith("+") else normalized
    if not numeric.isdigit() or not 7 <= len(numeric) <= 15:
        raise HTTPException(status_code=422, detail="رقم الجوال غير صالح.")
    return normalized


def _audit(actor_id: int | None, action: str, target_id: int | None = None, metadata=None):
    with _db() as connection:
        connection.execute(
            "INSERT INTO activity_log(actor_user_id, action, target_user_id, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (actor_id, action, target_id, json.dumps(metadata or {}, ensure_ascii=False), _now()),
        )


def _initialize_database():
    with _db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL,
                target_user_id INTEGER REFERENCES users(id),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_users_status_created ON users(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_activity_actor ON activity_log(actor_user_id, created_at DESC);
            """
        )
        admin = connection.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if admin is None:
            name = os.getenv("ADMIN_BOOTSTRAP_NAME", "").strip()
            phone = os.getenv("ADMIN_BOOTSTRAP_PHONE", "").strip()
            password = os.getenv("ADMIN_BOOTSTRAP_PASSWORD", "")
            if not (name and phone and password):
                raise RuntimeError(
                    "No administrator exists. Configure ADMIN_BOOTSTRAP_NAME, "
                    "ADMIN_BOOTSTRAP_PHONE and ADMIN_BOOTSTRAP_PASSWORD once to initialize one."
                )
            normalized_phone = _normalize_phone(phone)
            if len(password) < 12:
                raise RuntimeError("ADMIN_BOOTSTRAP_PASSWORD must contain at least 12 characters.")
            connection.execute(
                "INSERT INTO users(name, phone, password_hash, role, status, created_at) "
                "VALUES (?, ?, ?, 'admin', 'approved', ?)",
                (name, normalized_phone, PASSWORD_HASHER.hash(password), _now()),
            )
            LOGGER.info("Initial administrator account created; password is not logged.")


@app.on_event("startup")
def startup():
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Set JWT_SECRET to a random secret of at least 32 characters.")
    _initialize_database()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="سجّل الدخول للمتابعة.", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(
            credentials.credentials,
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="انتهت صلاحية الجلسة؛ سجّل الدخول مجدداً.")
    with _db() as connection:
        user = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        raise HTTPException(status_code=401, detail="الحساب غير موجود.")
    if user["status"] != "approved":
        raise HTTPException(status_code=403, detail="الحساب غير معتمد أو تم رفضه.")
    return dict(user)


def _admin_user(user: Annotated[dict, Depends(_current_user)]):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="هذه العملية متاحة للمسؤول فقط.")
    return user


def _public_user(user) -> dict:
    return {
        "id": user["id"],
        "name": user["name"],
        "phone": user["phone"],
        "role": user["role"],
        "status": user["status"],
        "created_at": user["created_at"],
    }


def _issue_token(user) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user["id"]), "iat": now, "exp": int(now.timestamp()) + TOKEN_TTL_SECONDS},
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/register", status_code=status.HTTP_202_ACCEPTED)
def register(payload: RegistrationInput):
    phone = _normalize_phone(payload.phone)
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="أدخل الاسم كاملاً.")
    try:
        with _db() as connection:
            cursor = connection.execute(
                "INSERT INTO users(name, phone, password_hash, role, status, created_at) "
                "VALUES (?, ?, ?, 'user', 'pending', ?)",
                (name, phone, PASSWORD_HASHER.hash(payload.password), _now()),
            )
            user_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO activity_log(actor_user_id, action, target_user_id, metadata_json, created_at) "
                "VALUES (?, 'registration_submitted', ?, '{}', ?)",
                (user_id, user_id, _now()),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="رقم الجوال مسجل مسبقاً.")
    return {"status": "pending", "message": "تم استلام طلبك. يمكنك الدخول بعد موافقة المسؤول."}


@app.post("/api/auth/login")
def login(payload: LoginInput):
    phone = _normalize_phone(payload.phone)
    with _db() as connection:
        user = connection.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    valid = False
    if user:
        try:
            valid = PASSWORD_HASHER.verify(user["password_hash"], payload.password)
        except (VerifyMismatchError, InvalidHashError):
            valid = False
    else:
        try:
            PASSWORD_HASHER.verify(DUMMY_HASH, payload.password)
        except VerifyMismatchError:
            pass
    if not valid:
        _audit(None, "login_failed")
        raise HTTPException(status_code=401, detail="رقم الجوال أو كلمة المرور غير صحيحة.")
    if user["status"] != "approved":
        _audit(user["id"], "login_denied_unapproved")
        raise HTTPException(status_code=403, detail="طلب الحساب بانتظار مراجعة المسؤول أو تم رفضه.")
    _audit(user["id"], "login_succeeded")
    return {"access_token": _issue_token(user), "token_type": "bearer", "user": _public_user(user)}


@app.get("/api/me")
def me(user: Annotated[dict, Depends(_current_user)]):
    return _public_user(user)


@app.get("/api/case-types")
def case_types(user: Annotated[dict, Depends(_current_user)]):
    try:
        result = available_case_types(load_case_records())
    except CaseRecordsError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    _audit(user["id"], "case_types_viewed")
    return {"case_types": result, "judges": available_judges(load_case_records())}


@app.get("/api/cases/search")
def search_cases(
    user: Annotated[dict, Depends(_current_user)],
    q: str = Query(min_length=1, max_length=120),
    case_type: str = Query(default="all", max_length=80),
    judge: str = Query(default="all", max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
):
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="أدخل رقم القضية أو اسم أحد الأطراف.")
    method = "number" if "/" in query else "name"
    try:
        records = load_case_records()
        matches = search_case_records(records, query, method, case_type, judge, limit=limit)
    except CaseRecordsError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    _audit(user["id"], "case_search", metadata={"result_count": len(matches), "method": method})
    return {"count": len(matches), "cases": matches}


def _normalize_date(value: str) -> str | None:
    normalized = "".join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in value.strip())
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            continue
    return None


@app.get("/api/sessions")
def sessions(
    user: Annotated[dict, Depends(_current_user)],
    sitting_date: str = Query(alias="date", min_length=8, max_length=10),
    judge: str = Query(default="all", max_length=120),
):
    normalized_date = _normalize_date(sitting_date)
    if normalized_date is None:
        raise HTTPException(status_code=422, detail="التاريخ غير صالح؛ استخدم YYYY-MM-DD.")
    try:
        records = load_case_records()
        results = search_sessions_by_date(records, normalized_date)
    except CaseRecordsError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if judge != "all":
        results = [r for r in results if normalize_judge_name(r.get("برئاسة القاضي")) == normalize_judge_name(judge)]
    _audit(user["id"], "sessions_search", metadata={"result_count": len(results)})
    return {"date": normalized_date, "count": len(results), "sessions": results[:500]}


@app.get("/api/cases/detail")
def case_detail(
    user: Annotated[dict, Depends(_current_user)],
    case_number: str = Query(min_length=1, max_length=40),
):
    try:
        matches = search_case_records(load_case_records(), case_number, "number", limit=1)
    except CaseRecordsError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not matches:
        raise HTTPException(status_code=404, detail="لم يتم العثور على القضية.")
    _audit(user["id"], "case_detail_viewed")
    return {"case": matches[0]}


@app.post("/api/cases/pdf")
def case_pdf(
    payload: dict,
    user: Annotated[dict, Depends(_current_user)],
):
    case_number = str(payload.get("case_number", "")).strip()
    if not case_number or len(case_number) > 40:
        raise HTTPException(status_code=422, detail="رقم القضية غير صالح.")
    try:
        matches = search_case_records(load_case_records(), case_number, "number", limit=1)
        if not matches:
            raise HTTPException(status_code=404, detail="لم يتم العثور على القضية.")
        pdf = generate_case_pdf(matches[0])
    except CaseRecordsError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    safe_number = re.sub(r"[^0-9A-Za-z_-]", "_", case_number)[:40] or "case"
    _audit(user["id"], "case_pdf_downloaded")
    return Response(
        content=pdf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="case_{safe_number}.pdf"'},
    )


@app.get("/api/admin/users")
def admin_users(
    admin: Annotated[dict, Depends(_admin_user)],
    user_status: str = Query(default="pending", alias="status", pattern="^(pending|approved|rejected|all)$"),
):
    with _db() as connection:
        if user_status == "all":
            users = connection.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 500").fetchall()
        else:
            users = connection.execute("SELECT * FROM users WHERE status=? ORDER BY created_at ASC LIMIT 500", (user_status,)).fetchall()
    _audit(admin["id"], "admin_users_listed", metadata={"filter": user_status})
    return {"users": [_public_user(row) for row in users]}


@app.post("/api/admin/users/{user_id}/decision")
def decide_user(
    user_id: int,
    payload: UserDecision,
    admin: Annotated[dict, Depends(_admin_user)],
):
    new_status = "approved" if payload.approve else "rejected"
    with _db() as connection:
        target = connection.execute("SELECT id, role FROM users WHERE id=?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="الحساب غير موجود.")
        if target["role"] == "admin":
            raise HTTPException(status_code=409, detail="لا يمكن تغيير حالة حساب مسؤول من هذه الشاشة.")
        connection.execute(
            "UPDATE users SET status=?, reviewed_at=?, reviewed_by=? WHERE id=?",
            (new_status, _now(), admin["id"], user_id),
        )
        connection.execute(
            "INSERT INTO activity_log(actor_user_id, action, target_user_id, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (admin["id"], "user_approved" if payload.approve else "user_rejected", user_id, "{}", _now()),
        )
    return {"id": user_id, "status": new_status}


@app.get("/api/admin/activity")
def admin_activity(
    admin: Annotated[dict, Depends(_admin_user)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with _db() as connection:
        rows = connection.execute(
            "SELECT a.id, a.action, a.target_user_id, a.metadata_json, a.created_at, "
            "u.id AS actor_id, u.name AS actor_name, u.phone AS actor_phone "
            "FROM activity_log a LEFT JOIN users u ON u.id=a.actor_user_id "
            "ORDER BY a.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    _audit(admin["id"], "admin_activity_viewed", metadata={"returned": len(rows)})
    return {
        "events": [
            {
                "id": row["id"],
                "action": row["action"],
                "actor": ({"id": row["actor_id"], "name": row["actor_name"], "phone": row["actor_phone"]} if row["actor_id"] else None),
                "target_user_id": row["target_user_id"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    }
