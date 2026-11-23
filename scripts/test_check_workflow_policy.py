"""scripts/check_workflow_policy.py 테스트 (#56):  python -m unittest discover -s scripts -p "test_*.py" """

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_workflow_policy import check  # noqa: E402

CHECKOUT = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
GOOD_DEPLOY = """
name: Deploy
on:
  workflow_run:
    workflows: [Release tag]
    types: [completed]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  plan:
    if: >-
      github.repository == 'yeeun5364/lemit' &&
      ((github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main') ||
       (github.event_name == 'workflow_run' &&
        vars.CD_AUTO_DEPLOY == 'true' &&
        github.event.workflow_run.conclusion == 'success' &&
        github.event.workflow_run.event == 'push' &&
        github.event.workflow_run.path == '.github/workflows/release-tag.yml' &&
        github.event.workflow_run.head_repository.full_name == github.repository))
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
      actions: read
    steps:
      - run: |
          if [ "$EVENT" = "workflow_run" ]; then REMOTE="deploy-image $VERSION_TAG $SHA"; else REMOTE="deploy $SHA"; fi
  test:
    needs: plan
    if: needs.plan.outputs.action == 'deploy-image'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
        with:
          ref: ${{ needs.plan.outputs.sha }}
          persist-credentials: false
      - run: echo test
  train:
    needs: [plan, test]
    if: needs.plan.outputs.action == 'deploy-image'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - run: echo train
  parity:
    needs: [plan, train]
    if: needs.plan.outputs.action == 'deploy-image'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - run: echo parity
  image:
    needs: [plan, train, parity]
    if: needs.plan.outputs.action == 'deploy-image'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    env:
      TOKEN: ${{ github.token }}
    steps:
      - run: echo image
  release:
    needs: [plan, train, image]
    if: needs.plan.outputs.action == 'deploy-image'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - run: echo release
  deploy:
    needs: [plan, release]
    if: >-
      !cancelled() && github.repository == 'yeeun5364/lemit' &&
      needs.plan.result == 'success' && needs.plan.outputs.remote != '' &&
      (needs.plan.outputs.action != 'deploy-image' || needs.release.result == 'success')
    runs-on: [self-hosted, Linux, X64, lemit-deploy]
    environment: production
    concurrency:
      group: >-
        ${{ needs.plan.outputs.action == 'check' && 'deploy-production-check' ||
            needs.plan.outputs.action == 'rollback' && 'deploy-production-rollback' || 'deploy-production' }}
      cancel-in-progress: false
    permissions:
      contents: read
    env:
      KEY: ${{ secrets.LIGHTSAIL_SSH_KEY }}
    steps:
      - run: |
          [ "$ACTION" != deploy-image ] || REMOTE="$REMOTE $IMAGE_DIGEST"
"""
PLAN_RUNS_ON = "    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"

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

GOOD_RELEASE_TAG = """
name: Release tag
# secrets. 는 주석에만 (괜찮음)
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
  actions: read
jobs:
  verify:
    runs-on: ubuntu-latest
    env:
      GH_TOKEN: ${{ github.token }}
    steps:
      - run: echo verify
"""


