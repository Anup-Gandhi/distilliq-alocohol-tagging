"""FastAPI service: sign-in, LLM key files, the Athena copy, commands with live logs, and result views."""
import csv
import io
import json
import os
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from tagger.config import DEFAULT_MODELS, EFFORTS, PROMPTS, SECRETS, load_llm_config, load_settings
from tagger.store import Store, migrate

from .auth import current_user, login, logout, public, seed_admin
from .jobs import NAME, UPLOADS, JobRunner, preview, prompts, uploads

RESULT_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
# Shown first in the Results page; any other table or view in the database can be opened by name too.
RESULT_VIEWS = ["final_results", "final_tag_summary", "llm_tag_votes", "human_tag_comparison", "first_second_tag_comparison",
                "llm_tag_comparison", "llm_tags", "llm_retags", "llm_final_tags", "topk_options", "csv_records"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set in .env")
    migrate(settings.database_url)  # once per start; commands and previews then skip schema work
    app.state.store = Store(settings.database_url)
    seed_admin(app.state.store.engine)
    app.state.jobs = JobRunner(app.state.store.engine)
    yield


app = FastAPI(title="Liquor Product Tagger", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_URL") or "http://localhost:5176"],
                   allow_methods=["*"], allow_headers=["*"])


def store(request: Request) -> Store:
    return request.app.state.store


# Auth

class Credentials(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def post_login(body: Credentials, request: Request):
    return login(store(request).engine, body.email, body.password)


@app.post("/api/auth/logout")
def post_logout(request: Request, user=Depends(current_user)):
    logout(store(request).engine, request.state.jti)
    return {"ok": True}


@app.get("/api/auth/me")
def get_me(user=Depends(current_user)):
    return public(user)


# LLM key files: secrets/<name>.json, the same files the command line reads

class KeyFile(BaseModel):
    name: str
    provider: str
    api_key: str
    model: str | None = None
    effort: str | None = None
    input_price: float | None = None
    output_price: float | None = None
    search_price: float | None = None
    base_url: str | None = None
    api_name: str | None = None  # stored on every tagged row; defaults to name


def _mask(key: str) -> str:
    return key[:6] + "…" + key[-4:] if len(key) > 12 else "…"


@app.get("/api/keys")
def get_keys(user=Depends(current_user)):
    out = []
    for path in sorted(SECRETS.glob("*.json")) if SECRETS.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("api_key"):
            continue
        out.append({**data, "name": path.stem, "api_key": _mask(data["api_key"])})
    return {"keys": out, "providers": DEFAULT_MODELS, "efforts": EFFORTS}


@app.post("/api/keys")
def post_key(body: KeyFile, user=Depends(current_user)):
    if not NAME.fullmatch(body.name):
        raise HTTPException(400, "name: use letters, digits, - and _")
    if body.provider not in DEFAULT_MODELS:
        raise HTTPException(400, f"provider: use {', '.join(DEFAULT_MODELS)}")
    data = {k: v for k, v in body.model_dump().items() if k != "name" and v not in (None, "")}
    data.setdefault("api_name", body.name)
    SECRETS.mkdir(parents=True, exist_ok=True)
    path = SECRETS / f"{body.name}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        load_llm_config(str(path))  # the same checks the command line makes (key prefix, model price, effort)
    except SystemExit as e:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(e).replace(str(path), body.name))
    return {"ok": True}


@app.delete("/api/keys/{name}")
def delete_key(name: str, user=Depends(current_user)):
    if not NAME.fullmatch(name) or not (SECRETS / f"{name}.json").is_file():
        raise HTTPException(404, "No such key file")
    (SECRETS / f"{name}.json").unlink()
    return {"ok": True}


# Source data and dashboard numbers

@app.get("/api/stats")
def get_stats(request: Request, user=Depends(current_user)):
    s = store(request)
    with s.engine.connect() as conn:
        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                  for t in ("llm_tags", "llm_retags", "llm_final_tags", "csv_records")}
        passes = [dict(conn.execute(text(
            f"SELECT count(*) AS total, count(tag) AS tagged, "
            f"count(*) FILTER (WHERE tag IS NULL AND skip_reason IS NOT NULL) AS skipped, "
            f"count(error) AS errors FROM {t}")).one()._mapping) for t in ("llm_tags", "llm_retags", "llm_final_tags")]
        final = dict(conn.execute(text(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE final_tag = 'Misc - Not a Liquor') AS misc, "
            "count(*) FILTER (WHERE final_tag IS NOT NULL AND final_tag <> 'Misc - Not a Liquor') AS named, "
            "count(*) FILTER (WHERE status LIKE 'waiting%') AS waiting, "
            "count(*) FILTER (WHERE status IN ('pass 3 failed', 'pass 3 skipped') "
            "OR status LIKE 'skipped%') AS no_tag "
            "FROM final_results")).one()._mapping)
        pending = dict(conn.execute(text(
            "SELECT (SELECT count(*) FROM topk_options o WHERE NOT EXISTS "
            "(SELECT 1 FROM llm_tags t WHERE t.source_row_id = o.source_row_id)) AS pass_1, "
            "count(*) FILTER (WHERE status = 'waiting for pass 2') AS pass_2, "
            "count(*) FILTER (WHERE status = 'waiting for pass 3') AS pass_3 FROM final_results")).one()._mapping)
        apis = [dict(r._mapping) for r in conn.execute(text(
            "SELECT api_name, count(*) AS total, count(*) FILTER (WHERE pass = 1) AS pass_1, "
            "count(*) FILTER (WHERE pass = 2) AS pass_2, count(*) FILTER (WHERE pass = 3) AS pass_3, "
            "count(error) AS errors FROM ("
            "SELECT api_name, error, 1 AS pass FROM llm_tags UNION ALL "
            "SELECT api_name, error, 2 FROM llm_retags UNION ALL "
            "SELECT api_name, error, 3 FROM llm_final_tags) x GROUP BY api_name ORDER BY total DESC"))]
        decisions = [dict(r._mapping) for r in conn.execute(
            text("SELECT status AS decision, count(*) AS n FROM final_results GROUP BY status ORDER BY n DESC"))]
    settings = load_settings()
    return {"source": s.source_stats(), "counts": counts, "passes": passes, "final": final, "pending": pending, "apis": apis, "decisions": decisions,
            "athena": {"database": settings.athena_database, "table": settings.athena_table,
                       "region": settings.aws_region, "workgroup": settings.athena_workgroup}}


