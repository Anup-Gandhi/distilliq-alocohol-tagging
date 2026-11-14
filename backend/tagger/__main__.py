"""Command line: python -m tagger migrate | refresh | pass1 | pass2 | pass3 | import | export."""
import argparse
import csv
import json
import os
import signal
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from itertools import islice

from .source import FetchStats, SourceRow, fetch_athena, fetch_csv, read_csv
from .budget import Budget, BudgetExceeded
from .claude_tagger import (RECORD_REVIEW_TOOL, ClaudeTagger, candidates, clean_item, first_pass_output, is_misc,
                            user_message)
from .config import (DEFAULT_MODELS, EFFORTS, PROMPTS, find_llm_configs, load_llm_config, load_settings,
                     provider_of_model)

# The provider pass 2 prefers, in order, when it isn't the one that tagged pass 1.
RETAG_PREFERENCE = ("gemini", "anthropic", "openai")


def _tagger(llm, budget, **kw):
    if llm.base_url:
        from .gemini_web_tagger import GeminiWebTagger
        return GeminiWebTagger(llm, budget, **kw)
    if llm.provider == "openai":
        from .openai_tagger import OpenAITagger
        return OpenAITagger(llm, budget, **kw)
    if llm.provider == "gemini":
        from .gemini_tagger import GeminiTagger
        return GeminiTagger(llm, budget, **kw)
    return ClaudeTagger(llm, budget, **kw)


def _stop_on_ctrl_c():
    """Exit at once on Ctrl+C. A normal exit would wait for every queued LLM call, then throw their results away."""
    def stop(*_):
        print("\nStopped. Saved rows are kept, and the next run picks up the rest.", file=sys.stderr, flush=True)
        os._exit(130)  # Postgres rolls back a save that was in progress
    signal.signal(signal.SIGINT, stop)


PASS3_REQUEST_INTERVAL = 1.0  # seconds between pass 3 requests: at most 1 per second, across all workers


def _throttled(fn, interval: float):
    """fn, but calls start at least `interval` seconds apart, even from several threads."""
    lock, next_start = threading.Lock(), [0.0]

    def call(*args, **kwargs):
        with lock:
            now = time.monotonic()
            start = max(now, next_start[0])
            next_start[0] = start + interval
        time.sleep(start - now)
        return fn(*args, **kwargs)
    return call


def _tag_rows(rows, tag, workers):
    """Tag rows in parallel windows of workers x 4. Yields (row, result); result is None after a budget stop."""
    rows = iter(rows)
    over_budget = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while not over_budget and (window := list(islice(rows, workers * 4))):
            futures = {pool.submit(tag, row): row for row in window}
            pending = set(futures)
            while pending:
                # needs a timeout: on Windows, Ctrl+C can't interrupt a wait without one
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        result = future.result()
                    except BudgetExceeded:
                        over_budget, result = True, None
                    yield futures[future], result


def _store(settings):
    from .store import Store
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set in .env")
    from sqlalchemy.exc import DBAPIError
    try:
        store = Store(settings.database_url)
        ready = store.schema_ready()
    except DBAPIError as e:  # can't connect
        message = str(e.orig).splitlines()[0] if e.orig else str(e)
        sys.exit(f"Can't connect to Postgres (DATABASE_URL in .env): {message}")
    if not ready:
        sys.exit("The database has no tables yet. Run `python -m tagger migrate` (the API does it when it starts).")
    return store


# migrate: bring the database schema up to date (Alembic)

def cmd_migrate(args, settings):
    from sqlalchemy.exc import DBAPIError
    from .store import migrate
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set in .env")
    try:
        migrate(settings.database_url)
    except DBAPIError as e:  # can't connect, or not allowed to create the missing database
        message = str(e.orig).splitlines()[0] if e.orig else str(e)
        sys.exit(f"Migration failed: {message}")
    print("The database schema is up to date.")


