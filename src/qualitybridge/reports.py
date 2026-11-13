from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from qualitybridge.junit import write_junit
from qualitybridge.models import RunResult


def write_reports(result: RunResult, directory: Path) -> None:
    result.artifacts.update(
        {"json": "report.json", "html": "report.html", "junit": "junit.xml", "log": "execution.log"}
    )
    if (directory / "raw-junit.xml").exists():
        result.artifacts["raw_junit"] = "raw-junit.xml"

    if (directory / "raw-jest.json").exists():
        result.artifacts["raw_jest"] = "raw-jest.json"
    (directory / "report.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    write_junit(result, directory / "junit.xml")
    environment = Environment(
        loader=PackageLoader("qualitybridge", "templates"), autoescape=select_autoescape(["html"])
    )
    html = environment.get_template("report.html").render(run=result, counts=result.counts)
    (directory / "report.html").write_text(html, encoding="utf-8")