# Prompts: prompts/<name>.md, read by each command when it starts, so an edit applies to the next job

# Files the passes load by name; renaming or emptying them would break that pass.
PROMPT_IN_USE = {"system_prompt.md": "Pass 1", "retag_system_prompt.md": "Pass 2",
                 "pass3_system_prompt.md": "Pass 3"}


def prompt_path(name: str):
    if not NAME.fullmatch(name) or not name.endswith(".md"):
        raise HTTPException(400, "Prompt names use letters, digits, . - _ and end in .md")
    return PROMPTS / name


class PromptBody(BaseModel):
    content: str


@app.get("/api/prompts")
def get_prompts(user=Depends(current_user)):
    return [{"name": n, "used_by": PROMPT_IN_USE.get(n), "size": (PROMPTS / n).stat().st_size,
             "updated_at": (PROMPTS / n).stat().st_mtime} for n in prompts()]


@app.get("/api/prompts/{name}")
def get_prompt(name: str, user=Depends(current_user)):
    path = prompt_path(name)
    if not path.is_file():
        raise HTTPException(404, "No such prompt")
    return {"name": name, "content": path.read_text(encoding="utf-8")}


@app.put("/api/prompts/{name}")
def put_prompt(name: str, body: PromptBody, user=Depends(current_user)):
    """Create or overwrite a prompt."""
    path = prompt_path(name)
    if not body.content.strip():
        raise HTTPException(400, "A prompt can't be empty")
    PROMPTS.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8", newline="\n")
    return {"ok": True}


# Commands

@app.get("/api/options")
def get_options(user=Depends(current_user)):
    return {"prompts": prompts(), "uploads": uploads(), "efforts": EFFORTS}


@app.post("/api/uploads")
async def post_upload(file: UploadFile, user=Depends(current_user)):
    name = os.path.basename(file.filename or "")
    if not NAME.fullmatch(name) or not name.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file with a plain name (letters, digits, . - _)")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / name).write_bytes(await file.read())
    return {"name": name}


class JobRequest(BaseModel):
    command: str
    args: dict = {}


@app.post("/api/preview")
def post_preview(body: JobRequest, user=Depends(current_user)):
    return preview(body.command, body.args)


