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
    if: >-
      github.repository == 'yeeun5364/lemit' &&
      ((github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main') ||
       (github.event_name == 'workflow_run' &&
        vars.CD_AUTO_DEPLOY == 'true' &&
        github.event.workflow_run.conclusion == 'success' &&
        github.event.workflow_run.event == 'push' &&
        github.event.workflow_run.head_branch == 'main' &&
        github.event.workflow_run.head_repository.full_name == github.repository))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: |
          if [ "$EVENT" = "workflow_run" ]; then REMOTE="deploy-auto $SHA"; else REMOTE="deploy $SHA"; fi
  deploy:
    needs: plan
    if: github.repository == 'yeeun5364/lemit'
    runs-on: [self-hosted, Linux, X64, lemit-deploy]
    environment: production
    concurrency:
      group: >-
        ${{ needs.plan.outputs.action == 'check' && 'deploy-production-check' ||
            needs.plan.outputs.action == 'rollback' && 'deploy-production-rollback' || 'deploy-production' }}
      cancel-in-progress: false
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
CI_RUNS_ON = "ubuntu-latest  # self-hosted 금지 (주석은 괜찮음)"


class WorkflowPolicyTests(unittest.TestCase):
    def run_check(self, deploy=GOOD_DEPLOY, ci=GOOD_CI):
        with tempfile.TemporaryDirectory() as d:
            if deploy is not None:
                Path(d, "deploy.yml").write_text(textwrap.dedent(deploy), encoding="utf-8")
            Path(d, "ci.yml").write_text(textwrap.dedent(ci), encoding="utf-8")
            return check(Path(d))

    def assert_error(self, errors, text):
        self.assertTrue(any(text in e for e in errors), errors)

    def deploy_with(self, old, new):
        self.assertIn(old, GOOD_DEPLOY)
        return self.run_check(deploy=GOOD_DEPLOY.replace(old, new, 1))

    def test_repository_workflows_pass(self):
        self.assertEqual(check(), [])  # 실제 저장소 .github/workflows

    def test_good_example_passes(self):
        self.assertEqual(self.run_check(), [])

    def test_self_hosted_or_non_hosted_runner_in_ci_is_rejected(self):
        cases = {
            "self-hosted": "deploy.yml에서만",
            "[self-hosted, Linux]": "deploy.yml에서만",
            "${{ 'self-hosted' }}": "deploy.yml에서만",
            "${{ vars.RUNNER }}": "GitHub-hosted 라벨",  # 표현식으로 라벨을 숨기는 경우
            "{group: mine}": "GitHub-hosted 라벨",
            "lemit-deploy": "GitHub-hosted 라벨",
            "ubuntu-personal": "GitHub-hosted 라벨",  # 접두사만 같은 사용자 지정 라벨
        }
        for runs_on, msg in cases.items():
            with self.subTest(runs_on=runs_on):
                self.assert_error(self.run_check(ci=GOOD_CI.replace(CI_RUNS_ON, runs_on)), msg)

    def test_other_hosted_labels_are_allowed(self):
        for runs_on in ("ubuntu-24.04", "windows-latest", "macos-15"):
            with self.subTest(runs_on=runs_on):
                self.assertEqual(self.run_check(ci=GOOD_CI.replace(CI_RUNS_ON, runs_on)), [])

    def test_pr_and_push_triggers_on_deploy_are_rejected(self):
        for trigger in ("pull_request:", "pull_request_target:", "push:", "issue_comment:"):
            with self.subTest(trigger=trigger):
                errors = self.deploy_with("  workflow_dispatch:", f"  workflow_dispatch:\n  {trigger}")
                self.assert_error(errors, "허용하지 않는 트리거")

    def test_inline_trigger_list_is_checked(self):
        inline = GOOD_DEPLOY[: GOOD_DEPLOY.index("on:")] + "on: [pull_request, workflow_dispatch]\n"
        inline += GOOD_DEPLOY[GOOD_DEPLOY.index("jobs:") :]
        self.assert_error(self.run_check(deploy=inline), "허용하지 않는 트리거")

    def test_workflow_run_must_be_exactly_ci_completed_on_main(self):
        for old, new in [
            ("branches: [main]", "branches: ['**']"),
            ("types: [completed]", "types: [requested, completed]"),
            ("workflows: [CI]", "workflows: [CI, Other]"),
            ("    branches: [main]\n", ""),
        ]:
            with self.subTest(change=new or "branches 삭제"):
                self.assert_error(self.deploy_with(old, new), "workflow_run은")

    def test_self_hosted_job_requirements(self):
        cases = [  # (바꿀 부분, 바꾼 값, 기대 오류 / None이면 통과)
            ("environment: production", "environment: staging", "environment: production"),
            ("environment: production", "environment: {name: production}", None),
            ("    needs: plan\n", "", "needs로"),
            (
                "if: github.repository == 'yeeun5364/lemit'\n    runs-on: [self",
                "if: always()\n    runs-on: [self",
                "if에 github",
            ),
            ("      - run: echo deploy", "      - uses: actions/checkout@v5", "액션(uses)"),
            ("      cancel-in-progress: false\n", "      cancel-in-progress: true\n", "concurrency"),
            ("    concurrency:\n", "    no_concurrency:\n", "concurrency"),
            (">-\n        ${{", ">-\n        x${{", "다른 그룹"),
            # 롤백을 배포와 같은 그룹으로 되돌림 (새 자동 배포가 대기 중인 롤백을 취소할 수 있음, PR #58 리뷰 P2)
            ("'deploy-production-rollback'", "'deploy-production'", "다른 그룹"),
            ("needs.plan.outputs.action == 'rollback' && 'deploy-production-rollback' || ", "", "다른 그룹"),
            # 그룹이 한 줄이든 여러 줄이든 같은 식이면 통과
            ("      group: >-\n        ${{", "      group: ${{", None),
            # 자동 배포를 deploy로 요청 (서버의 롤백 뒤 보류를 우회)
            ('REMOTE="deploy-auto $SHA"', 'REMOTE="deploy $SHA"', "deploy-auto"),
        ]
        for old, new, msg in cases:
            with self.subTest(change=new or f"삭제: {old.strip()[:30]}"):
                errors = self.deploy_with(old, new)
                if msg is None:
                    self.assertEqual(errors, [])
                else:
                    self.assert_error(errors, msg)

    def test_gate_job_must_keep_event_and_ref_conditions(self):
        for cond in (
            "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'",
            "github.event.workflow_run.event == 'push'",
            "github.event.workflow_run.head_repository.full_name == github.repository",
            "github.event.workflow_run.conclusion == 'success'",
            "vars.CD_AUTO_DEPLOY == 'true'",
        ):
            with self.subTest(removed=cond):
                errors = self.deploy_with(cond, "true")
                self.assert_error(errors, "if에 조건이 없습니다")

    def test_workflow_level_concurrency_is_rejected(self):
        errors = self.deploy_with("jobs:\n", "concurrency:\n  group: deploy-production\njobs:\n")
        self.assert_error(errors, "최상위 concurrency")

    def test_gate_job_must_be_github_hosted(self):
        errors = self.deploy_with("    runs-on: ubuntu-latest\n", "    runs-on: [self-hosted]\n")
        self.assert_error(errors, "정확히 하나")
        self.assert_error(errors, "GitHub-hosted 검증 작업")

    def test_missing_deploy_is_error(self):
        self.assert_error(self.run_check(deploy=None), "deploy.yml가 없습니다")


if __name__ == "__main__":
    unittest.main()
