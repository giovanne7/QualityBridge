from pathlib import Path

from qualitybridge.adapters import factory_for
from qualitybridge.models import ProjectConfig, SuiteConfig


def test_playwright_uses_project_install_and_report_environment():
    config = ProjectConfig(project="Web", runner="playwright")
    adapter = factory_for("playwright").create_adapter()
    report = Path("some path") / "raw.xml"
    args = adapter.command(config, SuiteConfig(paths=["tests"], filter="@smoke"), Path("."), report)
    assert args == [
        "node",
        "node_modules/@playwright/test/cli.js",
        "test",
        "--reporter=junit",
        "--grep",
        "@smoke",
        "tests",
    ]
    assert adapter.environment(report)["PLAYWRIGHT_JUNIT_OUTPUT_FILE"] == str(report)


def test_jest_uses_project_install_and_json_report():
    config = ProjectConfig(project="Web", runner="jest")
    adapter = factory_for("jest").create_adapter()
    report = Path("some path") / "raw.json"

    args = adapter.command(
        config,
        SuiteConfig(paths=["src", "tests"]),
        Path("."),
        report,
    )

    assert args == [
        "node",
        "node_modules/jest/bin/jest.js",
        "--json",
        f"--outputFile={report}",
        "src",
        "tests",
    ]


def test_jest_supports_test_name_filter():
    config = ProjectConfig(project="Web", runner="jest")
    adapter = factory_for("jest").create_adapter()
    report = Path("report.json")

    report = Path("some path") / "raw.json"
    args = adapter.command(
        config,
        SuiteConfig(paths=["tests"], filter="login"),
        Path("."),
        report,
    )

    assert "--testNamePattern" in args
    assert args[args.index("--testNamePattern") + 1] == "login"
    assert args[-1] == "tests"
