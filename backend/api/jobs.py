"""Runs `python -m tagger <command>` as a subprocess, one at a time, and keeps its log in data/logs/<id>.log."""
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import insert, select, update

from tagger.config import EFFORTS, PROMPTS, ROOT
from tagger.store import jobs

DATA = Path(os.getenv("DATA_DIR") or ROOT / "data")
UPLOADS, LOGS = DATA / "uploads", DATA / "logs"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
TABLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")

# Options every tagging command takes, and how each one goes on the command line.
SHARED = {"max_cost": float, "limit": int, "workers": int, "effort": "effort",
          "retry_errors": bool, "no_web_search": bool, "dry_run": bool}
COMMANDS = {
    "refresh": {},
    "pass1": {"llm": "name", "prompt": "prompt", "full": bool, "csv": "upload", **SHARED},
    "pass2": {"llm": "names", "prompt": "prompt", "from": "table", "table": "table", **SHARED},
    "pass3": {"llm": "name", "prompt": "prompt", **SHARED},
    "import": {"files": "uploads"},
}
REQUIRED = {"pass1": ["llm"], "import": ["files"]}


def prompts() -> list[str]:
    return sorted(p.name for p in PROMPTS.glob("*.md"))


def uploads() -> list[dict]:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    return sorted(({"name": p.name, "size": p.stat().st_size,
                    "uploaded_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)}
                   for p in UPLOADS.iterdir() if p.is_file()), key=lambda u: u["name"])


def _check(kind, value, key):
    if kind == "name" and not (isinstance(value, str) and NAME.fullmatch(value)):
        raise HTTPException(400, f"{key}: use letters, digits, - and _")
    if kind == "table" and not (isinstance(value, str) and TABLE.fullmatch(value)):
        raise HTTPException(400, f"{key}: lowercase letters, digits and _ only")
    if kind == "prompt" and value not in prompts():
        raise HTTPException(400, f"{key}: unknown prompt file")
    if kind == "upload" and not (isinstance(value, str) and NAME.fullmatch(value) and (UPLOADS / value).is_file()):
        raise HTTPException(400, f"{key}: upload the file first")
    if kind == "effort" and value not in EFFORTS:
        raise HTTPException(400, f"{key}: use {', '.join(EFFORTS)}")
    return value


def build_argv(command: str, args: dict) -> list[str]:
    """The command line after `python -m tagger`, from the JSON a form sent. Rejects anything not in COMMANDS."""
    spec = COMMANDS.get(command)
    if spec is None:
        raise HTTPException(400, f"Unknown command {command!r}")
    for key in REQUIRED.get(command, []):
        if not args.get(key):
            raise HTTPException(400, f"{key} is required")
    argv = [command]
    for key, value in (args or {}).items():
        kind = spec.get(key)
        if kind is None:
            raise HTTPException(400, f"{command} has no option {key!r}")
        if value in (None, "", False, []):
            continue
        flag = "--" + key.replace("_", "-")
        if kind is bool:
            argv.append(flag)
        elif kind in (int, float):
            try:
                argv += [flag, str(kind(value))]
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} must be a number")
        elif kind == "names":
            values = value if isinstance(value, list) else [value]
            for v in values:
                argv += [flag, _check("name", v, key)]
        elif kind == "uploads":
            values = value if isinstance(value, list) else [value]
            argv += [str(UPLOADS / _check("upload", v, key)) for v in values]
        elif kind == "upload":
            argv += [flag, str(UPLOADS / _check(kind, value, key))]
        else:
            argv += [flag, _check(kind, value, key)]
    return argv


PREVIEW_ROWS = 25


def preview(command: str, args: dict) -> dict:
    """What a pass would send, from the pass's own row selection (`--preview`), without calling a model."""
    if command not in ("pass1", "pass2", "pass3"):
        raise HTTPException(400, "Only the passes have a preview")
    argv = build_argv(command, args) + ["--preview", str(PREVIEW_ROWS)]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        done = subprocess.run([sys.executable, "-m", "tagger", *argv], cwd=ROOT, env=env, capture_output=True,
                              timeout=180)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "The preview took too long")
    out = done.stdout.decode("utf-8", errors="replace").strip().splitlines()
    if done.returncode != 0 or not out:
        err = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise HTTPException(400, err[-1] if err else f"Preview failed (exit code {done.returncode})")
    return json.loads(out[-1])


class JobRunner:
    """One subprocess at a time. The log goes to a file as it arrives, so clients can read it from any offset."""

    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.Lock()
        self.process = None
        self.current = None
        LOGS.mkdir(parents=True, exist_ok=True)
        with engine.begin() as conn:  # a job still 'running' at startup died with the previous server
            conn.execute(update(jobs).where(jobs.c.status == "running").values(status="lost"))

    def start(self, command: str, args: dict, user_email: str) -> dict:
        argv = build_argv(command, args)
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise HTTPException(409, f"A {self.current} job is still running. Stop it or wait for it to finish.")
            job_id = str(uuid.uuid4())
            log = open(LOGS / f"{job_id}.log", "w", encoding="utf-8", newline="\n")
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            try:
                self.process = subprocess.Popen([sys.executable, "-u", "-m", "tagger", *argv], cwd=ROOT, env=env,
                                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            except OSError as e:
                log.close()
                raise HTTPException(500, f"Could not start the command: {e}")
            self.current = command
            with self.engine.begin() as conn:
                conn.execute(insert(jobs).values(id=job_id, command=command, argv=argv, status="running",
                                                 started_by=user_email))
            log.write(f"$ python -m tagger {' '.join(argv)}\n")
            log.flush()
            threading.Thread(target=self._pump, args=(job_id, self.process, log), daemon=True).start()
        return self.get(job_id)

    def _pump(self, job_id: str, process, log) -> None:
        with log:
            for raw in iter(process.stdout.readline, b""):
                # Windows children print \r\n; the file is opened with newline="\n" so nothing doubles it
                log.write(raw.decode("utf-8", errors="replace").replace("\r\n", "\n"))

                log.flush()
            code = process.wait()
            status = "stopped" if self._stopping == job_id else "done" if code == 0 else "failed"
            log.write(f"\n[exit code {code}: {status}]\n")
        with self.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id == job_id)
                         .values(status=status, exit_code=code, finished_at=datetime.now(timezone.utc)))

    _stopping = None

    def stop(self, job_id: str) -> dict:
        job = self.get(job_id)
        with self.lock:
            if job["status"] != "running" or self.process is None or self.process.poll() is not None:
                raise HTTPException(409, "That job is not running")
            self._stopping = job_id
            self.process.terminate()  # saved rows are kept; Postgres rolls back a save in progress
        return job

    def get(self, job_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).first()
        if not row:
            raise HTTPException(404, "No such job")
        return dict(row._mapping)

    def list(self, limit: int = 50) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in
                    conn.execute(select(jobs).order_by(jobs.c.started_at.desc()).limit(limit))]

    def log(self, job_id: str, offset: int = 0) -> dict:
        """The log text from `offset` bytes on, plus the new offset and the job's status, for polling."""
        job = self.get(job_id)
        path = LOGS / f"{job_id}.log"
        text, end = "", offset
        if path.is_file():
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
            end = offset + len(data)
            text = data.decode("utf-8", errors="replace")
        return {"text": text, "offset": end, "status": job["status"]}
