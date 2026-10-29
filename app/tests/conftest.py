import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rcw import config  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    c = config.defaults()
    c["trip"]["routing_requests_per_poll"] = 0  # tests never call the real routing service
    return c


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
