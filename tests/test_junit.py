from xml.etree import ElementTree as ET

import pytest
from defusedxml.common import DefusedXmlException

from qualitybridge.junit import read_junit, write_junit
from qualitybridge.models import RunResult


def test_parses_all_states(tmp_path):
    path = tmp_path / "raw.xml"
    path.write_text(
        """<testsuites><testsuite>
      <testcase name="ok" time="0.2"/>
      <testcase name="bad"><failure message="expected">details</failure></testcase>
      <testcase name="setup"><error message="setup error"/></testcase>
      <testcase name="skip"><skipped message="pending"/></testcase>
    </testsuite></testsuites>""",
        encoding="utf-8",
    )
    cases = read_junit(path)
    assert [case.status for case in cases] == ["passed", "failed", "error", "skipped"]
    assert cases[1].message == "expected"
    assert cases[1].stack_trace == "details"


def test_rejects_xml_entities(tmp_path):
    path = tmp_path / "evil.xml"
    path.write_text(
        """<!DOCTYPE testsuite [<!ENTITY x "secret">]>
    <testsuite><testcase name="&x;"/></testsuite>""",
        encoding="utf-8",
    )
    with pytest.raises(DefusedXmlException):
        read_junit(path)


@pytest.mark.parametrize("value", ["NaN", "inf", "-1", "invalid"])
def test_rejects_invalid_duration(tmp_path, value):
    path = tmp_path / "raw.xml"
    path.write_text(f'<testsuite><testcase name="x" time="{value}"/></testsuite>')
    with pytest.raises(ValueError):
        read_junit(path)


def test_infrastructure_error_is_visible_to_ci(tmp_path):
    result = RunResult(
        id="x",
        project="X",
        runner="pytest",
        suite="full",
        started_at="2026-01-01T00:00:00Z",
        duration_seconds=1,
        status="timeout",
        exit_code=None,
        command=[],
        diagnostics=["timeout"],
    )
    path = tmp_path / "junit.xml"
    write_junit(result, path)
    suite = ET.parse(path).getroot().find("testsuite")
    assert suite.get("errors") == "1"
    assert suite.find("testcase/error").text == "timeout"


def test_suite_level_error(tmp_path):
    path = tmp_path / "raw.xml"
    path.write_text('<testsuite><error message="global failure"/></testsuite>')
    assert read_junit(path)[0].status == "error"