def _print_dry(row: SourceRow, result, model: str) -> None:
    print(json.dumps({"source_row_id": row.source_row_id, "base_value": row.base_value, "options": row.options,
                      "model": model, **asdict(result)}, default=str, ensure_ascii=False), flush=True)


def _run_loop(rows, tag, save, args, budget, model):
    """Tag rows, save each result (or print it on a dry run), and return (tagged, skipped, failed, budget_stopped)."""
    tagged = skipped = failed = 0
    stopped = False
    for row, result in _tag_rows(rows, tag, args.workers):
        if result is None:  # skipped because the budget ran out: not saved, so the next run picks it up
            stopped = True
            continue
        if args.dry_run:
            _print_dry(row, result, model(row))
        else:
            save(row, result)
        if result.error:
            failed += 1
            print(f"  failed {row.source_row_id}: {result.error}", file=sys.stderr, flush=True)
        elif result.skip_reason:
            skipped += 1
            print(f"  skipped {row.source_row_id}: {result.skip_reason}", file=sys.stderr, flush=True)
        else:
            tagged += 1
        done = tagged + skipped + failed
        if done % 50 == 0:
            print(f"  ... {done} done, ${budget.spent:.2f} spent", file=sys.stderr, flush=True)
    return tagged, skipped, failed, stopped


def _summary(budget, stopped, again: str, **counts) -> None:
    for name, value in counts.items():
        print(f"{name.replace('_', ' '):>15}: {value}")
    print(f"{'spent':>15}: ${budget.spent:.4f} of ${budget.limit:.2f}")
    if stopped:
        print(f"Stopped at the spending limit. Rows not reached weren't saved; run again to continue ({again}).")


def _prompt(name: str) -> str:
    """The text of prompts/<name>. Every pass takes --prompt; the file is read when the command starts."""
    path = PROMPTS / name
    if not path.is_file():
        sys.exit(f"No prompt file {name} in prompts/")
    return path.read_text(encoding="utf-8")


def _preview(args, rows, system_prompt: str, model, note=lambda row: None) -> None:
    """Print one JSON line: how many rows a run would send, and the first --preview of them with the exact user
    message. Calls no model and saves nothing."""
    shown, total = [], 0
    for row in rows:
        total += 1
        if len(shown) < args.preview:
            shown.append({"source_row_id": row.source_row_id, "base_value": row.base_value, "options": row.options,
                          "model": model(row),
                          "message": user_message(clean_item(row.base_value), candidates(row), note(row))})
    print(json.dumps({"command": args.command, "prompt": args.prompt, "system_prompt": system_prompt,
                      "total": total, "rows": shown}, default=str, ensure_ascii=False), flush=True)


def _limited(rows, limit):
    return islice(rows, limit) if limit else rows


# refresh: copy Athena's topk_options into Postgres

def cmd_refresh(args, settings):
    if not settings.athena_workgroup or not settings.athena_output_location:
        sys.exit("Set ATHENA_WORKGROUP and ATHENA_OUTPUT_LOCATION in .env")
    store = _store(settings)
    table = f"{settings.athena_database}.{settings.athena_table}"
    print(f"Querying Athena {table} ({settings.aws_region}, workgroup {settings.athena_workgroup})...", flush=True)
    fetched = 0

    def counting():
        nonlocal fetched
        for r in fetch_athena(settings):
            fetched += 1
            if fetched % 10000 == 0:
                print(f"  ... {fetched} rows read", flush=True)
            yield r

    try:
        loaded = store.replace_source(counting())
    except Exception as e:  # boto3 raises many classes; the message is what the user needs
        sys.exit(f"Refresh failed, the old copy is kept: {type(e).__name__}: {e}")
    stats = store.source_stats()
    print(f"Loaded {loaded} rows into topk_options (insert_dttm {stats['oldest_insert_dttm']} to "
          f"{stats['newest_insert_dttm']}).")


# pass1: name each row

