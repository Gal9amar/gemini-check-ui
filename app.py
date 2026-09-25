from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import importlib

import requests

# Import dynamically so environments that use a nonstandard interpreter or
# virtual environment do not report Flask's static import as unresolved.
_flask = importlib.import_module("flask")
Flask = _flask.Flask
jsonify = _flask.jsonify
render_template = _flask.render_template
request = _flask.request
Response = _flask.Response
session = _flask.session
redirect = _flask.redirect

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import scanner

BASE_DIR = Path(__file__).resolve().parent
SCANNER = BASE_DIR / "scanner.py"
DB_PATH = BASE_DIR / "scanner.db"
# scanner.py writes live scan output here; app.py syncs new rows into scan_results.
RESULTS_CSV = BASE_DIR / "gemini_results.csv"
# Regenerated from the DB's active panels before every scan - the hand-off
# file scanner.py itself reads, since it doesn't talk to the DB directly.
ACTIVE_PANELS_FEED = BASE_DIR / "_active_panels.txt"

# Hybrid DB backend: local scanner.db file by default, or Turso (remote libSQL)
# when a Turso URL is configured - useful on hosts like Render whose local
# disk isn't guaranteed to survive a redeploy or restart.
TURSO_DATABASE_URL = (os.environ.get("TURSO_DATABASE_URL") or os.environ.get("TURSO_URL") or "").strip()
# Use the plain HTTP protocol instead of the WebSocket one ("libsql://" -> "wss://").
# Some networks/hosts block the WebSocket upgrade; HTTP works everywhere and is
# just as correct for the request-per-call pattern this file uses.
if TURSO_DATABASE_URL.startswith("libsql://"):
    TURSO_DATABASE_URL = "https://" + TURSO_DATABASE_URL[len("libsql://"):]
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
USE_TURSO = bool(TURSO_DATABASE_URL)

if USE_TURSO:
    import importlib

    libsql_client = importlib.import_module("libsql_client")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)

# Basic Auth gate for the whole app - only enforced when APP_PASSWORD is set,
# so local development without it stays open. Set APP_USERNAME / APP_PASSWORD
# as environment variables before exposing this publicly (e.g. on Render).
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()

# Email one-time-code login: send a 6-digit code to your own inbox and
# require it to unlock a session. Takes priority over Basic Auth when
# configured. Neither the login page nor the API ever show or ask for the
# email address - it's fixed to LOGIN_EMAIL.
#
# Sent via the Resend HTTP API (https://resend.com), not raw SMTP: Render
# (like many free hosts) blocks outbound SMTP ports to stop spam, but plain
# HTTPS is never blocked. RESEND_FROM_ADDRESS defaults to Resend's shared
# "onboarding@resend.dev" sender, which works with no domain setup as long
# as it's only sending to the account owner's own verified address.
LOGIN_EMAIL = os.environ.get("GMAIL_ADDRESS", "").strip()
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM_ADDRESS = os.environ.get("RESEND_FROM_ADDRESS", "onboarding@resend.dev").strip()
OTP_LOGIN_ENABLED = bool(LOGIN_EMAIL and RESEND_API_KEY)
OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5

_otp_lock = threading.Lock()
_pending_otp: dict = {}


def send_email(subject: str, text: str):
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={"from": RESEND_FROM_ADDRESS, "to": LOGIN_EMAIL, "subject": subject, "text": text},
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f"Resend {response.status_code}: {response.text[:200]}")


def notify_new_links(rows):
    lines = [
        f"{row['activation_url']}\n  Device: {row['device_id']}  Mobile: {row['mobile_number']}"
        for row in rows
    ]
    subject = f"נמצאו {len(rows)} קישורי הפעלה חדשים - Gemini Scanner"
    text = "הסריקה מצאה קישורי הפעלה חדשים:\n\n" + "\n\n".join(lines)
    send_email(subject, text)


