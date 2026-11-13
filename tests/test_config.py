from pathlib import Path

import pytest

from qualitybridge.config import ConfigurationError, load_config
from qualitybridge.models import ProjectConfig
from qualitybridge.strategies import select_suite


def test_paths_are_relative_to_config(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    path = nested / "qualitybridge.yaml"
    path.write_text("project: Exemplo\nrunner: pytest\n", encoding="utf-8")
    _, root, output = load_config(path)
    assert root == nested
    assert output == nested / ".qualitybridge"


@pytest.mark.parametrize(
    "content",
    [
        "project: X\nrunner: unknown",
        "project: X\nrunner: pytest\ntimeout_seconds: 0",
        "project: X\nrunner: pytest\nunknown: true",
        "[]",
        "",
        "project: X\nrunner: pytest\nsuites: {quick: {}}",
        "project: X\nrunner: pytest\nsuites: {full: {paths: ['--help']}}",
        "project: X\nrunner: pytest\noutput_dir: .",
    ],
)
def test_invalid_configuration(tmp_path, content):
    path = tmp_path / "qualitybridge.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_unknown_suite_does_not_fallback():
    with pytest.raises(ConfigurationError, match="quick"):
        select_suite(ProjectConfig(project="X", runner="pytest"), "quick")


def test_missing_config():
    with pytest.raises(ConfigurationError):
        load_config(Path("nonexistent-qualitybridge.yaml"))
