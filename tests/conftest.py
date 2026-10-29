import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_html() -> str:
    return (FIXTURES / "search_results.html").read_text(encoding="utf-8")


@pytest.fixture
def detail_html() -> str:
    return (FIXTURES / "ad_detail.html").read_text(encoding="utf-8")


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    from dealfinder import store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return tmp_path
