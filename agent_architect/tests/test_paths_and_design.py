import pytest

from agent_architect.design import DesignParseError, parse_analysis, parse_design
from agent_architect.platform_paths import (
    InvalidPathError,
    component_hld_file,
    validate_architecture_path,
    validate_feature_id,
)
from conftest import FEATURE_ID, design_json


@pytest.mark.parametrize(
    "raw, expected",
    [("backend/api", "backend/api"), ("/frontend/web/", "frontend/web"), ("backend\\api", "backend/api"), ("db", "db")],
)
def test_valid_architecture_paths(raw, expected):
    assert validate_architecture_path(raw) == expected


@pytest.mark.parametrize("raw", ["", "../secrets", "a/../b", "a//b", ".git", "a/.git/x", "has space", "-x"])
def test_invalid_architecture_paths(raw):
    with pytest.raises(InvalidPathError):
        validate_architecture_path(raw)


def test_component_path_layout():
    assert component_hld_file(FEATURE_ID, "backend/api").as_posix() == (
        f".platform/{FEATURE_ID}/architect/components/backend/api/HLD.md"
    )


@pytest.mark.parametrize("bad", ["../../etc", "", "a/b", "-x", "x" * 101, "a..b"])
def test_invalid_feature_ids(bad):
    with pytest.raises(InvalidPathError):
        validate_feature_id(bad)


@pytest.mark.parametrize("good", [FEATURE_ID, "feature-42", "login_v2.1"])
def test_valid_feature_ids(good):
    assert validate_feature_id(good) == good


def test_parse_bare_and_fenced_json():
    assert len(parse_design(design_json()).components) == 2
    fenced = "Here you go:\n```json\n" + design_json(("db",)) + "\n```"
    assert parse_design(fenced).components[0].architecture_path == "db"


def test_parse_rejects_duplicates_bad_paths_and_garbage():
    with pytest.raises(DesignParseError):
        parse_design(design_json(("backend/api", "Backend/API")))
    with pytest.raises(DesignParseError):
        parse_design(design_json(("../x",)))
    with pytest.raises(DesignParseError):
        parse_design("no json here")


def test_parse_analysis():
    analysis = parse_analysis('```json\n{"summary": "x", "questions": ["q?"]}\n```')
    assert analysis.questions == ["q?"] and analysis.assumptions == []
    with pytest.raises(DesignParseError):
        parse_analysis('{"questions": []}')