def cmd_run(args, settings):
    prompt = _prompt(args.prompt)
    llm = load_llm_config(args.llm)
    budget = Budget(args.max_cost)
    tagger = _tagger(llm, budget, effort=args.effort or llm.effort, web_search=not args.no_web_search,
                     system_prompt=prompt)
    print(f"{tagger.model} with {args.prompt}", file=sys.stderr)
    store = _store(settings)  # the source rows live in Postgres, so a dry run needs it too

    skip = set() if args.dry_run else store.tagged_ids(include_errors=True)
    stats = FetchStats()
    if args.csv:
        if not args.dry_run and not args.preview:
            print(f"Uploaded {store.save_csv(args.csv, read_csv(args.csv))} rows to csv_records", file=sys.stderr)
        source = fetch_csv(args.csv, stats)
    else:
        if store.source_stats()["rows"] == 0:
            sys.exit("topk_options is empty. Run refresh (the Refresh button) first.")
        watermark = None if (args.full or args.dry_run) else store.watermark()
        source = store.source_rows(watermark, stats)

    already = 0

    def pending():
        nonlocal already
        if args.retry_errors and not args.dry_run:
            for r in store.error_rows():
                yield SourceRow(r["source_row_id"], r["base_value"], list(r["options"]), r["source_insert_dttm"])
        for row in source:
            if row.source_row_id in skip:
                already += 1
                continue
            yield row

    if args.preview:
        return _preview(args, _limited(pending(), args.limit), prompt, lambda row: tagger.model)
    tagged, skipped, failed, stopped = _run_loop(
        _limited(pending(), args.limit), tagger.tag,
        lambda row, result: store.save(row, result, tagger.model, llm=tagger.llm), args, budget, lambda row: tagger.model)
    _summary(budget, stopped, "add --full" if not args.csv else "same command",
             fetched=stats.fetched, invalid=stats.invalid, already_tagged=already, tagged=tagged,
             skipped=skipped, failed=failed)


# pass2: another provider reviews pass 1

def _retag_configs(names):
    """One LLM config per provider, from --llm or from secrets/."""
    if names:
        configs = [load_llm_config(n) for n in names]
    else:
        configs = []
        for provider in RETAG_PREFERENCE:
            found = find_llm_configs(provider)
            if len(found) > 1:
                sys.exit(f"Several {provider} LLM files in secrets/ ({', '.join(p.stem for p in found)}). "
                         "Pick one with --llm.")
            if found:
                configs.append(load_llm_config(str(found[0])))
    by_provider = {}
    for llm in configs:
        if llm.provider in by_provider:
            sys.exit(f"--llm names two {llm.provider} files; give one per provider.")
        by_provider[llm.provider] = llm
    return by_provider


def _other_provider(model: str, available) -> str | None:
    first = provider_of_model(model or "")
    return next((p for p in RETAG_PREFERENCE if p != first and p in available), None)


def _retag_request(first: dict) -> tuple[SourceRow, str]:
    """The record to review and pass 1's output, as retag_system_prompt.md expects them."""
    options, output = first_pass_output(first)
    output["confidence"] = first["confidence"]
    note = f"<model_1_output>\n{json.dumps(output, ensure_ascii=False)}\n</model_1_output>"
    return SourceRow(first["source_row_id"], first["base_value"], options, first["source_insert_dttm"]), note


