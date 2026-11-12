"""Candidate rows: fetched from AWS Athena into Postgres (topk_options), or read from a CSV export, then cleaned."""
import csv
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

MISC_OPTION = "Misc - Not a Liquor"  # the "not alcoholic" tag


@dataclass(frozen=True)
class SourceRow:
    source_row_id: str
    base_value: str               # the store's item name
    options: list[str]            # candidate catalogue names
    insert_dttm: datetime | None  # Athena insert time (used as the watermark)


@dataclass
class FetchStats:
    fetched: int = 0
    invalid: int = 0
    invalid_ids: list[str] = field(default_factory=list)


def parse_options(raw) -> list[str]:
    """Split `a | b | c` (or take a list), trim, drop empties and duplicates, keeping the first."""
    parts = raw if isinstance(raw, (list, tuple)) else str(raw or "").split("|")
    seen, options = set(), []
    for part in parts:
        option = str(part or "").strip()
        if option and option not in seen:
            seen.add(option)
            options.append(option)
    return options


def clean_row(source_row_id, base_value, raw_options, insert_dttm=None) -> SourceRow | None:
    """Return the cleaned row, or None when it is invalid and should be skipped."""
    source_row_id = str(source_row_id or "").strip()
    base_value = str(base_value or "").strip()
    options = parse_options(raw_options)
    # No real candidates (only None / Misc): still tag it, so the LLM can name the product itself.
    is_misc = not options or all(o.lower() in ("none", MISC_OPTION.lower()) for o in options)
    if is_misc:
        options = [MISC_OPTION]  # no candidates: the LLM names the product itself
    if not source_row_id or not base_value or (not is_misc and len(options) < 2):
        return None
    return SourceRow(source_row_id, base_value, options, insert_dttm)


ATHENA_QUERY = "SELECT sourcerowid, basevalue, model, options, insert_dttm FROM {table}"


def fetch_athena(settings) -> Iterator[dict]:
    """Every row of the Athena topk_options table, as dicts with the Postgres column names.
    One query, read page by page with boto3 (1000 rows per page), so memory stays flat."""
    import boto3

    athena = boto3.client("athena", region_name=settings.aws_region)
    started = athena.start_query_execution(
        QueryString=ATHENA_QUERY.format(table=settings.athena_table),
        QueryExecutionContext={"Database": settings.athena_database, "Catalog": settings.athena_catalog},
        WorkGroup=settings.athena_workgroup,
        ResultConfiguration={"OutputLocation": settings.athena_output_location})
    query_id = started["QueryExecutionId"]
    while True:
        status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if status["State"] != "SUCCEEDED":
        raise RuntimeError(f"Athena query {status['State']}: {status.get('StateChangeReason', '')}")
    first = True
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=query_id):
        for r in page["ResultSet"]["Rows"]:
            if first:  # the first row of the first page is the header
                first = False
                continue
            yield _athena_row(r)


def _athena_row(r: dict) -> dict:
    cells = [d.get("VarCharValue") for d in r["Data"]]
    source_row_id, base_value, model, options, insert_dttm = (cells + [None] * 5)[:5]
    return {"source_row_id": source_row_id, "base_value": base_value,
            "model": None if model is None else model == "true", "options": options,
            "insert_dttm": parse_dttm(insert_dttm)}


def parse_dttm(value) -> datetime | None:
    """`2026-11-11 21:21:42.730` or an ISO string, else None. Athena timestamps are UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace(" UTC", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _column(fieldnames: list[str], *names: str) -> str | None:
    lookup = {f.strip().lower(): f for f in fieldnames}
    return next((lookup[n.lower()] for n in names if n.lower() in lookup), None)


def read_csv(path: str) -> list[dict]:
    """Every non-blank row of a CSV, with its id, item, options and insert time columns renamed to
    source_row_id, base_value, options and insert_dttm. All other columns are kept as they are."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        columns = {"source_row_id": _column(fields, "sourceRowId", "source_row_id"),
                   "base_value": _column(fields, "baseValue", "base_value"),
                   "options": _column(fields, "options")}
        if not all(columns.values()):
            raise SystemExit(f"{path} needs the columns sourceRowId, baseValue and options (found: {fields})")
        columns["insert_dttm"] = _column(fields, "insert_dttm", "source_insert_dttm")
        rows = []
        for r in reader:
            if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
                continue  # spreadsheet exports end with rows of empty cells
            for name, column in columns.items():
                if column:
                    r[name] = r.pop(column)
            rows.append(r)
        return rows


def fetch_csv(path: str, stats: FetchStats) -> Iterator[SourceRow]:
    """Rows from a CSV export of topk_options (sourceRowId, baseValue, options[, insert_dttm])."""
    for r in read_csv(path):
        stats.fetched += 1
        row = clean_row(r["source_row_id"], r["base_value"], r["options"], parse_dttm(r.get("insert_dttm")))
        if row is None:
            stats.invalid += 1
            stats.invalid_ids.append(str(r["source_row_id"]))
            continue
        yield row
