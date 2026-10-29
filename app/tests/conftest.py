import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rcw import config  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return config.defaults()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
