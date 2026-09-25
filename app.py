from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from flask import Flask, jsonify, render_template, request, Response

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import scanner

BASE_DIR = Path(__file__).resolve().parent
SCANNER = BASE_DIR / "scanner.py"
DB_PATH = BASE_DIR / "scanner.db"
PANELS_TXT = BASE_DIR / "panels.txt"
INACTIVE_PANELS_JSON = BASE_DIR / "inactive_panels.json"
RESULTS_CSV = BASE_DIR / "gemini_results.csv"
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
    import libsql_client
    _turso_client = libsql_client.create_client_sync(url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN or None)

app = Flask(__name__)

# Basic Auth gate for the whole app - only enforced when APP_PASSWORD is set,
# so local development without it stays open. Set APP_USERNAME / APP_PASSWORD
# as environment variables before exposing this publicly (e.g. on Render).
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()


@app.before_request
def require_auth():
    if not APP_PASSWORD:
        return
    auth = request.authorization
    if not auth or auth.username != APP_USERNAME or auth.password != APP_PASSWORD:
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Gemini Scanner"'},
        )

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
    """Thin sqlite3.Connection-like wrapper around a shared libsql_client.ClientSync."""

    def __init__(self, client):
        self._client = client

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
        pass  # the underlying client is a shared, long-lived singleton - never close it here

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass


def get_db():
    if USE_TURSO:
        return TursoConnection(_turso_client)
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
        db.commit()
    migrate_legacy_files()


def migrate_legacy_files():
    """One-time import of the old panels.txt / inactive_panels.json / CSV files into SQLite."""
    with get_db() as db:
        panels_count_row = db.execute("SELECT COUNT(*) c FROM panels").fetchone()
        panels_empty = (panels_count_row["c"] if panels_count_row else 0) == 0
        if panels_empty:
            now = datetime.now(timezone.utc).isoformat()
            # Import inactive entries first so a panel flagged inactive keeps that
            # status even if the same key still lingers in panels.txt.
            if INACTIVE_PANELS_JSON.exists():
                try:
                    items = json.loads(INACTIVE_PANELS_JSON.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    items = []
                inactive_params = []
                for item in items:
                    value = str(item.get("value", "")).strip()
                    if not value:
                        continue
                    inactive_params.append((value, panel_key(value), item.get("reason"), item.get("disabled_at"), now))
                if inactive_params:
                    db.executemany(
                        "INSERT OR IGNORE INTO panels (value, key, status, reason, disabled_at, created_at) "
                        "VALUES (?, ?, 'inactive', ?, ?, ?)",
                        inactive_params,
                    )
            if PANELS_TXT.exists():
                active_params = []
                for raw in PANELS_TXT.read_text(encoding="utf-8").splitlines():
                    value = raw.strip()
                    if not value or value.startswith("#"):
                        continue
                    active_params.append((value, panel_key(value), now))
                if active_params:
                    db.executemany(
                        "INSERT OR IGNORE INTO panels (value, key, status, created_at) VALUES (?, ?, 'active', ?)",
                        active_params,
                    )
            db.commit()

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


def reader(p):
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
        proc = None
        with get_db() as db:
            sync_results_from_csv(db)


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


@app.post("/api/start")
def start():
    global proc
    with lock:
        if state["running"]:
            return jsonify({"ok": False, "error": "A scan is already running."}), 409
    if not SCANNER.exists():
        return jsonify({"ok": False, "error": "scanner.py not found."}), 500

    with get_db() as db:
        panel_count = write_active_panels_feed(db)
    if panel_count == 0:
        return jsonify({"ok": False, "error": "No active panels to scan."}), 400

    body = request.get_json(silent=True) or {}
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
    threading.Thread(target=reader, args=(proc,), daemon=True).start()
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


init_db()

if __name__ == "__main__":
    # 0.0.0.0 + $PORT so this also works as a Render web service (which injects
    # PORT and needs the app to listen on all interfaces, not just localhost).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