def send_login_code():
    code = f"{random.randint(0, 999999):06d}"
    send_email(
        "קוד התחברות - Gemini Scanner",
        f"קוד ההתחברות שלך ל-Gemini Scanner: {code}\nהקוד בתוקף ל-10 דקות.",
    )
    # Only becomes verifiable once the email genuinely went out - a failed
    # send must not leave a code pending that the user can never see.
    with _otp_lock:
        _pending_otp.clear()
        _pending_otp.update(code=code, expires_at=time.time() + OTP_TTL_SECONDS, attempts=0)


def check_login_code(code: str) -> tuple[bool, str]:
    with _otp_lock:
        if not _pending_otp.get("code"):
            return False, "לא נשלח קוד. לחץ על שליחת קוד קודם."
        if time.time() > _pending_otp["expires_at"]:
            _pending_otp.clear()
            return False, "הקוד פג תוקף. בקש קוד חדש."
        _pending_otp["attempts"] += 1
        if _pending_otp["attempts"] > OTP_MAX_ATTEMPTS:
            _pending_otp.clear()
            return False, "יותר מדי ניסיונות שגויים. בקש קוד חדש."
        if code != _pending_otp["code"]:
            return False, "קוד שגוי."
        _pending_otp.clear()
        return True, ""


UNAUTHENTICATED_PATHS = {"/login", "/login/send", "/login/verify"}


@app.before_request
def require_auth():
    if OTP_LOGIN_ENABLED:
        if request.path in UNAUTHENTICATED_PATHS or request.path.startswith("/static/"):
            return
        if not session.get("authed"):
            # A JSONP endpoint is loaded via a <script src> tag, so a plain
            # 401 JSON body never calls the client's callback - the request
            # just silently times out and the panel/results lists look
            # empty. Respond in valid JSONP so the client notices immediately
            # and can redirect to /login instead of hanging for 8 seconds.
            if request.path.startswith("/api/dashboard/"):
                callback = request.args.get("callback", "")
                if re.fullmatch(r"[A-Za-z_$][\w$]*", callback):
                    return Response(
                        f"{callback}({{\"__unauthenticated__\": true}});",
                        mimetype="application/javascript",
                    )
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required."}), 401
            return redirect("/login")
        return
    if not APP_PASSWORD:
        return
    auth = request.authorization
    if not auth or auth.username != APP_USERNAME or auth.password != APP_PASSWORD:
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Gemini Scanner"'},
        )


@app.get("/login")
def login_page():
    if session.get("authed"):
        return redirect("/")
    return render_template("login.html")


@app.post("/login/send")
def login_send():
    if not OTP_LOGIN_ENABLED:
        return jsonify({"ok": False, "error": "התחברות בקוד לא הוגדרה בשרת."}), 500
    try:
        send_login_code()
    except Exception as e:
        return jsonify({"ok": False, "error": f"שליחת המייל נכשלה: {e}"}), 500
    return jsonify({"ok": True})


@app.post("/login/verify")
def login_verify():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code", "")).strip()
    ok, error = check_login_code(code)
    if not ok:
        return jsonify({"ok": False, "error": error}), 401
    session["authed"] = True
    session.permanent = True
    return jsonify({"ok": True})


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")

state = {
    "running": False,
    "progress": 0,
    "step": "Idle",
    "panels": 0,
    "devices": 0,
    "numbers": 0,
    "processed": 0,
    "links": 0,
    "current_device": "",
    "current_number": "",
    "logs": [],
    "statuses": {},
    "return_code": None,
}
proc = None
lock = threading.Lock()
MAX_LOGS = 500


# ==================== DATABASE ====================

class _TursoRow:
    """Mimics sqlite3.Row: key/index access, dict(row), and list(row) == values in column order."""

    __slots__ = ("_data",)

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return list(self._data.keys())

    def __iter__(self):
        return iter(self._data.values())

    def __len__(self):
        return len(self._data)


