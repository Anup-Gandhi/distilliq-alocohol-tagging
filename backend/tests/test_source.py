from datetime import timezone

from tagger.source import MISC_OPTION, _athena_row, clean_row, parse_dttm


def test_athena_row_maps_columns_and_types():
    r = {"Data": [{"VarCharValue": "abc"}, {"VarCharValue": "titos 1.75l"}, {"VarCharValue": "true"},
                  {"VarCharValue": "Tito's Handmade|None"}, {"VarCharValue": "2026-11-11 21:21:42.730"}]}
    row = _athena_row(r)
    assert row["source_row_id"] == "abc" and row["base_value"] == "titos 1.75l"
    assert row["model"] is True and row["options"] == "Tito's Handmade|None"
    assert row["insert_dttm"].tzinfo == timezone.utc and row["insert_dttm"].year == 2026


def test_athena_row_null_cells():
    row = _athena_row({"Data": [{"VarCharValue": "id"}, {}, {}, {}, {}]})
    assert row["base_value"] is None and row["model"] is None and row["insert_dttm"] is None


def test_parse_dttm():
    assert parse_dttm("") is None and parse_dttm("nope") is None
    assert parse_dttm("2026-01-02 03:04:05 UTC").hour == 3


def test_clean_row_from_athena_options_string():
    row = clean_row("id", " amaras logia ", "None|None|None")
    assert row.options == [MISC_OPTION] and row.base_value == "amaras logia"
    assert clean_row("id", "x", "Only One") is None
    assert clean_row("id", "x", "A|B|None").options == ["A", "B", "None"]
