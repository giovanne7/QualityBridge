import sys

from qualitybridge.auto_healing import HealingSuggestion
from qualitybridge.healing_validation import SuggestionValidator, ValidationRequest


def project(tmp_path, expected="login-button"):
    (tmp_path / "app.py").write_text('BUTTON_ID = "signin-button"\n', encoding="utf-8")
    (tmp_path / "test_ui.py").write_text(
        "from app import BUTTON_ID\n\n"
        "def test_button_selector():\n"
        f'    assert BUTTON_ID == "{expected}"\n',
        encoding="utf-8",
    )
    return tmp_path / "test_ui.py"


def suggestion(new_text="signin-button"):
    return HealingSuggestion(
        test_name="test_button_selector",
        problem_type="selector_changed",
        classification="outdated_test",
        explanation="selector changed",
        evidence=["controlled fixture"],
        file_path="test_ui.py",
        old_text="login-button",
        new_text=new_text,
        diff="-login-button\n+" + new_text,
    )


def request():
    return ValidationRequest(
        runner="pytest",
        test_selector="test_ui.py::test_button_selector",
        test_name="test_button_selector",
        python_executable=sys.executable,
        timeout_seconds=30,
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )


def test_valid_suggestion_runs_relevant_test_and_preserves_original(tmp_path):
    test_file = project(tmp_path)
    original = test_file.read_text(encoding="utf-8")

    updated, outcome = SuggestionValidator().validate(suggestion(), request(), tmp_path)

    assert outcome.state == "validated"
    assert outcome.executed_case == "test_button_selector"
    assert outcome.preserved_original is True
    assert updated.state == "validated"
    assert test_file.read_text(encoding="utf-8") == original


def test_rejects_suggestion_that_does_not_resolve_failure(tmp_path):
    test_file = project(tmp_path)
    original = test_file.read_text(encoding="utf-8")

    updated, outcome = SuggestionValidator().validate(
        suggestion(new_text="wrong-button"), request(), tmp_path
    )

    assert outcome.state == "rejected"
    assert "continua falhando" in outcome.message
    assert updated.state == "rejected"
    assert test_file.read_text(encoding="utf-8") == original


def test_rejects_ambiguous_change_without_modifying_original(tmp_path):
    test_file = project(tmp_path)
    test_file.write_text(
        test_file.read_text(encoding="utf-8") + "\n# login-button\n", encoding="utf-8"
    )
    original = test_file.read_text(encoding="utf-8")

    updated, outcome = SuggestionValidator().validate(suggestion(), request(), tmp_path)

    assert outcome.state == "rejected"
    assert "ambígua" in outcome.message
    assert updated.state == "rejected"
    assert test_file.read_text(encoding="utf-8") == original


def test_rejects_path_outside_project(tmp_path):
    project(tmp_path)
    unsafe = suggestion().model_copy(update={"file_path": "../outside.py"})

    try:
        SuggestionValidator().validate(unsafe, request(), tmp_path)
    except ValueError as exc:
        assert "fora" in str(exc)
    else:
        raise AssertionError("Caminho externo deveria ser rejeitado.")
