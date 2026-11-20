"""scripts/check_workflow_policy.py 테스트 (#56):  python -m unittest discover -s scripts -p "test_*.py" """

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_workflow_policy import check  # noqa: E402

GOOD_DEPLOY = """
name: Deploy
on:
  workflow_run:
    workflows: [CI]
    types: [completed]
    branches: [main]
  workflow_dispatch:
jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
  deploy:
    needs: plan
    if: github.repository == 'yeeun5364/lemit'
    runs-on: [self-hosted, Linux, X64, lemit-deploy]
    environment: production
    steps:
      - run: echo deploy
"""

GOOD_CI = """
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  lint:
    runs-on: ubuntu-latest  # self-hosted 금지 (주석은 괜찮음)
    steps:
      - run: echo lint
"""


class WorkflowPolicyTests(unittest.TestCase):
    def run_check(self, deploy=GOOD_DEPLOY, ci=GOOD_CI):
        with tempfile.TemporaryDirectory() as d:
            if deploy is not None:
                Path(d, "deploy.yml").write_text(textwrap.dedent(deploy), encoding="utf-8")
            Path(d, "ci.yml").write_text(textwrap.dedent(ci), encoding="utf-8")
            return check(Path(d))

    def assert_error(self, errors, text):
        self.assertTrue(any(text in e for e in errors), errors)

    def test_repository_workflows_pass(self):
        self.assertEqual(check(), [])  # 실제 저장소 .github/workflows

    def test_good_example_passes(self):
        self.assertEqual(self.run_check(), [])

    def test_self_hosted_in_ci_is_rejected(self):
        for runs_on in ("self-hosted", "[self-hosted, Linux]", "${{ 'self-hosted' }}", "{group: mine}"):
            with self.subTest(runs_on=runs_on):
                errors = self.run_check(
                    ci=GOOD_CI.replace("ubuntu-latest  # self-hosted 금지 (주석은 괜찮음)", runs_on)
                )
                self.assert_error(errors, "deploy.yml에서만")

    def test_pr_and_push_triggers_on_deploy_are_rejected(self):
        for trigger in ("pull_request:", "pull_request_target:", "push:", "issue_comment:"):
            with self.subTest(trigger=trigger):
                errors = self.run_check(
                    deploy=GOOD_DEPLOY.replace("  workflow_dispatch:", f"  workflow_dispatch:\n  {trigger}")
                )
                self.assert_error(errors, "허용하지 않는 트리거")

    def test_inline_trigger_list_is_checked(self):
        inline = GOOD_DEPLOY[: GOOD_DEPLOY.index("on:")] + "on: [pull_request, workflow_dispatch]\n"
        inline += GOOD_DEPLOY[GOOD_DEPLOY.index("jobs:") :]
        self.assert_error(self.run_check(deploy=inline), "허용하지 않는 트리거")

    def test_workflow_run_must_be_ci_on_main(self):
        errors = self.run_check(deploy=GOOD_DEPLOY.replace("branches: [main]", "branches: ['**']"))
        self.assert_error(errors, "branches: [main]")

    def test_self_hosted_job_requirements(self):
        cases = [  # (바꿀 부분, 바꾼 값, 기대 오류)
            ("environment: production", "environment: staging", "environment: production"),
            ("environment: production", "environment: {name: production}", None),  # 객체 형식도 허용
            ("    needs: plan\n", "", "needs"),
            ("if: github.repository == 'yeeun5364/lemit'", "if: always()", "github.repository"),
            ("      - run: echo deploy", "      - uses: actions/checkout@v5", "액션(uses)"),
        ]
        for old, new, msg in cases:
            with self.subTest(change=new):
                self.assertIn(old, GOOD_DEPLOY)
                errors = self.run_check(deploy=GOOD_DEPLOY.replace(old, new))
                if msg is None:
                    self.assertEqual(errors, [])
                else:
                    self.assert_error(errors, msg)

    def test_needs_on_self_hosted_job_is_rejected(self):
        two = GOOD_DEPLOY.replace("runs-on: ubuntu-latest", "runs-on: [self-hosted]\n    environment: production")
        self.assert_error(self.run_check(deploy=two), "GitHub-hosted 검증 작업")

    def test_missing_deploy_is_error(self):
        self.assert_error(self.run_check(deploy=None), "deploy.yml가 없습니다")


if __name__ == "__main__":
    unittest.main()