class _TursoCursor:
    """Makes a libsql ResultSet look like the sqlite3 cursor this file was written against."""

    __slots__ = ("_rows", "rowcount", "lastrowid", "description")

    def __init__(self, result):
        self._rows = [_TursoRow(row.asdict()) for row in result.rows]
        self.rowcount = result.rows_affected
        self.lastrowid = result.last_insert_rowid
        self.description = [(c,) for c in result.columns]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class TursoConnection:
    """
    Thin sqlite3.Connection-like wrapper around a libsql_client.ClientSync.

    A fresh client (with its own background thread and event loop) is
    created per request and closed when done - NOT shared as a long-lived
    singleton. A shared client's single background thread can wedge if any
    one request against it ever hangs or breaks, which then silently jams
    every future request forever (no crash, no restart, just permanent
    502s) since they all queue up behind the same stuck executor.
    """

    def __init__(self):
        self._client = libsql_client.create_client_sync(
            url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN or None
        )

    def execute(self, sql, params=()):
        return _TursoCursor(self._client.execute(sql, list(params) if params else None))

    def executemany(self, sql, seq_of_params, chunk_size=200):
        all_params = [list(params) for params in seq_of_params]
        for i in range(0, len(all_params), chunk_size):
            chunk = all_params[i:i + chunk_size]
            self._client.batch([(sql, params) for params in chunk])

    def commit(self):
        pass  # every statement above is already durable on the server once it returns

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def get_db():
    if USE_TURSO:
        return TursoConnection()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT NOT NULL,
                key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                reason TEXT,
                disabled_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial_number INTEGER,
                device_id TEXT,
                mobile_number TEXT,
                status TEXT,
                activation_url TEXT,
                nepal_date TEXT,
                nepal_time TEXT,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS auto_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                message TEXT,
                panels INTEGER,
                devices INTEGER,
                numbers INTEGER,
                links INTEGER,
                statuses_json TEXT
            )
        """)
        migrate_auto_runs_table(db)
        db.commit()
    migrate_legacy_files()


def migrate_auto_runs_table(db):
    """Upgrade an auto_runs table created by an earlier version of this file
    (which only had ran_at/status/message) to the current column set."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(auto_runs)")}
    if "started_at" not in columns:
        if "ran_at" in columns:
            db.execute("ALTER TABLE auto_runs RENAME COLUMN ran_at TO started_at")
        else:
            db.execute("ALTER TABLE auto_runs ADD COLUMN started_at TEXT")
    for name, coltype in (
        ("finished_at", "TEXT"),
        ("panels", "INTEGER"),
        ("devices", "INTEGER"),
        ("numbers", "INTEGER"),
        ("links", "INTEGER"),
        ("statuses_json", "TEXT"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE auto_runs ADD COLUMN {name} {coltype}")


def migrate_legacy_files():
    """One-time import of any pre-existing gemini_results.csv into a freshly created DB."""
    with get_db() as db:
        results_count_row = db.execute("SELECT COUNT(*) c FROM scan_results").fetchone()
        results_empty = (results_count_row["c"] if results_count_row else 0) == 0
        if results_empty and RESULTS_CSV.exists():
            sync_results_from_csv(db)


def sync_results_from_csv(db):
    """Import any CSV rows written by scanner.py that aren't in scan_results yet."""
    if not RESULTS_CSV.exists():
        return
    with RESULTS_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    synced = db.execute("SELECT value FROM sync_meta WHERE key = 'csv_rows_synced'").fetchone()
    synced_count = int(synced["value"]) if synced else 0
    new_rows = rows[synced_count:]
    if new_rows:
        now = datetime.now(timezone.utc).isoformat()
        db.executemany(
            "INSERT INTO scan_results (serial_number, device_id, mobile_number, status, activation_url, "
            "nepal_date, nepal_time, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.get("serial_number") or None,
                    row.get("device_id"),
                    row.get("mobile_number"),
                    row.get("status"),
                    row.get("activation_url"),
                    row.get("nepal_date"),
                    row.get("nepal_time"),
                    now,
                )
                for row in new_rows
            ],
        )
        db.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('csv_rows_synced', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(len(rows)),),
        )
        db.commit()


def panel_key(value: str):
    return value.split("|", 1)[0].strip().rstrip("/").casefold()