def cmd_retag(args, settings):
    from .store import llm_retags, llm_tags
    store = _store(settings)
    try:
        source = llm_tags if args.source == llm_tags.name else store.tags_table(args.source, like=llm_tags)
        target = llm_retags if args.table == llm_retags.name else store.tags_table(args.table, like=llm_retags)
    except ValueError as e:
        sys.exit(f"--from/--table {e}")
    configs = _retag_configs(args.llm)
    budget = Budget(args.max_cost)
    prompt = _prompt(args.prompt)

    # Pass 2 checks only what pass 1 called Misc, or left without a tag (skipped or failed).
    needed = [r for r in store.first_pass_rows(source) if not (r["tag"] or "").strip() or is_misc(r["tag"])]
    done = store.retagged_ids(include_errors=not args.retry_errors, table=target)
    firsts = {r["source_row_id"]: r for r in needed if r["source_row_id"] not in done}
    already = len(needed) - len(firsts)

    models = sorted({r["model"] for r in firsts.values()})
    route = {}
    for model in models:
        provider = _other_provider(model, configs)
        if provider is None:
            others = [p for p in DEFAULT_MODELS if p != provider_of_model(model or "")]
            sys.exit(f"Rows tagged by {model} need a retagger from another provider ({' or '.join(others)}). "
                     "Add one to secrets/ or pass --llm.")
        route[model] = provider
    taggers = {p: _tagger(configs[p], budget, effort=args.effort or configs[p].effort,
                          web_search=not args.no_web_search, system_prompt=prompt,
                          tool=RECORD_REVIEW_TOOL)
               for p in set(route.values())}
    for model, provider in route.items():
        print(f"Pass 1 rows by {model} -> checked by {taggers[provider].model}", file=sys.stderr)

    print(f"{source.name} -> {target.name} with {args.prompt}", file=sys.stderr)

    def tagger_for(row):
        return taggers[route[firsts[row.source_row_id]["model"]]]

    requests = {i: _retag_request(r) for i, r in firsts.items()}
    if args.preview:
        return _preview(args, _limited((row for row, _ in requests.values()), args.limit), prompt,
                        lambda row: tagger_for(row).model, lambda row: requests[row.source_row_id][1])
    tagged, skipped, failed, stopped = _run_loop(
        _limited((row for row, _ in requests.values()), args.limit),
        lambda row: tagger_for(row).tag(row, note=requests[row.source_row_id][1]),
        lambda row, result: store.save_retag(firsts[row.source_row_id], result, tagger_for(row).model, table=target,
                                               llm=tagger_for(row).llm),
        args, budget, lambda row: tagger_for(row).model)
    _summary(budget, stopped, "same command", misc_or_blank=len(needed), already_checked=already,
             tagged=tagged, skipped=skipped, failed=failed)


# pass3: name every row that isn't confirmed Misc, without seeing passes 1 and 2

def cmd_final(args, settings):
    store = _store(settings)
    prompt = _prompt(args.prompt)
    llm = load_llm_config(args.llm)
    budget = Budget(args.max_cost)
    tagger = _tagger(llm, budget, effort=args.effort or llm.effort, web_search=not args.no_web_search,
                     system_prompt=prompt)
    print(f"{tagger.model} with {args.prompt}", file=sys.stderr)

    done = store.final_tagged_ids(include_errors=not args.retry_errors)
    needed = store.pass3_rows()
    firsts = {str(r["source_row_id"]): r for r in needed if str(r["source_row_id"]) not in done}
    # first_pass_output drops a name pass 1 made up, so pass 3 sees only the catalogue options; no note is sent
    rows = (SourceRow(i, r["base_value"], first_pass_output(r)[0], r["source_insert_dttm"]) for i, r in firsts.items())

    if args.preview:
        return _preview(args, _limited(rows, args.limit), prompt, lambda row: tagger.model)
    # shortcut: a pause_turn continuation inside one tag() call isn't spaced out; rare, throttle _request if it matters
    tagged, skipped, failed, stopped = _run_loop(
        _limited(rows, args.limit), _throttled(tagger.tag, PASS3_REQUEST_INTERVAL),
        lambda row, result: store.save_final(firsts[row.source_row_id], row, result, tagger.model,
                                             llm=tagger.llm),
        args, budget, lambda row: tagger.model)
    _summary(budget, stopped, "same command", to_name=len(needed), already_named=len(needed) - len(firsts),
             tagged=tagged, skipped=skipped, failed=failed)


# import

def cmd_import(args, settings):
    store = _store(settings)
    for path in args.paths:
        print(f"Uploaded {store.save_csv(path, read_csv(path))} rows from {path} to csv_records")


# export

