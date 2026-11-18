"""Run the white-box suite and save expected and actual results for submission."""
import html
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class TestResults(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.outcomes = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes[test.id()] = ("PASS", "")

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.outcomes[test.id()] = ("FAIL", self._exc_info_to_string(error, test))

    def addError(self, test, error):
        super().addError(test, error)
        self.outcomes[test.id()] = ("ERROR", self._exc_info_to_string(error, test))

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error:
            self.outcomes[test.id()] = ("FAIL", self._exc_info_to_string(error, test))

    def stopTest(self, test):
        status, error = self.outcomes.get(test.id(), ("PASS", ""))
        info = getattr(getattr(test, test._testMethodName), "test_info", {})
        self.records.append({"test_name": test._testMethodName, **info, "status": status,
                             "actual_output": getattr(test, "actual", []), "failure": error})
        super().stopTest(test)


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT/"cloud_tests"))
    result = unittest.TextTestRunner(verbosity=2, resultclass=TestResults).run(suite)
    output = ROOT/"test_results"
    output.mkdir(exist_ok=True)
    summary = {"run_at_utc": datetime.now(timezone.utc).isoformat(), "tests": result.testsRun,
               "passed": sum(r["status"] == "PASS" for r in result.records),
               "failed": sum(r["status"] != "PASS" for r in result.records), "results": result.records}
    (output/"results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = []
    for r in result.records:
        fields = [("Use case", r.get("use_case", "")), ("Description", r.get("description", "")), ("Input", r.get("input", "")),
                  ("Expected output", r.get("expected_output", "")), ("White-box branch", r.get("white_box_branch", ""))]
        detail = "".join(f"<p><b>{html.escape(k)}:</b> {html.escape(v)}</p>" for k,v in fields)
        actual = html.escape(json.dumps(r["actual_output"], indent=2, ensure_ascii=False))
        error = f"<pre>{html.escape(r['failure'])}</pre>" if r["failure"] else ""
        rows.append(f"<article><h2>{html.escape(r['test_name'])} <span>{r['status']}</span></h2>{detail}<details><summary>Actual output from this run</summary><pre>{actual}</pre></details>{error}</article>")
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>DRRCS White Box Test Results</title>
<style>body{{font:16px/1.5 system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#192d40}}article{{border-top:1px solid #ccd4dd;padding:15px 0}}h2{{font-size:19px}}span{{color:#176333}}pre{{white-space:pre-wrap;background:#f2f5f8;padding:16px;overflow-wrap:anywhere}}p{{margin:6px 0}}summary{{cursor:pointer}}</style>
<h1>DRRCS UC-12 through UC-24 white box test results</h1>
<p>Run at {summary['run_at_utc']}. {summary['passed']}/{summary['tests']} passed. {summary['failed']} failed.</p>
<p>Cloud backend white-box tests. SQLite is used only as an isolated D1 test substitute; real Worker HTTP checks are recorded separately in cloud_verification.json. Inputs, expected results and actual outputs are shown below.</p>
{''.join(rows)}</html>"""
    (output/"results.html").write_text(page, encoding="utf-8")
    print(f"\nSaved {output/'results.html'}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
