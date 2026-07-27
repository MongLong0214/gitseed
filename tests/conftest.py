from unittest.mock import patch

import pytest


@pytest.fixture(scope="session", autouse=True)
def forbid_network() -> None:
    with patch(
        "urllib.request.urlopen",
        side_effect=AssertionError("tests must not reach the network"),
    ):
        yield