def panel_is_reachable(value: str):
    url = value.split("|", 1)[0].strip()
    if not url.startswith(("http://", "https://")):
        return False
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "GeminiScanner/1.0"})
        with urlopen(req, timeout=7) as response:
            return response.status < 500
    except HTTPError as error:
        return error.code < 500
    except Exception:
        return False


def panel_extracts_targets(value: str) -> bool:
    """True if scanner.py can pull at least one Firebase target out of this panel entry."""
    original_url = value.split("|", 1)[0].strip()
    try:
        if scanner.extract_panel_urls(original_url):
            return True
        if scanner.extract_panel_urls(value):
            return True
        if scanner.extract_panel_m_entries(original_url):
            return True
        if scanner.extract_panel_m_entries(value):
            return True
    except Exception:
        return False
    return False


def write_active_panels_feed(db):
    """Write the currently-active panels to a plain-text file for scanner.py to read."""
    rows = db.execute("SELECT value FROM panels WHERE status = 'active' ORDER BY id").fetchall()
    ACTIVE_PANELS_FEED.write_text(
        "\n".join(row["value"] for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return len(rows)


MAX_AUTO_RUNS = 500


def start_auto_run(started_at: str) -> int:
    """Record that an automatic scan is about to be triggered. Returns the new row's id
    so the scan's outcome can be filled in later via finish_auto_run()."""
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO auto_runs (started_at, status) VALUES (?, 'running')",
            (started_at,),
        )
        run_id = cur.lastrowid
        db.execute(
            "DELETE FROM auto_runs WHERE id NOT IN "
            "(SELECT id FROM auto_runs ORDER BY id DESC LIMIT ?)",
            (MAX_AUTO_RUNS,),
        )
        db.commit()
    return run_id


def finish_auto_run(
    run_id: int,
    finished_at: str,
    status: str,
    message: str | None = None,
    panels: int | None = None,
    devices: int | None = None,
    numbers: int | None = None,
    links: int | None = None,
    statuses: dict | None = None,
):
    """Fill in an automatic run's outcome - either right away (it never started) or
    once the scan subprocess exits (full stats)."""
    with get_db() as db:
        db.execute(
            "UPDATE auto_runs SET finished_at = ?, status = ?, message = ?, panels = ?, "
            "devices = ?, numbers = ?, links = ?, statuses_json = ? WHERE id = ?",
            (
                finished_at, status, message, panels, devices, numbers, links,
                json.dumps(statuses) if statuses else None, run_id,
            ),
        )
        db.commit()


def log(line: str):
    line = line.rstrip()
    if not line:
        return
    with lock:
        state["logs"].append(line)
        state["logs"] = state["logs"][-MAX_LOGS:]
        if line.startswith("Panels to scan:"):
            m = re.search(r"(\d+)", line)
            if m: state["panels"] = int(m.group(1))
        if "Total Online Devices" in line:
            m = re.search(r"(\d+)", line)
            if m: state["devices"] = int(m.group(1))
        if "Total Unique Number Candidates" in line:
            m = re.search(r"(\d+)", line)
            if m: state["numbers"] = int(m.group(1))
        if "LINK ->" in line:
            state["links"] += 1
        m = re.search(r"\[(\d+)\/(\d+)\]\s+Device:\s*(.*?)\s*\|\s*ending:\s*(\d{4})", line)
        if m:
            state["processed"] = int(m.group(1)) - 1
            state["current_device"] = m.group(3)
            state["current_number"] = "••••••" + m.group(4)
            # Keep the public pipeline stage stable while an individual device
            # is processed. "Activation" was not a recognised stage in
            # calculate_progress(), so the percentage stopped changing.
            state["step"] = "OTP / Activation"
        if line.startswith("STEP 1"):
            state["step"] = "Loading Panels"
        elif line.startswith("STEP 2"):
            state["step"] = "Scanning Messages"
        elif line.startswith("STEP 3"):
            state["step"] = "OTP / Activation"
        elif line.startswith("FINAL SUMMARY"):
            state["step"] = "Completed"
        m = re.search(r"Status:\s*([\w-]+)", line)
        if m:
            status = m.group(1)
            state["statuses"][status] = state["statuses"].get(status, 0) + 1
            state["processed"] += 1


def reader(p, before_id=0, auto_run_id=None):
    global proc
    try:
        for raw in iter(p.stdout.readline, ""):
            log(raw)
    finally:
        p.stdout.close()
        code = p.wait()
        with lock:
            state["running"] = False
            state["return_code"] = code
            if state["step"] != "Completed":
                state["step"] = "Stopped" if code != 0 else "Completed"
            state["progress"] = 100 if code == 0 else state["progress"]
            run_snapshot = {
                "panels": state["panels"], "devices": state["devices"],
                "numbers": state["numbers"], "links": state["links"],
                "statuses": dict(state["statuses"]),
            }
        proc = None
        with get_db() as db:
            sync_results_from_csv(db)
            new_links = db.execute(
                "SELECT device_id, mobile_number, activation_url FROM scan_results "
                "WHERE id > ? AND TRIM(COALESCE(activation_url, '')) != ''",
                (before_id,),
            ).fetchall()
        if auto_run_id is not None:
            try:
                finish_auto_run(
                    auto_run_id,
                    datetime.now(timezone.utc).isoformat(),
                    "completed" if code == 0 else "stopped",
                    panels=run_snapshot["panels"],
                    devices=run_snapshot["devices"],
                    numbers=run_snapshot["numbers"],
                    links=run_snapshot["links"],
                    statuses=run_snapshot["statuses"],
                )
            except Exception as e:
                log(f"Failed to record automatic run completion: {e}")
        if new_links and RESEND_API_KEY and LOGIN_EMAIL:
            try:
                notify_new_links(new_links)
            except Exception as e:
                log(f"Link notification email failed: {e}")


def reset_state():
    with lock:
        state.update({
            "running": True, "progress": 0, "step": "Starting",
            "panels": 0, "devices": 0, "numbers": 0, "processed": 0,
            "links": 0, "current_device": "", "current_number": "",
            "logs": [], "statuses": {}, "return_code": None,
        })


def calculate_progress():
    with lock:
        step = state["step"]
        if step == "Loading Panels": p = 10
        elif step == "Scanning Messages":
            total = state["devices"]
            p = 35 if total == 0 else 15 + min(30, int(state["processed"] / max(total, 1) * 30))
        elif step == "OTP / Activation":
            total = state["numbers"]
            p = 65 if total == 0 else 65 + min(34, int(state["processed"] / max(total, 1) * 34))
        elif step == "Completed": p = 100
        elif step == "Stopped": p = state["progress"]
        else: p = state["progress"]
        state["progress"] = p
        return p


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    calculate_progress()
    with lock:
        return jsonify(dict(state))


@app.get("/api/results")
def api_results():
    with get_db() as db:
        sync_results_from_csv(db)
        rows = db.execute(
            "SELECT serial_number, device_id, mobile_number, status, activation_url, nepal_date, nepal_time "
            "FROM scan_results ORDER BY id DESC LIMIT 500"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/links")
def api_links():
    with get_db() as db:
        sync_results_from_csv(db)
        rows = db.execute(
            "SELECT device_id, mobile_number, activation_url, nepal_date, nepal_time FROM scan_results "
            "WHERE TRIM(COALESCE(activation_url, '')) != '' ORDER BY id DESC LIMIT 5000"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/auto-runs")
def api_auto_runs():
    with get_db() as db:
        rows = db.execute(
            "SELECT started_at, finished_at, status, message, panels, devices, numbers, links, statuses_json "
            "FROM auto_runs ORDER BY id DESC LIMIT 200"
        ).fetchall()
    runs = []
    for row in rows:
        run = dict(row)
        statuses_json = run.pop("statuses_json", None)
        run["statuses"] = json.loads(statuses_json) if statuses_json else None
        runs.append(run)
    return jsonify({
        "interval_minutes": AUTO_SCAN_INTERVAL_MINUTES,
        "runs": runs,
    })


def db_table_names(db):
    return [
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def table_count(db, name: str) -> int:
    row = db.execute(f'SELECT COUNT(*) c FROM "{name}"').fetchone()
    return row["c"] if row else 0


@app.get("/api/db/backend")
def api_db_backend():
    return jsonify({"backend": "Turso" if USE_TURSO else "SQLite"})


@app.get("/api/db/tables")
def api_db_tables():
    with get_db() as db:
        names = db_table_names(db)
        tables = [{"name": name, "count": table_count(db, name)} for name in names]
    return jsonify(tables)


@app.get("/api/db/tables/<name>")
def api_db_table(name: str):
    with get_db() as db:
        if name not in db_table_names(db):
            return jsonify({"error": "Unknown table."}), 404
        cur = db.execute(f'SELECT * FROM "{name}" ORDER BY rowid DESC LIMIT 1000')
        columns = [c[0] for c in cur.description]
        rows = [list(row) for row in cur.fetchall()]
        total = table_count(db, name)
    return jsonify({"columns": columns, "rows": rows, "total": total})


@app.get("/api/panels")
def api_panels():
    with get_db() as db:
        rows = db.execute("SELECT id, value FROM panels WHERE status = 'active' ORDER BY id").fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/panels/inactive")
def api_inactive_panels():
    with get_db() as db:
        rows = db.execute(
            "SELECT id, value, reason, disabled_at FROM panels WHERE status = 'inactive' ORDER BY id DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/panels/audit")
def audit_panels():
    """Mark unreachable active panels as inactive."""
    with get_db() as db:
        rows = db.execute("SELECT id, value FROM panels WHERE status = 'active'").fetchall()
        from concurrent.futures import ThreadPoolExecutor
        values = [row["value"] for row in rows]
        with ThreadPoolExecutor(max_workers=16) as pool:
            reachable = dict(zip(values, pool.map(panel_is_reachable, values)))
        now = datetime.now(timezone.utc).isoformat()
        moved = 0
        for row in rows:
            if not reachable[row["value"]]:
                db.execute(
                    "UPDATE panels SET status = 'inactive', reason = 'connection_error', disabled_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                moved += 1
        db.commit()
    return jsonify({"ok": True, "moved": moved})


@app.post("/api/panels/audit-inactive")
def audit_inactive_panels():
    """Recheck inactive panels; restore any that are reachable again to the active list."""
    with get_db() as db:
        rows = db.execute("SELECT id, value FROM panels WHERE status = 'inactive'").fetchall()
        from concurrent.futures import ThreadPoolExecutor
        values = [row["value"] for row in rows]
        with ThreadPoolExecutor(max_workers=16) as pool:
            reachable = dict(zip(values, pool.map(panel_is_reachable, values)))
        restored = 0
        for row in rows:
            if reachable[row["value"]]:
                db.execute(
                    "UPDATE panels SET status = 'active', reason = NULL, disabled_at = NULL WHERE id = ?",
                    (row["id"],),
                )
                restored += 1
        db.commit()
    return jsonify({"ok": True, "restored": restored})


@app.get("/api/panels/review")
def api_review_panels():
    with get_db() as db:
        rows = db.execute(
            "SELECT id, value, disabled_at FROM panels WHERE status = 'review' ORDER BY id DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/panels/audit-extract")
def audit_extract_panels():
    """
    Flag active panels that scanner.py cannot pull any Firebase target out of
    (bare homepage links, Telegram invites, shortlinks, etc.) as 'review' so
    they stop silently vanishing from the scan and the person can look at
    them. Also promotes previously-flagged panels back to active if a parser
    improvement (like ?m= support) can now extract a target from them.
    """
    with get_db() as db:
        active_rows = db.execute("SELECT id, value FROM panels WHERE status = 'active'").fetchall()
        review_rows = db.execute("SELECT id, value FROM panels WHERE status = 'review'").fetchall()
        now = datetime.now(timezone.utc).isoformat()
        flagged = 0
        for row in active_rows:
            if not panel_extracts_targets(row["value"]):
                db.execute(
                    "UPDATE panels SET status = 'review', reason = 'no_firebase_url', disabled_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                flagged += 1
        restored = 0
        for row in review_rows:
            if panel_extracts_targets(row["value"]):
                db.execute(
                    "UPDATE panels SET status = 'active', reason = NULL, disabled_at = NULL WHERE id = ?",
                    (row["id"],),
                )
                restored += 1
        db.commit()
    return jsonify({"ok": True, "flagged": flagged, "restored": restored})


@app.get("/api/dashboard/<kind>")
def dashboard_data(kind: str):
    """Read-only JSONP endpoints avoid browser extensions that intercept XHR."""
    callback = request.args.get("callback", "")
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", callback):
        return jsonify({"error": "Invalid callback."}), 400
    if kind == "status":
        payload = api_status().get_json()
    elif kind == "results":
        payload = api_results().get_json()
    elif kind == "panels":
        payload = api_panels().get_json()
    elif kind == "inactive-panels":
        payload = api_inactive_panels().get_json()
    elif kind == "review-panels":
        payload = api_review_panels().get_json()
    elif kind == "links":
        payload = api_links().get_json()
    elif kind == "auto-runs":
        payload = api_auto_runs().get_json()
    elif kind == "db-backend":
        payload = api_db_backend().get_json()
    elif kind == "db-tables":
        payload = api_db_tables().get_json()
    elif kind.startswith("db-table-"):
        table_name = kind[len("db-table-"):]
        result = api_db_table(table_name)
        response = result[0] if isinstance(result, tuple) else result
        payload = response.get_json()
    else:
        return jsonify({"error": "Unknown dashboard data."}), 404
    return Response(f"{callback}({json.dumps(payload)});", mimetype="application/javascript")


@app.post("/api/panels")
def add_panel():
    body = request.get_json(silent=True) or request.form
    value = str(body.get("value", "")).strip()
    if not value:
        return jsonify({"ok": False, "error": "יש להזין כתובת Panel."}), 400
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        cur = db.execute(
            "INSERT OR IGNORE INTO panels (value, key, status, created_at) VALUES (?, ?, 'active', ?)",
            (value, panel_key(value), now),
        )
        db.commit()
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "ה-Panel הזה כבר קיים ברשימה (פעיל, לא תקין או לבדיקה)."}), 409
    return jsonify({"ok": True})


URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def extract_urls(text: str):
    """Pull plain URLs out of a blob of free text, trimming trailing punctuation."""
    found = []
    seen = set()
    for match in URL_RE.findall(text):
        url = match.rstrip(".,;:!?)]}־’”")
        if url and url not in seen:
            seen.add(url)
            found.append(url)
    return found


@app.post("/api/panels/bulk")
def add_panels_bulk():
    """Extract every URL out of a pasted block of text and add each as a panel."""
    body = request.get_json(silent=True) or request.form
    text = str(body.get("text", ""))
    urls = extract_urls(text)
    if not urls:
        return jsonify({"ok": False, "error": "No links found in the pasted text."}), 400
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    with get_db() as db:
        for url in urls:
            cur = db.execute(
                "INSERT OR IGNORE INTO panels (value, key, status, created_at) VALUES (?, ?, 'active', ?)",
                (url, panel_key(url), now),
            )
            added += cur.rowcount
        db.commit()
    return jsonify({"ok": True, "found": len(urls), "added": added, "skipped": len(urls) - added})


@app.delete("/api/panels/<int:panel_id>")
def delete_panel(panel_id: int):
    with get_db() as db:
        cur = db.execute("DELETE FROM panels WHERE id = ?", (panel_id,))
        db.commit()
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "ה-Panel לא נמצא."}), 404
    return jsonify({"ok": True})


@app.post("/api/panels/<int:panel_id>/remove")
def remove_panel_form(panel_id: int):
    return delete_panel(panel_id)


def start_scan(extra_args: dict | None = None, auto_run_id: int | None = None):
    """Launch a scan. Shared by the /api/start route and the automatic scheduler.
    Returns (ok, error, status_code)."""
    global proc
    with lock:
        if state["running"]:
            return False, "A scan is already running.", 409
    if not SCANNER.exists():
        return False, "scanner.py not found.", 500

    with get_db() as db:
        panel_count = write_active_panels_feed(db)
        row = db.execute("SELECT COALESCE(MAX(id), 0) m FROM scan_results").fetchone()
        before_id = row["m"] if row else 0
    if panel_count == 0:
        return False, "No active panels to scan.", 400

    body = extra_args or {}
    # The scanner writes into a pipe, where Python normally buffers output.
    # Unbuffered mode lets the dashboard receive each progress line promptly.
    args = [sys.executable, "-u", str(SCANNER), "--panels", str(ACTIVE_PANELS_FEED)]
    for key in ("limit", "skip", "message-limit", "otp-timeout", "poll-interval"):
        if key in body and str(body[key]).strip():
            args += [f"--{key}", str(body[key])]

    reset_state()
    proc = subprocess.Popen(
        args,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    threading.Thread(target=reader, args=(proc, before_id, auto_run_id), daemon=True).start()
    return True, None, 200


@app.post("/api/start")
def start():
    ok, error, status_code = start_scan(request.get_json(silent=True) or {})
    if not ok:
        return jsonify({"ok": False, "error": error}), status_code
    return jsonify({"ok": True})


@app.post("/api/stop")
def stop():
    global proc
    if proc and proc.poll() is None:
        proc.terminate()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No running scan."}), 409


@app.get("/api/export")
def export_csv():
    with get_db() as db:
        sync_results_from_csv(db)
        rows = db.execute(
            "SELECT serial_number, device_id, mobile_number, status, activation_url, nepal_date, nepal_time "
            "FROM scan_results WHERE TRIM(COALESCE(activation_url, '')) != '' ORDER BY id"
        ).fetchall()
    if not rows:
        return jsonify({"error": "No results yet."}), 404
    fields = ["serial_number", "device_id", "mobile_number", "status", "activation_url", "nepal_date", "nepal_time"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(dict(row) for row in rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=gemini_activation_links.csv"},
    )


# Automatic recurring scan. Runs inside this same process (a background
# thread), so it only fires reliably while the process stays alive - fine
# here since an external uptime pinger keeps the free Render instance awake.
# Set AUTO_SCAN_INTERVAL_MINUTES=0 to disable.
AUTO_SCAN_INTERVAL_MINUTES = int(os.environ.get("AUTO_SCAN_INTERVAL_MINUTES", "30") or "0")


def auto_scan_loop():
    interval_seconds = AUTO_SCAN_INTERVAL_MINUTES * 60
    while True:
        time.sleep(interval_seconds)
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            run_id = start_auto_run(started_at)
        except Exception as e:
            log(f"Failed to record automatic run: {e}")
            run_id = None
        try:
            ok, error, _ = start_scan(auto_run_id=run_id)
        except Exception as e:
            ok, error = False, str(e)
            log(f"Scheduled scan failed to start: {e}")
        # start_scan() only launches the subprocess - reader() finishes the
        # run record once it exits. Here we only need to close out the record
        # for a run that never made it that far (already running, no active
        # panels, scanner.py missing, or an unexpected exception above).
        if not ok and run_id is not None:
            try:
                finish_auto_run(run_id, datetime.now(timezone.utc).isoformat(), "skipped", message=error)
            except Exception as e:
                log(f"Failed to record automatic run outcome: {e}")


init_db()

if AUTO_SCAN_INTERVAL_MINUTES > 0:
    threading.Thread(target=auto_scan_loop, daemon=True).start()

if __name__ == "__main__":
    # 0.0.0.0 + $PORT so this also works as a Render web service (which injects
    # PORT and needs the app to listen on all interfaces, not just localhost).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
