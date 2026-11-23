"""버전 태그 배포 판정 테스트: release-tag.yml·deploy.yml의 run 본문을 가짜 gh·git으로 실행한다.

워크플로의 판정 로직은 YAML 안의 bash에 있다 (러너 작업은 저장소 코드를 받지 않으므로). 여기서는 그 본문을 꺼내
GitHub Actions와 같은 옵션(bash -eo pipefail)으로 실행하고, GitHub API 응답은 가짜 gh가 돌려준다.
가짜 gh는 jq를 실행하지 않고 **jq를 거친 뒤의 결과**를 경로별로 돌려준다 (응답이 여러 개면 호출마다 다음 것).

서버 쪽 판정(롤백 보류 중 건너뜀, 태그·main 포함 재확인, 이미 배포된 커밋)은 scripts/test_ci_deploy.sh에서 시험한다.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
BASH = shutil.which("bash")
SHA = "a" * 40
OTHER = "b" * 40
REPO = "yeeun5364/lemit"
CI_JOBS = "린트 (ruff)\tsuccess\n테스트 (PostgreSQL 18)\tsuccess\nDocker 이미지 (빌드·스모크)\tsuccess"

FAKE_GH = r"""#!/usr/bin/env bash
# 가짜 gh: gh api <경로> [--jq 식] → routes에서 경로가 접두사로 맞는 첫 응답을 출력한다
# gh release create ... → 호출만 기록 (Release 노트는 작업 폴더의 notes.md로 남음)
if [ "${1:-}" = release ]; then echo "release $*" >> "$FAKE_DIR/calls.log"; exit 0; fi
[ "${1:-}" = api ] || { echo "fake gh: 지원하지 않는 명령 $*" >&2; exit 2; }
shift
path=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq|-q|-X|-H|-f|-F) shift 2 ;;
    -*) shift ;;
    *) [ -n "$path" ] || path="$1"; shift ;;
  esac
done
echo "$path" >> "$FAKE_DIR/calls.log"
n=0
while IFS=$'\t' read -r prefix name; do
  n=$((n + 1))
  case "$path" in
    "$prefix"*)
      c="$(cat "$FAKE_DIR/count.$n" 2> /dev/null || echo 0)"
      echo $((c + 1)) > "$FAKE_DIR/count.$n"
      f="$FAKE_DIR/resp/$name.$c"
      if [ ! -f "$f" ]; then f="$(ls "$FAKE_DIR/resp/$name".* | sort -t. -k2 -n | tail -n 1)"; fi
      if [ "$(cat "$f")" = "__FAIL__" ]; then echo "fake gh: Not Found (HTTP 404)" >&2; exit 1; fi
      if [ "$(cat "$f")" = "__ERROR__" ]; then echo "fake gh: Server Error (HTTP 502)" >&2; exit 1; fi
      cat "$f"
      exit 0 ;;
  esac
