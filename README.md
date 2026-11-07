# Albayda Court Telegram bot and private Android application API

## Android app/backend

This repository now includes a FastAPI service (`api.py`) and Android client source (`android/`). The API serves the existing case register/search/PDF functionality. The Telegram bot can continue running separately; its `TELEGRAM_BOT_TOKEN` is not used by the Android API and must never be embedded in the APK.

### Server setup

Use Python 3.11 or newer. Install dependencies with `python -m pip install -r requirements.txt`, then configure these **host environment variables** (never commit real values):

- `GOOGLE_DRIVE_FILE_ID`: publicly link-readable court CSV file ID, as required by the existing case-data loader.
- `JWT_SECRET`: at least 32 random characters; keep stable between restarts so sessions remain valid.
- `APP_DATABASE_PATH`: writable persistent disk path, e.g. `/var/lib/albayda/app.sqlite3`. The database contains password hashes, user accounts, and audit history. Back it up and restrict filesystem access.
- `ADMIN_BOOTSTRAP_NAME`, `ADMIN_BOOTSTRAP_PHONE`, `ADMIN_BOOTSTRAP_PASSWORD`: used to create the first administrator only. Admin password must be at least 12 characters. After first startup, rotate/remove the bootstrap password variable; the existing admin password is not reset on restart.

The existing Telegram deployment additionally requires `TELEGRAM_BOT_TOKEN`. It is independent of this API.

Start the API with `uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}`. Keep the service behind HTTPS. The host must support a continuously running ASGI/Python web service and persistent writable storage; a bot-only runner or short-lived GitHub Actions job is not a suitable API host. Configure a persistent disk before production use, otherwise SQLite data and audit history may be lost on redeploy.

### Accounts, privacy, and audit

Registration is self-service but every account starts `pending`; users cannot access case data until an administrator approves them. There is intentionally no SMS verification, per the requested policy. The first admin is seeded via environment variables. Admin endpoints list accounts, approve/reject registration, and inspect audit activity. Logs record registration, successful/failed login (failed attempts are not linked to submitted phone numbers), approvals/rejections, case-type views, searches (method and result count only—not the query), details views, PDF downloads, and sessions searches. Passwords are Argon2 hashes and are never logged. Only admin accounts can view audit history.

Because this app handles court records, grant access only to authorized users, enforce HTTPS, configure backups and audit-log retention, and review applicable privacy/security requirements before public deployment. Add rate limiting at the reverse proxy/host for production.

### API routes

- `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/me`
- `GET /api/case-types`, `GET /api/cases/search?q=...`, `GET /api/cases/detail?case_number=...`
- `GET /api/sessions?date=YYYY-MM-DD&judge=...`, `POST /api/cases/pdf`
- Admin: `GET /api/admin/users?status=pending`, `POST /api/admin/users/{id}/decision`, `GET /api/admin/activity`
- `GET /api/health`

All endpoints except health, register, and login require a bearer access token. API docs are off by default; set `API_DOCS_ENABLED=true` only when needed.

### Android client

Open `android/` with Android Studio. Set the Gradle project property `API_BASE_URL` to the HTTPS base URL ending in `/`, e.g. `API_BASE_URL=https://your-api.example/`; the app has no production endpoint built in until this is configured. Build/install the debug APK from Android Studio. The starter client includes Arabic login/registration, pending approval messaging, case search, and an admin approval/activity view. Tokens are held in memory (not persisted), so users sign in again after closing the process. Do not distribute a build that points to HTTP or a test server.

## Telegram bot

The existing bot remains runnable with `python bot.py` after configuring `TELEGRAM_BOT_TOKEN` and `GOOGLE_DRIVE_FILE_ID`.
