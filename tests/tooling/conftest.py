import pytest

pytest_plugins = ["pytester"]


# The run under test loads the real index hook and reads its own index file.
@pytest.fixture
def index_pytester(
    monkeypatch: pytest.MonkeyPatch, pytester: pytest.Pytester
) -> pytest.Pytester:
    pytester.makeconftest(
        "from tests.conftest import pytest_collection_modifyitems, pytest_configure"
    )
    monkeypatch.setenv("DJ_TURSO_INDEX", str(pytester.path / "index.toml"))
    monkeypatch.setenv("DJ_TURSO_TARGET", "pyturso")
    return pytester
