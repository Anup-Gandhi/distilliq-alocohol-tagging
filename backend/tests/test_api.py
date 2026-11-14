import pytest
from fastapi import HTTPException

from api.auth import check_password, hash_password
from api.jobs import build_argv, conflict


def test_build_argv_run():
    argv = build_argv("pass1", {"llm": "claude", "full": True, "max_cost": "2.5", "limit": 10, "dry_run": False,
                              "effort": "low", "csv": ""})
    assert argv == ["pass1", "--llm", "claude", "--full", "--max-cost", "2.5", "--limit", "10", "--effort", "low"]
    assert build_argv("pass1", {"llm": "claude", "prompt": "system_prompt_v2.md"})[-2:] == ["--prompt", "system_prompt_v2.md"]


def test_build_argv_retag_lists_and_tables():
    argv = build_argv("pass2", {"llm": ["gemini", "claude"], "from": "llm_tags_v2", "table": "llm_retags_v2"})
    assert argv == ["pass2", "--llm", "gemini", "--llm", "claude", "--from", "llm_tags_v2", "--table", "llm_retags_v2"]


@pytest.mark.parametrize("command,args", [
    ("pass1", {}),                                 # llm required
    ("pass1", {"llm": "claude; rm -rf /"}),        # bad name
    ("pass1", {"llm": "claude", "bogus": 1}),      # unknown option
    ("pass2", {"table": "LLM_TAGS"}),              # table names are lowercase
    ("pass1", {"llm": "claude", "limit": "ten"}),
    ("pass1", {"llm": "claude", "prompt": "../secrets/claude.json"}),  # only files in prompts/
    ("run", {}),                                   # old names are gone
    ("rerun", {"llm": "c", "prompt": "system_prompt.md", "table": "t"}),
])
def test_build_argv_rejects(command, args):
    with pytest.raises(HTTPException) as e:
        build_argv(command, args)
    assert e.value.status_code == 400


def test_build_argv_prompt_on_every_pass():
    for command in ("pass1", "pass2", "pass3"):
        args = {"llm": "claude"} if command != "pass2" else {"llm": ["claude"]}
        assert build_argv(command, {**args, "prompt": "system_prompt.md"})[-2:] == ["--prompt", "system_prompt.md"]


def test_preview_only_for_passes():
    from api.jobs import preview
    for command in ("refresh", "import"):
        with pytest.raises(HTTPException):
            preview(command, {})


def test_password_hash_roundtrip():
    h = hash_password("ChangeMe123!")
    assert check_password("ChangeMe123!", h) and not check_password("changeme123!", h)


@pytest.mark.parametrize("name", ["../secrets/claude.md", "x.txt", "a/b.md", ".md", "con fig.md"])
def test_prompt_path_rejects(name):
    from api.main import prompt_path
    with pytest.raises(HTTPException):
        prompt_path(name)


def test_prompt_path_accepts():
    from api.main import prompt_path
    assert prompt_path("misc_check_v2.md").name == "misc_check_v2.md"


def test_conflict_lets_different_passes_overlap():
    assert conflict("pass3", ["pass1"]) is None
    assert conflict("pass1", ["pass2", "pass3"]) is None
    assert conflict("pass1", ["pass1"]) == "pass1"
    assert conflict("refresh", ["pass1"]) == "pass1"
    assert conflict("pass1", ["import"]) == "import"
    assert conflict("refresh", []) is None
