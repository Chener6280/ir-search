"""Keep offline tests isolated from the user's real credential file."""
import pytest


@pytest.fixture(autouse=True)
def isolated_source_credentials(tmp_path, monkeypatch, request):
    if request.node.get_closest_marker("live"):
        return
    path = tmp_path / "test_sources.env"
    path.write_text("# Offline tests never load user credentials.\n")
    path.chmod(0o600)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(path))
