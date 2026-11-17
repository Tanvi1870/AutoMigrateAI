"""The shipped samples must parse cleanly: they drive the hackathon demo."""

import json
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.dependencies import get_parser
from app.knowledge.loader import load_knowledge
from app.models import ApplicationType
from app.parsers.representative_format import FORMAT_LABEL

SAMPLES_DIR = PROJECT_ROOT / "samples" / "icap"
TEST_CASE_SAMPLES = sorted(SAMPLES_DIR.glob("*_test.json"))
ALL_SAMPLES = sorted(SAMPLES_DIR.glob("*.json"))
MVP_KEYWORDS = set(load_knowledge("keyword_kb")["keywords"])


def _read(name: str) -> str:
    return (SAMPLES_DIR / name).read_text(encoding="utf-8")


def test_samples_exist() -> None:
    names = {p.name for p in ALL_SAMPLES}

    assert {"login_test.json", "checkout_test.json", "sample_testdata.json", "sample_object_repository.json"} <= names


@pytest.mark.parametrize("path", ALL_SAMPLES, ids=lambda p: p.name)
def test_every_sample_is_labelled_as_representative_format(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["format"] == FORMAT_LABEL
    assert document["formatVersion"] == "1.0"


@pytest.mark.parametrize("path", TEST_CASE_SAMPLES, ids=lambda p: p.name)
def test_test_case_samples_parse_with_shared_files(path: Path) -> None:
    test_case = get_parser().parse(
        path.read_text(encoding="utf-8"),
        test_data=_read("sample_testdata.json"),
        object_repository=_read("sample_object_repository.json"),
        source_name=path.name,
    )

    assert test_case.application_type is ApplicationType.WEB
    assert test_case.missing_objects() == []
    assert test_case.unresolved_variables() == []
    assert test_case.source is not None and test_case.source.reference == path.name


def test_login_sample_is_self_contained() -> None:
    test_case = get_parser().parse(_read("login_test.json"))

    assert test_case.test_case_id == "ICAP-TC-001"
    assert [s.keyword for s in test_case.steps] == [
        "web_navigate",
        "web_setText",
        "web_setText",
        "web_click",
        "web_assertText",
    ]
    assert [s.source_step_ref for s in test_case.steps] == ["1", "2/BLK-LOGIN#1", "2/BLK-LOGIN#2", "2/BLK-LOGIN#3", "3"]
    assert [b.block_id for b in test_case.reusable_blocks] == ["BLK-LOGIN"]
    assert test_case.missing_objects() == []
    assert test_case.unresolved_variables() == []
    assert test_case.test_data.masked()["PASSWORD"] != "demo_password"


def test_checkout_sample_needs_shared_files() -> None:
    test_case = get_parser().parse(_read("checkout_test.json"))

    assert "addToCartButton" in test_case.missing_objects()
    assert "COUNTRY" in test_case.unresolved_variables()


def test_samples_together_cover_every_mvp_keyword() -> None:
    shared = {"test_data": _read("sample_testdata.json"), "object_repository": _read("sample_object_repository.json")}
    used = {s.keyword for p in TEST_CASE_SAMPLES for s in get_parser().parse(p.read_text(encoding="utf-8"), **shared).steps}

    assert used == MVP_KEYWORDS