def cmd_export(args, settings):
    store = _store(settings)
    rows = store.all_retags() if args.retags else store.all_rows()
    if not rows:
        print("Nothing to export.")
        return
    with open(args.path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            out = dict(row)
            if out.get("options") is not None:
                out["options"] = " | ".join(out["options"])
            for key in ("judgements", "sources"):
                if key in out:
                    out[key] = json.dumps(out[key], ensure_ascii=False)
            writer.writerow(out)
    print(f"Wrote {len(rows)} rows to {args.path}")


def main(argv=None):
    # a Windows console defaults to cp1252, which can't print the prompts' arrows or accented names
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    _stop_on_ctrl_c()
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="python -m tagger", description="Tag liquor products with LLMs.")
    commands = parser.add_subparsers(dest="command", required=True)

    def tagging(name, help):
        p = commands.add_parser(name, help=help)
        p.add_argument("--max-cost", type=float, default=15.0, metavar="USD", help="spending cap (default 15)")
        p.add_argument("--limit", type=int, help="tag at most N rows")
        p.add_argument("--retry-errors", action="store_true", help="tag failed rows again")
        p.add_argument("--workers", type=int, default=settings.tag_workers, help="parallel requests")
        p.add_argument("--effort", choices=EFFORTS,
                       help="overrides the LLM file's effort (default: the file's, else medium)")
        p.add_argument("--no-web-search", action="store_true")
        p.add_argument("--dry-run", action="store_true", help="print JSON lines, write nothing (default --limit 5)")
        p.add_argument("--preview", type=int, default=0, metavar="N",
                       help="print how many rows would be sent and the first N with their messages; call no model")
        return p

    p = commands.add_parser("migrate", help="create or update the database tables and views (Alembic upgrade head)")
    p.set_defaults(func=cmd_migrate)

    p = commands.add_parser("refresh", help="copy the Athena topk_options table into Postgres (replaces the old copy)")
    p.set_defaults(func=cmd_refresh)

    p = tagging("pass1", "tag new rows from the local topk_options copy (or a CSV)")
    p.add_argument("--llm", required=True, help="LLM file in secrets/, e.g. claude, chatgpt or gemini")
    p.add_argument("--full", action="store_true", help="ignore the watermark and read the whole table")
    p.add_argument("--csv", metavar="PATH", help="read rows from a CSV export of topk_options instead of Postgres")
    p.add_argument("--prompt", default="system_prompt.md", metavar="FILE",
                   help="prompt file in prompts/ (default system_prompt.md)")
    p.set_defaults(func=cmd_run)

    p = tagging("pass2", "check the rows pass 1 called Misc or left blank, with another provider")
    p.add_argument("--prompt", default="retag_system_prompt.md", metavar="FILE",
                   help="prompt file in prompts/ (default retag_system_prompt.md)")
    p.add_argument("--llm", action="append", help="LLM file to review with; repeat for several providers")
    p.add_argument("--from", dest="source", default="llm_tags", help="pass 1 table to review (default llm_tags)")
    p.add_argument("--table", default="llm_retags", help="table to save to, e.g. llm_retags_v2 (created if missing)")
    p.set_defaults(func=cmd_retag)

    p = tagging("pass3", "name every row not confirmed Misc by passes 1 and 2, without seeing their answers")
    p.add_argument("--llm", default="final-tagger", help="LLM file (default final-tagger)")
    p.add_argument("--prompt", default="pass3_system_prompt.md", metavar="FILE",
                   help="prompt file in prompts/ (default pass3_system_prompt.md)")
    p.set_defaults(func=cmd_final)

    p = commands.add_parser("import", help="upload CSV files to csv_records without tagging them")
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.set_defaults(func=cmd_import)

    p = commands.add_parser("export", help="write llm_tags (or llm_retags) to a CSV file")
    p.add_argument("path")
    p.add_argument("--retags", action="store_true")
    p.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    if getattr(args, "dry_run", False) and args.limit is None:
        args.limit = 5
    args.func(args, settings)


if __name__ == "__main__":
    main()
