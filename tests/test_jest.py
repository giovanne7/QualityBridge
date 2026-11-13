import json

from qualitybridge.jest import read_jest


def test_parses_jest_results(tmp_path):
    path = tmp_path / "report.json"

    data = {
        "testResults": [
            {
                "name": "tests/example.test.js",
                "status": "passed",
                "assertionResults": [
                    {
                        "fullName": "Example works",
                        "title": "works",
                        "status": "passed",
                        "duration": 200,
                    },
                    {
                        "fullName": "Example fails",
                        "title": "fails",
                        "status": "failed",
                        "duration": 100,
                        "failureMessages": [
                            "Expected 2 to be 3.\n    at Object.test (example.test.js:10:5)"
                        ],
                    },
                    {
                        "fullName": "Example skipped",
                        "title": "skipped",
                        "status": "pending",
                        "duration": 0,
                    },
                ],
            }
        ]
    }

    path.write_text(json.dumps(data), encoding="utf-8")

    cases = read_jest(path)

    assert len(cases) == 3
    assert [case.status for case in cases] == ["passed", "failed", "skipped"]
    assert cases[0].duration_seconds == 0.2
    assert cases[1].message == "Expected 2 to be 3."
    assert "at Object.test" in cases[1].stack_trace


def test_parses_suite_level_error(tmp_path):
    path = tmp_path / "report.json"

    data = {
        "testResults": [
            {
                "name": "tests/setup.test.js",
                "status": "failed",
                "assertionResults": [],
                "failureMessage": "SyntaxError: Unexpected token",
            }
        ]
    }

    path.write_text(json.dumps(data), encoding="utf-8")

    cases = read_jest(path)

    assert len(cases) == 1
    assert cases[0].status == "error"
    assert "SyntaxError" in cases[0].message


def test_rejects_invalid_jest_report(tmp_path):
    path = tmp_path / "report.json"
    path.write_text('{"invalid": true}', encoding="utf-8")

    try:
        read_jest(path)
    except TypeError:
        pass
    else:
        raise AssertionError("Era esperado um TypeError.")
