# Liquor Product Tagger

LLMs tag liquor store items against candidate catalogue names in three passes. Postgres is the source of truth.
This repository holds the tagging package (`backend/tagger`), a FastAPI service around it (`backend/api`) and a
React web app (`frontend`). `BUILD-GUIDE.md` explains the tagging logic in depth; this file explains how to run it.

## How the passes work

1. **Pass 1** names every row, or answers `Misc - Not a Liquor`, or skips an unclear one (`llm_tags`).
2. **Pass 2** checks only the rows pass 1 called Misc or left without a tag, with a different provider
   (`llm_retags`).
3. **Pass 3** names every row that isn't confirmed Misc: rows pass 1 named, rows pass 1 left blank, and Misc rows
   where pass 2 disagreed or gave no tag. It sees only the item and its candidate names, never the earlier answers
   (`llm_final_tags`).

The view `final_results` holds the answer: `final_tag` is `Misc - Not a Liquor` when passes 1 and 2 agree, else pass
3's name. `status` says where each row stands, and `needs_pass_2` / `needs_pass_3` show the routing. The Misc list
is `SELECT * FROM final_results WHERE final_tag = 'Misc - Not a Liquor'`. Every pass takes a prompt file
(`--prompt`): `system_prompt.md`, `retag_system_prompt.md` and `pass3_system_prompt.md` by default.

## What changed from the command-line version

- **Athena, not BigQuery.** `refresh` copies the whole Athena table `dev_proximo_cantag.topk_options` (same
  columns as before: `sourcerowid, basevalue, options, insert_dttm`, plus `model`) into the Postgres table
  `topk_options`. `pass1` reads from that local copy, so tagging never waits on Athena. Press **Refresh from Athena**
  on the dashboard whenever you want the latest rows.
- **Web app** with sign-in (JWT; accounts and sessions in Postgres, logout revokes the session), API key files,
  every command with its live log, and every table and view browsable and downloadable.
- **API keys** stay JSON files in `backend/secrets/<name>.json`, one per key, as many per provider as you like.
  The file name is what commands take as the LLM key. The web app writes and deletes them; the command line still
  reads them as before.

## Run with Docker

```bash
cp .env.example .env      # fill in the AWS and Athena values; set JWT_SECRET to any long random string
docker compose up --build
```

Open http://localhost:5175 and sign in with the `EMAIL` / `PASSWORD` from `.env` (the account is created on first
start). Postgres data lives in the `pgdata` volume; key files in `backend/secrets/`; prompts in `backend/prompts/`; uploaded CSVs and job logs in
`backend/data/`.

## Run for development

Backend (Python 3.12), from `backend/`:

```bash
python -m venv .venv && .venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt pytest
uvicorn api.main:app --reload --port 8000
python -m pytest tests
```

Frontend (Node 20+), from `frontend/`:

```bash
npm install
npm run dev          # http://localhost:5175, proxies /api to :8000
npm run build        # type-checks, then bundles to dist/
```

`.env` is read from the repository root (or `backend/.env`). The command line still works:
`python -m tagger refresh | pass1 | pass2 | pass3 | import | export --help`. These replace the old `run`, `retag` and `final`; `rerun` was removed.

## Database schema (Alembic)

Tables and views are created and changed only by Alembic migrations in `backend/migrations/versions/`. The API
upgrades the database to the latest revision when it starts; without the API, run `python -m tagger migrate`
(or `alembic upgrade head`) from `backend/`. Commands refuse to run on a database that was never migrated.

To change the schema, from `backend/`:

```bash
# 1. edit the Table in tagger/store.py, then:
alembic revision --autogenerate -m "add x to llm_tags"
# 2. read the generated file in migrations/versions/ and fix it if needed
alembic upgrade head
python -m pytest tests/test_migrations.py   # migrations and store.py must still agree
```

Autogenerate sees tables and columns only. A view change goes in a revision by hand, as
`op.execute("CREATE OR REPLACE VIEW ...")`; `CREATE OR REPLACE` can only add columns at the end, so changing or
removing a view column needs `DROP VIEW` first (and of any view built on it). The baseline revision holds the view
SQL as it was moved from `store.py`. Pass 2's on-demand tables (`--table llm_retags_v2`) are still created when first
used and are left out of migrations.

## .env

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://user:password@host:5432/dbname`; created if missing. Docker sets its own. |
| `EMAIL`, `PASSWORD`, `FIRST_NAME`, `LAST_NAME` | the first account, created at startup if that email doesn't exist |
| `JWT_SECRET` | signs the sign-in tokens; 16+ random characters |
| `FRONTEND_URL` | allowed CORS origin for development (default `http://localhost:5175`) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | Athena access (a profile or instance role works too) |
| `ATHENA_WORKGROUP`, `ATHENA_OUTPUT_LOCATION` | required for `refresh` |
| `ATHENA_CATALOG`, `ATHENA_DATABASE`, `ATHENA_TABLE` | defaults `AwsDataCatalog`, `dev_proximo_cantag`, `topk_options` |
| `TAG_WORKERS` | default parallel requests (4) |

## The web app

- **Dashboard**: the local copy's row count and insert range, the Refresh button with its log, pass counts and the
  `llm_tag_votes` decision breakdown.
- **Commands**: Pass 1, Pass 2, Pass 3 and Import with every option the command line has, CSV upload for
  `pass1 --csv` and `import`, and Export as a download. One job runs at a time; its log streams live and can be
  stopped. Past jobs and their logs stay listed.
- **API keys**: add a key (name, provider, model, effort, optional prices or proxy URL); the same checks as the
  command line run before the file is kept.
- **Prompts**: edit the system prompts in `backend/prompts/`, or save a copy under a new name as a backup. Each
  command reads its prompt when it starts, so an edit applies from the next job. In Docker the folder is a volume,
  so edits survive rebuilds; they are plain files, so git shows what changed.
- **Results**: any table or view (`final_tag_summary` first), searched by item name, paged, downloadable as CSV with
  arrays joined by ` | ` and JSON columns as JSON, like `export`.

## HTTP API

All routes except login need `Authorization: Bearer <token>`.

| Route | Does |
|---|---|
| `POST /api/auth/login` `{email, password}` → `{token, user}`; `POST /api/auth/logout`; `GET /api/auth/me` | sign-in |
| `GET/POST /api/keys`, `DELETE /api/keys/{name}` | key files (keys come back masked) |
| `GET /api/stats` | dashboard numbers |
| `GET /api/prompts`, `GET/PUT /api/prompts/{name}` | read, edit or create a prompt |
| `GET /api/options`, `GET/POST /api/uploads` | prompt files, uploaded CSVs |
| `GET/POST /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/log?offset=N`, `POST /api/jobs/{id}/stop` | commands and logs |
| `GET /api/results`, `GET /api/results/{name}?q=&offset=&limit=`, `GET /api/results/{name}/csv` | tables and views |