@app.get("/api/jobs")
def get_jobs(request: Request, user=Depends(current_user)):
    return request.app.state.jobs.list()


@app.post("/api/jobs")
def post_job(body: JobRequest, request: Request, user=Depends(current_user)):
    return request.app.state.jobs.start(body.command, body.args, user.email)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request, user=Depends(current_user)):
    return request.app.state.jobs.get(job_id)


@app.get("/api/jobs/{job_id}/log")
def get_job_log(job_id: str, request: Request, offset: int = 0, user=Depends(current_user)):
    return request.app.state.jobs.log(job_id, max(offset, 0))


@app.post("/api/jobs/{job_id}/stop")
def post_job_stop(job_id: str, request: Request, user=Depends(current_user)):
    return request.app.state.jobs.stop(job_id)


# Results: any table or view in the database, searched by item name, paged, or downloaded whole as CSV

def _relation(conn, name: str) -> str:
    if not RESULT_NAME.fullmatch(name) or not conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = :n"),
            {"n": name}).first():
        raise HTTPException(404, f"No table or view named {name}")
    return name


def _has_base_value(conn, name: str) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                                  "AND table_name = :n AND column_name = 'base_value'"), {"n": name}).first())


HIDDEN_COLUMNS = {"api_key"}  # stored for debugging failed rows; never sent to the browser


def _visible(columns) -> list[int]:
    return [i for i, c in enumerate(columns) if c not in HIDDEN_COLUMNS]


@app.get("/api/results")
def get_result_names(request: Request, user=Depends(current_user)):
    with store(request).engine.connect() as conn:
        names = [r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"))]
    hidden = {"users", "sessions", "jobs"}
    return [n for n in RESULT_VIEWS if n in names] + [n for n in names if n not in RESULT_VIEWS and n not in hidden]


@app.get("/api/results/{name}")
def get_results(name: str, request: Request, q: str = "", offset: int = 0,
                limit: int = Query(50, le=500), user=Depends(current_user)):
    with store(request).engine.connect() as conn:
        rel = _relation(conn, name)
        where = "WHERE base_value ILIKE :q" if q and _has_base_value(conn, rel) else ""
        params = {"q": f"%{q}%", "offset": max(offset, 0), "limit": limit}
        total = conn.execute(text(f"SELECT count(*) FROM {rel} {where}"), params).scalar()
        result = conn.execute(text(f"SELECT * FROM {rel} {where} ORDER BY 1 OFFSET :offset LIMIT :limit"), params)
        columns = list(result.keys())  # RMKeyView isn't indexable
        keep = _visible(columns)
        return {"columns": [columns[i] for i in keep], "rows": [[r[i] for i in keep] for r in result],
                "total": total}


@app.get("/api/results/{name}/csv")
def get_results_csv(name: str, request: Request, q: str = "", no_errors: bool = False, user=Depends(current_user)):
    """The table or view as CSV, like `export`: arrays joined with ' | ', JSON columns as JSON. `q` filters like the page;
    `no_errors` (final_results only) leaves out rows where any pass returned an error."""
    engine = store(request).engine

    def rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        with engine.connect() as conn:
            rel = _relation(conn, name)
            conditions = ["base_value ILIKE :q"] if q and _has_base_value(conn, rel) else []
            if no_errors and rel == "final_results":
                conditions.append("status <> 'pass 3 failed' AND NOT EXISTS (" + " UNION ALL ".join(
                    f"SELECT 1 FROM {t} e WHERE e.source_row_id = final_results.source_row_id AND e.error IS NOT NULL"
                    for t in ("llm_tags", "llm_retags", "llm_final_tags")) + ")")
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            result = conn.execute(text(f"SELECT * FROM {rel} {where} ORDER BY 1").execution_options(yield_per=2000),
                                  {"q": f"%{q}%"})
            columns = list(result.keys())
            keep = _visible(columns)
            writer.writerow([columns[i] for i in keep])
            yield buffer.getvalue()
            for r in result:
                buffer.seek(0), buffer.truncate()
                writer.writerow([" | ".join(v) if isinstance(v, list) and all(isinstance(x, str) for x in v)
                                 else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                                 for v in (r[i] for i in keep)])
                yield buffer.getvalue()

    return StreamingResponse(rows(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