done < "$FAKE_DIR/routes"
echo "fake gh: 응답이 없는 경로 $path" >&2
exit 1
"""

FAKE_GIT = r"""#!/usr/bin/env bash
# 가짜 git: ls-remote만 (미리 정한 출력)
[ "${1:-}" = ls-remote ] || { echo "fake git: 지원하지 않는 명령 $*" >&2; exit 2; }
echo "$*" >> "$FAKE_DIR/calls.log"
cat "$FAKE_DIR/ls-remote"
"""


def step_script(workflow, job, name):
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return next(s["run"] for s in doc["jobs"][job]["steps"] if s.get("name") == name)


@unittest.skipUnless(BASH, "bash 필요")
class StepRunner(unittest.TestCase):
    workflow = job = step = ""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "resp").mkdir()
        (self.dir / "bin").mkdir()
        (self.dir / "work").mkdir()
        for tool, body in (("gh", FAKE_GH), ("git", FAKE_GIT)):
            p = self.dir / "bin" / tool
            p.write_text(body, encoding="utf-8", newline="\n")
            p.chmod(0o755)
        self.routes = []
        self.script = self.dir / "step.sh"
        self.script.write_text(step_script(self.workflow, self.job, self.step), encoding="utf-8", newline="\n")

    def gh(self, prefix, *responses):
        """경로 접두사에 응답(들)을 붙인다. 응답이 여러 개면 호출 순서대로, 다 쓰면 마지막 것을 반복."""
        name = f"r{len(self.routes)}"
        self.routes.append(f"{prefix}\t{name}")
        for i, body in enumerate(responses):
            text = body + "\n" if body else ""
            (self.dir / "resp" / f"{name}.{i}").write_text(text, encoding="utf-8", newline="\n")

    def ls_remote(self, text):
        (self.dir / "ls-remote").write_text(text + "\n", encoding="utf-8", newline="\n")

    def run_step(self, **env):
        (self.dir / "routes").write_text("\n".join(self.routes) + "\n", encoding="utf-8", newline="\n")
        out_file = self.dir / "github_output"
        out_file.write_text("", encoding="utf-8")
        base = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG")}
        full_env = {
            **base,
            "PATH": str(self.dir / "bin") + os.pathsep + base.get("PATH", ""),
            "FAKE_DIR": str(self.dir),
            "GITHUB_OUTPUT": str(out_file),
            "GITHUB_STEP_SUMMARY": str(self.dir / "summary"),
            "REPO": REPO,
            "LC_ALL": "C.UTF-8",
            **env,
        }
        proc = subprocess.run(
            [BASH, "--noprofile", "--norc", "-eo", "pipefail", str(self.script)],
            cwd=self.dir / "work",
            env=full_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        outputs = {}
        for line in out_file.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                outputs[k] = v
        calls_file = self.dir / "calls.log"
        calls = calls_file.read_text(encoding="utf-8").splitlines() if calls_file.exists() else []
        return proc.returncode, proc.stdout + proc.stderr, outputs, calls


class ReleaseTagCheckTests(StepRunner):
    """release-tag.yml: 태그 형식·대상 커밋·main 포함 (정상 태그, main 밖 커밋)."""

    workflow, job, step = "release-tag.yml", "verify", "태그 형식·대상 커밋·main 포함 확인"

    def test_lightweight_tag_on_main(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.1.1", f"commit {SHA}")
        self.gh(f"repos/yeeun5364/lemit/compare/{SHA}...main", "ahead")
        rc, out, outputs, _ = self.run_step(TAG="v0.1.1", PUSHED_SHA=SHA)
        self.assertEqual(rc, 0, out)
        self.assertEqual(outputs["sha"], SHA)

    def test_annotated_tag_is_followed_to_commit(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.2.0", f"tag {'c' * 40}")
        self.gh("repos/yeeun5364/lemit/git/tags/", f"commit {SHA}")
        self.gh(f"repos/yeeun5364/lemit/compare/{SHA}...main", "identical")
        rc, out, outputs, _ = self.run_step(TAG="v0.2.0", PUSHED_SHA=SHA)
        self.assertEqual(rc, 0, out)
        self.assertEqual(outputs["sha"], SHA)

    def test_commit_outside_main_fails(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.1.1", f"commit {SHA}")
        self.gh(f"repos/yeeun5364/lemit/compare/{SHA}...main", "diverged")
        rc, out, outputs, _ = self.run_step(TAG="v0.1.1", PUSHED_SHA=SHA)
        self.assertNotEqual(rc, 0)
        self.assertIn("main에 포함된 커밋이 아닙니다", out)
        self.assertNotIn("sha", outputs)

    def test_bad_tag_format_fails_without_api_calls(self):
        for tag in ("v1.2", "v01.2.3", "v1.2.3-rc1", "1.2.3", "vlatest"):
            with self.subTest(tag=tag):
                rc, out, _, calls = self.run_step(TAG=tag, PUSHED_SHA=SHA)
                self.assertNotEqual(rc, 0)
                self.assertIn("vMAJOR.MINOR.PATCH", out)
                self.assertEqual(calls, [])

    def test_missing_tag_fails_with_message(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.1.1", "__FAIL__")
        rc, out, _, _ = self.run_step(TAG="v0.1.1", PUSHED_SHA=SHA)
        self.assertNotEqual(rc, 0)
        self.assertIn("커밋을 찾지 못했습니다", out)

    def test_tag_moved_after_push_fails(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.1.1", f"commit {OTHER}")
        rc, out, _, _ = self.run_step(TAG="v0.1.1", PUSHED_SHA=SHA)
        self.assertNotEqual(rc, 0)
        self.assertIn("옮겨짐", out)


class ReleaseTagCiWaitTests(StepRunner):
    """release-tag.yml: main push CI 결과 기다리기 (CI 진행 중·실패·없음·시간 초과)."""

    workflow, job, step = "release-tag.yml", "verify", "main push CI 결과 기다리기"
    CI = "repos/yeeun5364/lemit/actions/workflows/ci.yml/runs"

    def wait(self, **env):
        return self.run_step(SHA=SHA, POLL_SECONDS="0", CI_WAIT_SECONDS="60", NO_RUN_GRACE="60", **env)

    def test_ci_already_succeeded(self):
        self.gh(self.CI, "11 completed success")
        rc, out, _, calls = self.wait()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(calls), 1)

    def test_waits_while_ci_in_progress_then_succeeds(self):
        self.gh(self.CI, "", "11 queued -", "11 in_progress -", "11 completed success")
        rc, out, _, calls = self.wait()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(calls), 4)
        self.assertIn("CI 진행 중", out)

    def test_ci_failure_fails(self):
        self.gh(self.CI, "11 in_progress -", "11 completed failure")
        rc, out, _, _ = self.wait()
        self.assertNotEqual(rc, 0)
        self.assertIn("성공하지 않았습니다", out)

    def test_cancelled_run_then_rerun_success_is_accepted(self):
        self.gh(self.CI, "12 in_progress -\n11 completed cancelled", "12 completed success\n11 completed cancelled")
        rc, out, _, _ = self.wait()
        self.assertEqual(rc, 0, out)

    def test_no_ci_run_for_commit_fails_after_grace(self):
        self.gh(self.CI, "")
        rc, out, _, _ = self.run_step(SHA=SHA, POLL_SECONDS="0", CI_WAIT_SECONDS="60", NO_RUN_GRACE="0")
        self.assertNotEqual(rc, 0)
        self.assertIn("CI 실행이 없습니다", out)

    def test_timeout_while_ci_running_fails(self):
        self.gh(self.CI, "11 in_progress -")
        rc, out, _, _ = self.run_step(SHA=SHA, POLL_SECONDS="0", CI_WAIT_SECONDS="0", NO_RUN_GRACE="60")
        self.assertNotEqual(rc, 0)
        self.assertIn("다시 실행하세요", out)


class DeployPlanRequestTests(StepRunner):
    """deploy.yml plan: 요청 정하기 (정상 태그, 자동 배포 꺼짐, 더 높은 태그, 태그 이동, 수동 동작 유지)."""

    workflow, job, step = "deploy.yml", "plan", "요청 정하기 (동작·대상 검증)"

    def tag_event(self, tag="v0.1.1", **env):
        base = {"EVENT": "workflow_run", "RUN_SHA": SHA, "RUN_TAG": tag, "AUTO": "true"}
        return self.run_step(**{**base, **env})

    def test_normal_tag_requests_image_pipeline(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.1.1", f"commit {SHA}")
        self.gh("repos/yeeun5364/lemit/git/matching-refs/tags/v", "v0.1.0\nv0.1.1\nvnot-a-version")
        rc, out, o, _ = self.tag_event()
        self.assertEqual(rc, 0, out)
        # 태그 배포는 deploy-image: 1~5 작업이 돌고, deploy 작업이 release의 digest를 붙인다 (서버 빌드 없음)
        self.assertEqual((o["action"], o["sha"], o["tag"]), ("deploy-image", SHA, "v0.1.1"))
        self.assertEqual(o["remote"], f"deploy-image v0.1.1 {SHA}")

    def test_auto_deploy_off_skips_without_api_calls(self):
        for auto in ("", "false", "TRUE"):
            with self.subTest(auto=auto):
                rc, out, o, calls = self.tag_event(AUTO=auto)
                self.assertEqual(rc, 0, out)
                self.assertEqual((o["action"], o["remote"]), ("skip", ""))
                self.assertIn("자동 배포가 꺼져", out)
                self.assertEqual(calls, [])

    def test_older_tag_is_skipped_by_version_order(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.9.0", f"commit {SHA}")
        self.gh("repos/yeeun5364/lemit/git/matching-refs/tags/v", "v0.9.0\nv0.10.0")  # 문자열 순서로는 v0.9.0이 뒤
        rc, out, o, _ = self.tag_event(tag="v0.9.0")
        self.assertEqual(rc, 0, out)
        self.assertEqual((o["action"], o["remote"]), ("skip", ""))
        self.assertIn("v0.10.0", out)

    def test_tag_moved_fails(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.1.1", f"commit {OTHER}")
        rc, out, o, _ = self.tag_event()
        self.assertNotEqual(rc, 0)
        self.assertIn("가리키지 않습니다", out)
        self.assertNotIn("remote", o)

    def test_deleted_tag_fails(self):
        self.gh("repos/yeeun5364/lemit/git/ref/tags/v0.1.1", "__FAIL__")
        rc, out, o, _ = self.tag_event()
        self.assertNotEqual(rc, 0)
        self.assertIn("가리키지 않습니다 (지금: 없음)", out)  # API 실패도 이유를 남기고 멈춤
        self.assertNotIn("remote", o)

    def test_non_version_tag_fails(self):
        rc, out, o, _ = self.tag_event(tag="v1-test")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("remote", o)

    def test_manual_actions_unchanged(self):
        cases = [
            ({"IN_ACTION": "deploy", "IN_SHA": SHA}, ("deploy", f"deploy {SHA}")),
            ({"IN_ACTION": "check"}, ("check", "status")),
            ({"IN_ACTION": "rollback", "IN_TAG": "0123456789ab"}, ("rollback", "rollback 0123456789ab")),
        ]
        for env, (action, remote) in cases:
            with self.subTest(**env):
                rc, out, o, _ = self.run_step(EVENT="workflow_dispatch", AUTO="", **env)
                self.assertEqual(rc, 0, out)
                self.assertEqual((o["action"], o["remote"], o["tag"]), (action, remote, ""))
        # sha를 비우면 main 최신 커밋 (자동 배포가 꺼져 있어도 수동은 됨)
        self.gh("repos/yeeun5364/lemit/commits/main", SHA)
        rc, out, o, _ = self.run_step(EVENT="workflow_dispatch", AUTO="false", IN_ACTION="deploy", IN_SHA="")
        self.assertEqual((rc, o["remote"]), (0, f"deploy {SHA}"), out)


class DeployPlanCiTests(StepRunner):
    """deploy.yml plan: CI 실행·main 포함 확인 (CI 미통과, main 밖 커밋)."""

    workflow, job, step = "deploy.yml", "plan", "CI 실행·main 포함 확인"
    RUNS = "repos/yeeun5364/lemit/actions/workflows/ci.yml/runs"

    def ci_routes(self, compare="ahead", run_id="321", event="push", jobs=CI_JOBS):
        self.gh(f"repos/yeeun5364/lemit/compare/{SHA}...main", compare)
        self.gh(self.RUNS, run_id)
        self.gh(
            "repos/yeeun5364/lemit/actions/runs/321/jobs",
            jobs,
        )
        self.gh(
            "repos/yeeun5364/lemit/actions/runs/321",
            f".github/workflows/ci.yml\t{event}\tmain\t{SHA}\tsuccess\t{REPO}",
        )

    def test_ci_passed_on_main(self):
        self.ci_routes()
        rc, out, _, _ = self.run_step(SHA=SHA)
        self.assertEqual(rc, 0, out)
        self.assertIn("3개 작업 모두 성공", out)

    def test_commit_outside_main_fails(self):
        self.ci_routes(compare="diverged")
        rc, out, _, _ = self.run_step(SHA=SHA)
        self.assertNotEqual(rc, 0)
        self.assertIn("main에 포함된 커밋이 아닙니다", out)

    def test_no_successful_ci_run_fails(self):
        self.ci_routes(run_id="")
        rc, out, _, _ = self.run_step(SHA=SHA)
        self.assertNotEqual(rc, 0)
        self.assertIn("성공한 CI 실행", out)

    def test_failed_ci_job_fails(self):
        self.ci_routes(jobs=CI_JOBS.replace("테스트 (PostgreSQL 18)\tsuccess", "테스트 (PostgreSQL 18)\tfailure"))
        rc, out, _, _ = self.run_step(SHA=SHA)
        self.assertNotEqual(rc, 0)
        self.assertIn("성공하지 않았습니다", out)

    def test_run_from_other_event_fails(self):
        self.ci_routes(event="pull_request")
        rc, out, _, _ = self.run_step(SHA=SHA)
        self.assertNotEqual(rc, 0)


class RunnerFreshnessTests(StepRunner):
    """deploy.yml deploy: 러너가 늦게 켜졌을 때 태그가 옮겨졌거나 더 높은 태그가 있으면 건너뜀."""

    workflow, job, step = "deploy.yml", "deploy", "태그가 그대로이고 최신 버전인지 확인 (태그 배포만)"

    def check(self, ls_remote, tag="v0.1.1"):
        self.ls_remote(ls_remote)
        rc, out, o, _ = self.run_step(EVENT="workflow_run", SHA=SHA, VERSION_TAG=tag)
        self.assertEqual(rc, 0, out)
        return o["skip"], out

    def test_tag_unchanged_and_newest(self):
        skip, out = self.check(f"{'c' * 40}\trefs/tags/v0.1.0\n{SHA}\trefs/tags/v0.1.1")
        self.assertEqual(skip, "false", out)

    def test_annotated_tag_uses_peeled_commit(self):
        skip, out = self.check(f"{'d' * 40}\trefs/tags/v0.1.1\n{SHA}\trefs/tags/v0.1.1^{{}}")
        self.assertEqual(skip, "false", out)

    def test_tag_moved_skips(self):
        skip, out = self.check(f"{OTHER}\trefs/tags/v0.1.1")
        self.assertEqual(skip, "true")
        self.assertIn("가리키지 않아", out)

    def test_newer_tag_skips(self):
        skip, out = self.check(f"{SHA}\trefs/tags/v0.1.1\n{OTHER}\trefs/tags/v0.2.0")
        self.assertEqual(skip, "true")
        self.assertIn("v0.2.0", out)

    def test_manual_deploy_is_not_checked(self):
        rc, out, o, calls = self.run_step(EVENT="workflow_dispatch", SHA=SHA, VERSION_TAG="")
        self.assertEqual((rc, o["skip"], calls), (0, "false", []), out)


DIGEST = "sha256:" + "d" * 64
OTHER_DIGEST = "sha256:" + "e" * 64
IMAGE_REPO = "ghcr.io/yeeun5364/lemit-web"


class DeployRemoteTests(StepRunner):
    """deploy.yml deploy: 서버 요청 만들기 (태그 배포는 release의 digest를 붙임, 수동은 그대로)."""

    workflow, job, step = "deploy.yml", "deploy", "서버 요청 만들기"

    def make(self, **env):
        rc, out, o, _ = self.run_step(**env)
        return rc, out, o.get("remote")

    def tag(self, **over):
        env = {
            "ACTION": "deploy-image",
            "PLAN_REMOTE": f"deploy-image v0.1.1 {SHA}",
            "VERSION_TAG": "v0.1.1",
            "SHA": SHA,
            "IMAGE_DIGEST": DIGEST,
        }
        return self.make(**{**env, **over})

    def test_tag_deploy_appends_release_digest(self):
        rc, out, remote = self.tag()
        self.assertEqual(rc, 0, out)
        self.assertEqual(remote, f"deploy-image v0.1.1 {SHA} {DIGEST}")

    def test_bad_or_missing_digest_fails(self):
        for digest in ("", "sha256:abc", DIGEST.upper(), f"{DIGEST} status"):
            with self.subTest(digest=digest):
                rc, out, remote = self.tag(IMAGE_DIGEST=digest)
                self.assertNotEqual(rc, 0)
                self.assertIsNone(remote)

    def test_plan_request_must_match_target(self):
        rc, out, remote = self.tag(PLAN_REMOTE=f"deploy-image v0.1.1 {OTHER}")
        self.assertNotEqual(rc, 0)
        self.assertIn("plan 요청과 대상이 다릅니다", out)
        self.assertIsNone(remote)

    def test_manual_requests_unchanged(self):
        for action, remote in (("deploy", f"deploy {SHA}"), ("check", "status"), ("rollback", "rollback 0123456789ab")):
            with self.subTest(action=action):
                rc, out, got = self.make(ACTION=action, PLAN_REMOTE=remote, IMAGE_DIGEST="")
                self.assertEqual((rc, got), (0, remote), out)


class ReleaseRecordTests(StepRunner):
    """deploy.yml release: Release 만들기 또는 기존 기록과 비교 (재시도 중복 없음, digest가 다르면 덮어쓰지 않음)."""

    workflow, job, step = "deploy.yml", "release", "Release 만들기 또는 기존 기록과 비교"
    API = "repos/yeeun5364/lemit/releases/tags/v0.1.2"

    def env(self, **over):
        base = {
            "TAG": "v0.1.2",
            "TARGET_SHA": SHA,
            "MODEL_VERSION": "v2+v0.1.2",
            "MODEL_SHA": "f" * 64,
            "VAL_F1": "0.9357",
            "UNSEEN_F1": "0.9833",
            "DIGEST": DIGEST,
            "IMAGE_REPO": IMAGE_REPO,
            "RUN_URL": "https://github.com/yeeun5364/lemit/actions/runs/1",
        }
        return {**base, **over}

    def body(self, commit=SHA, digest=DIGEST, model_sha="f" * 64):
        lines = [
            "## v0.1.2",
            "",
            "| 항목 | 값 |",
            "<!-- lemit-release",
            "tag=v0.1.2",
            f"commit={commit}",
            "model_version=v2+v0.1.2",
            f"model_sha256={model_sha}",
            "val_macro_f1=0.9357",
            "unseen_macro_f1=0.9833",
            f"image={IMAGE_REPO}@{digest}",
            "-->",
        ]
        return "\r\n".join(lines)  # GitHub가 돌려주는 본문은 CRLF일 수 있다

    def creates(self, calls):
        return [c for c in calls if c.startswith("release ")]

    def test_new_release_is_created_with_record(self):
        self.gh(self.API, "__FAIL__")
        rc, out, o, calls = self.run_step(**self.env())
        self.assertEqual(rc, 0, out)
        self.assertEqual((o["state"], o["digest"]), ("created", DIGEST))
        self.assertEqual(len(self.creates(calls)), 1)
        self.assertIn("--verify-tag", self.creates(calls)[0])
        notes = (self.dir / "work" / "notes.md").read_text(encoding="utf-8")
        for text in (
            "tag=v0.1.2",
            f"commit={SHA}",
            "model_version=v2+v0.1.2",
            "model_sha256=" + "f" * 64,
            "val_macro_f1=0.9357",
            f"image={IMAGE_REPO}@{DIGEST}",
        ):
            self.assertIn(text, notes)

    def test_retry_with_same_image_reuses_release(self):
        self.gh(self.API, self.body())
        rc, out, o, calls = self.run_step(**self.env())
        self.assertEqual(rc, 0, out)
        self.assertEqual((o["state"], o["digest"]), ("existing", DIGEST))
        self.assertEqual(self.creates(calls), [])  # 중복 Release를 만들지 않음

    def test_different_digest_is_reported_not_overwritten(self):
        self.gh(self.API, self.body(digest=OTHER_DIGEST))
        rc, out, o, calls = self.run_step(**self.env())
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.creates(calls), [])
        self.assertNotIn("digest", o)
        summary = (self.dir / "summary").read_text(encoding="utf-8")
        self.assertIn("덮어쓰지 않음", summary)
        self.assertIn(f"| image | `{IMAGE_REPO}@{OTHER_DIGEST}` | `{IMAGE_REPO}@{DIGEST}` |", summary)

    def test_different_commit_is_reported(self):
        self.gh(self.API, self.body(commit=OTHER))
        rc, out, _, calls = self.run_step(**self.env())
        self.assertNotEqual(rc, 0)
        self.assertIn("| commit |", (self.dir / "summary").read_text(encoding="utf-8"))
        self.assertEqual(self.creates(calls), [])

    def test_release_without_pipeline_record_is_not_touched(self):
        self.gh(self.API, "손으로 쓴 릴리즈 노트")
        rc, out, _, calls = self.run_step(**self.env())
        self.assertNotEqual(rc, 0)
        self.assertIn("파이프라인 기록(lemit-release)이 없습니다", out)
        self.assertEqual(self.creates(calls), [])

    def test_api_error_other_than_404_fails(self):
        self.gh(self.API, "__ERROR__")
        rc, out, _, calls = self.run_step(**self.env())
        self.assertNotEqual(rc, 0)
        self.assertIn("Release를 확인하지 못했습니다", out)
        self.assertEqual(self.creates(calls), [])

    def test_bad_inputs_fail_before_api(self):
        for over in (
            {"DIGEST": ""},
            {"DIGEST": "sha256:xyz"},
            {"TAG": "v1.2"},
            {"TARGET_SHA": "main"},
            {"MODEL_SHA": ""},
        ):
            with self.subTest(**over):
                rc, out, _, calls = self.run_step(**self.env(**over))
                self.assertNotEqual(rc, 0)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
