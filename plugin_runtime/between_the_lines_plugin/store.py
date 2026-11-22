"""Durable work queue shared by short hook processes and the local MCP server."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


IMMUTABLE_JOB_FIELDS = {
    "id", "workspaceRoot", "eventId", "promptEventId", "source", "sessionId", "turnId",
    "prompt", "promptHash", "path", "language", "beforeSource", "afterSource", "afterHash",
    "symbolId", "symbolName", "codeHash", "created", "modelAtCapture",
}
TRANSITIONS = {
    "pending": {"pending", "previewed", "skipped"},
    "previewed": {"previewed", "applied", "skipped"},
    "applied": {"applied", "undone"},
    "skipped": {"skipped"},
    "undone": {"undone"},
}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def default_data_dir():
    configured = os.environ.get("BETWEEN_THE_LINES_PLUGIN_DATA")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError("Plugin data directory must be absolute.")
        return path
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "between-the-lines/codex-plugin" if local else Path.home() / ".local/share/between-the-lines/codex-plugin"


class Store:
    def __init__(self, data_dir=None):
        path = Path(data_dir) if data_dir is not None else default_data_dir()
        if not path.is_absolute():
            raise ValueError("Plugin data directory must be absolute.")
        path.mkdir(parents=True, exist_ok=True)
        self.data_dir = path.resolve(strict=True)
        self.database = self.data_dir / "plugin.sqlite3"
        if self.database.is_symlink():
            raise ValueError("Plugin database cannot be a symbolic link.")
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspaces (path TEXT PRIMARY KEY, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, turn TEXT NOT NULL,
                    reason TEXT, continued_turn TEXT, synthetic_turn TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, state TEXT NOT NULL,
                    record_json TEXT NOT NULL, created TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(workspace,state,created);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def session(self, session_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def register_prompt(self, session_id, workspace, turn_id):
        """Only the hook coordinator registers a workspace after capturing a prompt."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row and row["workspace"] != workspace:
                raise ValueError("A captured session cannot switch projects.")
            db.execute("INSERT OR IGNORE INTO workspaces VALUES (?,?)", (workspace, timestamp()))
            if row and row["turn"] == turn_id:
                return
            db.execute("INSERT INTO sessions VALUES (?,?,?,NULL,NULL,NULL) ON CONFLICT(id) DO UPDATE SET turn=excluded.turn,reason=NULL,synthetic_turn=NULL",
                       (session_id, workspace, turn_id))

    def mark_synthetic_prompt(self, session_id, prompt, turn_id):
        """Recognize only the exact continuation issued to this captured session."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reason FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row or not row["reason"] or row["reason"] != prompt:
                return False
            db.execute("UPDATE sessions SET synthetic_turn=? WHERE id=?", (turn_id, session_id))
            return True

    def issue_continuation(self, session_id, turn_id, reason):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT turn,continued_turn FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row or row["turn"] != turn_id or row["continued_turn"] == turn_id:
                return False
            db.execute("UPDATE sessions SET reason=?,continued_turn=? WHERE id=?", (reason, turn_id, session_id))
            return True

    def approved_workspace(self, workspace):
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            raise ValueError("Use an absolute captured workspace path.")
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Workspace must be a local directory.")
        with self.connection() as db:
            row = db.execute("SELECT path FROM workspaces WHERE path=?", (str(root),)).fetchone()
        if not row:
            raise ValueError("This workspace has not been registered by a trusted prompt hook.")
        return str(root)

    @staticmethod
    def _identifier(job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{64}", job_id):
            raise ValueError("Invalid annotation job ID.")
        return job_id

    def save_job(self, record):
        record = dict(record)
        self._identifier(record.get("id"))
        workspace = self.approved_workspace(record.get("workspaceRoot"))
        if not IMMUTABLE_JOB_FIELDS.issubset(record) or record.get("state") not in TRANSITIONS:
            raise ValueError("Annotation jobs require original provenance and a known state.")
        encoded = json.dumps(record, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Annotation job exceeds its storage budget.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT record_json FROM jobs WHERE id=?", (record["id"],)).fetchone()
            if existing:
                saved = json.loads(existing["record_json"])
                # created/model timestamps describe the first delivery; replay
                # must retain them and never reset an already completed job.
                if any(saved.get(key) != record.get(key) for key in IMMUTABLE_JOB_FIELDS - {"created", "modelAtCapture"}):
                    raise ValueError("A job ID already belongs to different captured evidence.")
                return saved
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (record["id"], workspace, record["state"], encoded, record["created"]))
        return record

    def get_job(self, workspace, job_id):
        workspace = self.approved_workspace(workspace)
        self._identifier(job_id)
        with self.connection() as db:
            row = db.execute("SELECT record_json FROM jobs WHERE id=? AND workspace=?", (job_id, workspace)).fetchone()
        if not row:
            raise ValueError("The annotation job is unavailable in this workspace.")
        return json.loads(row["record_json"])

    def update_job(self, workspace, job_id, **updates):
        workspace = self.approved_workspace(workspace)
        self._identifier(job_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM jobs WHERE id=? AND workspace=?", (job_id, workspace)).fetchone()
            if not row:
                raise ValueError("The annotation job is unavailable in this workspace.")
            saved = json.loads(row["record_json"])
            if any(key in IMMUTABLE_JOB_FIELDS and saved.get(key) != value for key, value in updates.items()):
                raise ValueError("Original job evidence cannot be changed.")
            state = updates.get("state", saved["state"])
            if state not in TRANSITIONS[saved["state"]]:
                raise ValueError("The annotation job cannot make that state transition.")
            saved.update(updates)
            db.execute("UPDATE jobs SET state=?,record_json=? WHERE id=?", (saved["state"], json.dumps(saved), job_id))
        return saved

    def pending(self, workspace):
        workspace = self.approved_workspace(workspace)
        with self.connection() as db:
            rows = db.execute("""SELECT record_json FROM jobs WHERE workspace=? AND
                              (state IN ('pending','previewed') OR
                               (state='applied' AND json_extract(record_json,'$.pendingUndo') IS NOT NULL))
                              ORDER BY created,id LIMIT 30""", (workspace,)).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    @contextmanager
    def job_lock(self, workspace, job_id):
        """One writer per job across independent hook and MCP processes."""
        job = self.get_job(workspace, job_id)
        directory = self.data_dir / "locks"
        directory.mkdir(exist_ok=True)
        # Different completed turns can create jobs for the same file. Their
        # writes must serialize as well as retries of one particular job.
        target = str(Path(job["path"]).resolve(strict=False))
        target = target.casefold() if os.name == "nt" else target
        lock_id = hashlib.sha256(target.encode("utf-8")).hexdigest()
        path = directory / (lock_id + ".lock")
        if path.is_symlink():
            raise ValueError("Annotation lock cannot be a symbolic link.")
        with path.open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("This annotation is already being updated; retry after it finishes.") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