class WorkflowPolicyTests(unittest.TestCase):
    def run_check(self, deploy=GOOD_DEPLOY, ci=GOOD_CI, release=GOOD_RELEASE_TAG):
        with tempfile.TemporaryDirectory() as d:
            if deploy is not None:
                Path(d, "deploy.yml").write_text(textwrap.dedent(deploy), encoding="utf-8")
            if release is not None:
                Path(d, "release-tag.yml").write_text(textwrap.dedent(release), encoding="utf-8")
            Path(d, "ci.yml").write_text(textwrap.dedent(ci), encoding="utf-8")
            return check(Path(d))

    def release_with(self, old, new):
        self.assertIn(old, GOOD_RELEASE_TAG)
        return self.run_check(release=GOOD_RELEASE_TAG.replace(old, new, 1))

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

    def test_workflow_run_must_be_exactly_release_tag_completed(self):
        for old, new in [
            ("types: [completed]", "types: [requested, completed]"),
            ("workflows: [Release tag]", "workflows: [Release tag, CI]"),
            # main push 자동 배포로 되돌림 (태그 배포와 중복)
            ("workflows: [Release tag]", "workflows: [CI]"),
            ("    types: [completed]\n", "    types: [completed]\n    branches: [main]\n"),
        ]:
            with self.subTest(change=new):
                self.assert_error(self.deploy_with(old, new), "workflow_run은")

    def test_self_hosted_job_requirements(self):
        cases = [  # (바꿀 부분, 바꾼 값, 기대 오류 / None이면 통과)
            ("environment: production", "environment: staging", "environment: production"),
            ("environment: production", "environment: {name: production}", None),
            ("    needs: [plan, release]\n", "", "needs로"),
            ("    needs: [plan, release]\n", "    needs: [release]\n", "needs에 검증 작업 plan"),
            (
                "!cancelled() && github.repository == 'yeeun5364/lemit' &&\n",
                "!cancelled() &&\n",
                "if에 github",
            ),
            # 수동 실행 때문에 !cancelled()를 쓰므로, plan 성공·release 성공 조건이 빠지면
            # 앞 작업이 실패해도 배포될 수 있음
            ("needs.plan.result == 'success' && ", "", "needs.plan.result == 'success'"),
            ("(needs.plan.outputs.action != 'deploy-image' || needs.release.result == 'success')", "true", "release"),
            ('          [ "$ACTION" != deploy-image ] || REMOTE="$REMOTE $IMAGE_DIGEST"', "          echo x", "digest"),
            (
                "    steps:\n      - run: |\n          [",
                "    steps:\n      - uses: x/y@v1\n      - run: |\n          [",
                "액션(uses)",
            ),
            ("      cancel-in-progress: false\n", "      cancel-in-progress: true\n", "concurrency"),
            ("    concurrency:\n", "    no_concurrency:\n", "concurrency"),
            (">-\n        ${{", ">-\n        x${{", "다른 그룹"),
            # 롤백을 배포와 같은 그룹으로 되돌림 (새 자동 배포가 대기 중인 롤백을 취소할 수 있음, PR #58 리뷰 P2)
            ("'deploy-production-rollback'", "'deploy-production'", "다른 그룹"),
            ("needs.plan.outputs.action == 'rollback' && 'deploy-production-rollback' || ", "", "다른 그룹"),
            # 그룹이 한 줄이든 여러 줄이든 같은 식이면 통과
            ("      group: >-\n        ${{", "      group: ${{", None),
            # 태그 자동 배포를 deploy로 요청 (서버의 롤백 뒤 보류·태그·Release 재확인을 우회)
            ('REMOTE="deploy-image $VERSION_TAG $SHA"', 'REMOTE="deploy $SHA"', "deploy-image"),
            # 서버에서 다시 빌드하는 태그 배포 요청으로 되돌림
            ('REMOTE="deploy-image $VERSION_TAG $SHA"', 'REMOTE="deploy-tag $VERSION_TAG $SHA"', "deploy-tag"),
            # main push 자동 배포 요청을 다시 넣음 (중복 배포)
            (
                'REMOTE="deploy-image $VERSION_TAG $SHA"',
                'REMOTE="deploy-image $VERSION_TAG $SHA"; X=deploy-auto',
                "deploy-auto",
            ),
        ]
        for old, new, msg in cases:
            with self.subTest(change=new or f"삭제: {old.strip()[:30]}"):
                errors = self.deploy_with(old, new)
                if msg is None:
                    self.assertEqual(errors, [])
                else:
                    self.assert_error(errors, msg)

    def test_pipeline_order_and_skip_rules(self):
        """앞 작업이 실패·건너뛰면 뒤 작업이 실행되지 않는 구조.

        plan → test → train → parity → image → release → deploy
        """
        cases = [
            ("    needs: [plan, train]\n", "    needs: [plan, test]\n", "needs는"),  # parity가 train을 건너뜀
            ("    needs: [plan, train, image]\n", "    needs: [plan, train]\n", "needs는"),  # release가 image 전에
            ("    needs: [plan, release]\n", "    needs: [plan, image]\n", "needs는"),  # deploy가 release를 건너뜀
            ("  parity:\n", "  parity2:\n", "작업은"),
            (
                "    needs: [plan, test]\n    if: needs.plan.outputs.action == 'deploy-image'\n",
                "    needs: [plan, test]\n    if: always() && needs.plan.outputs.action == 'deploy-image'\n",
                "always()",
            ),
            (
                "    needs: [plan, train, parity]\n    if: needs.plan.outputs.action == 'deploy-image'\n",
                "    needs: [plan, train, parity]\n    if: ${{ !cancelled() }}\n",
                "cancelled()",
            ),
            (
                "    needs: [plan, train, image]\n    if: needs.plan.outputs.action == 'deploy-image'\n",
                "    needs: [plan, train, image]\n    if: success()\n",
                "deploy-image' 조건",  # 수동 실행에서도 release가 돌게 됨
            ),
        ]
        for old, new, msg in cases:
            with self.subTest(change=new.strip()[:50]):
                self.assert_error(self.deploy_with(old, new), msg)

    def test_pipeline_permissions_secrets_and_checkout(self):
        cases = [
            ("permissions:\n  contents: read\njobs:", "permissions:\n  contents: write\njobs:", "최상위 permissions"),
            (
                "      contents: read\n      packages: write\n",
                "      contents: write\n      packages: write\n",
                "permissions는",
            ),
            (
                "    permissions:\n      contents: write\n",
                "    permissions:\n      contents: write\n      packages: write\n",
                "permissions는",
            ),
            (
                "  train:\n    needs: [plan, test]\n",
                "  train:\n    environment: production\n    needs: [plan, test]\n",
                "environment는 deploy",
            ),
            ("      TOKEN: ${{ github.token }}", "      KEY: ${{ secrets.LIGHTSAIL_SSH_KEY }}", "Secret은 deploy"),
            # workflow_run의 기본 커밋(github.sha = main 최신)을 받으면 태그 커밋이 아닌 코드를 시험하게 됨
            ("          ref: ${{ needs.plan.outputs.sha }}\n", "          ref: ${{ github.sha }}\n", "ref:"),
            ("          ref: ${{ needs.plan.outputs.sha }}\n", "", "ref:"),
            ("          persist-credentials: false\n", "", "persist-credentials"),
            (f"      - uses: {CHECKOUT}  # v5.1.0", "      - uses: actions/checkout@v5", "커밋 SHA 40자리"),
        ]
        for old, new, msg in cases:
            with self.subTest(change=new.strip()[:50]):
                self.assert_error(self.deploy_with(old, new), msg)

    def test_gate_job_must_keep_event_and_ref_conditions(self):
        for cond in (
            "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'",
            "github.event.workflow_run.event == 'push'",
            "github.event.workflow_run.head_repository.full_name == github.repository",
            "github.event.workflow_run.conclusion == 'success'",
            "vars.CD_AUTO_DEPLOY == 'true'",  # 자동 배포 끄기 스위치
            "github.event.workflow_run.path == '.github/workflows/release-tag.yml'",
        ):
            with self.subTest(removed=cond):
                errors = self.deploy_with(cond, "true")
                self.assert_error(errors, "if에 조건이 없습니다")

    def test_workflow_level_concurrency_is_rejected(self):
        errors = self.deploy_with("jobs:\n", "concurrency:\n  group: deploy-production\njobs:\n")
        self.assert_error(errors, "최상위 concurrency")

    def test_gate_job_must_be_github_hosted(self):
        errors = self.deploy_with(PLAN_RUNS_ON, "    runs-on: [self-hosted]\n    timeout-minutes: 5\n")
        self.assert_error(errors, "정확히 하나")
        self.assert_error(errors, "GitHub-hosted 검증 작업")

    def test_missing_deploy_is_error(self):
        self.assert_error(self.run_check(deploy=None), "deploy.yml가 없습니다")

    def test_missing_release_tag_is_error(self):
        self.assert_error(self.run_check(release=None), "release-tag.yml가 없습니다")

    def test_release_tag_rules(self):
        runs_on = "    runs-on: ubuntu-latest\n"
        cases = [  # (바꿀 부분, 바꾼 값, 기대 오류)
            ("name: Release tag", "name: Release", "name은"),
            ('    tags: ["v*"]', '    tags: ["*"]', "트리거는"),
            ('    tags: ["v*"]', '    tags: ["v*"]\n    branches: [main]', "트리거는"),
            ("on:\n", "on:\n  pull_request:\n", "트리거는"),
            ("  actions: read\n", "  actions: read\n  deployments: write\n", "permissions는"),
            ("permissions:\n  contents: read", "permissions:\n  contents: write", "permissions는"),
            (runs_on, runs_on + "    permissions: write-all\n", "작업 단위"),
            (runs_on, runs_on + "    environment: production\n", "environment"),
            ("      GH_TOKEN: ${{ github.token }}", "      KEY: ${{ secrets.LIGHTSAIL_SSH_KEY }}", "Secret"),
            (runs_on, "    runs-on: [self-hosted, lemit-deploy]\n", "deploy.yml에서만"),
        ]
        for old, new, msg in cases:
            with self.subTest(change=new):
                self.assert_error(self.release_with(old, new), msg)

    def test_tag_push_trigger_only_in_release_tag(self):
        ci = GOOD_CI.replace("    branches: [main]\n", "    branches: [main]\n    tags: ['v*']\n", 1)
        self.assertNotEqual(ci, GOOD_CI)
        self.assert_error(self.run_check(ci=ci), "태그 푸시 트리거는")


if __name__ == "__main__":
    unittest.main()
