"""A computer with nothing configured must be told what to configure, not handed an empty choice."""
from datetime import date

from ir_search import DataRequest, MaterialRegistry, MaterialSearchRequest, get_data, search_materials
from ir_search.infrastructure.credentials import credentials_file_state, setup_hint
from ir_search.models import FailureKind


def test_material_search_explains_an_empty_source_choice(tmp_path, monkeypatch):
    result = search_materials(MaterialSearchRequest("公司研究"))  # conftest points at an empty private file
    hint = next(d for d in result.diagnostics if d.code == "no_material_source_enabled")
    assert hint.failure_kind == FailureKind.NO_CREDENTIAL and hint.message.startswith("credentials_file=found")
    assert "_ENABLED=true" in hint.message and "ir-search-doctor" in hint.message
    assert {"code": "no_material_source_enabled"} in result.gaps and result.plan["source_options"] == []

    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "absent.env"))
    assert credentials_file_state() == {"selection": "explicit_path", "found": False}
    assert setup_hint().startswith("credentials_file=not_found") and str(tmp_path) not in setup_hint()


def test_caller_supplied_registry_is_not_second_guessed():
    result = search_materials(MaterialSearchRequest("公司研究"), registry=MaterialRegistry())
    assert "no_material_source_enabled" not in {d.code for d in result.diagnostics}


def test_get_data_separates_missing_configuration_from_a_missing_feature():
    result = get_data(DataRequest("prices_daily", symbols=("600519.SH",), start=date(2026, 1, 5), end=date(2026, 1, 9)))
    codes = {d.code: d for d in result.diagnostics}
    assert "no_data_provider_registered" in codes
    assert codes["no_data_source_enabled"].failure_kind == FailureKind.NO_CREDENTIAL
    assert codes["no_data_source_enabled"].message.startswith("credentials_file=found")
