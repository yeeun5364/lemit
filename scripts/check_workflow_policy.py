"""Self-hosted 러너 사용 규칙 검사 (#56). CI 린트 작업에서:  python scripts/check_workflow_policy.py

공개 저장소에서 개인 PC의 Self-hosted 러너를 쓰므로, 워크플로가 실수로 바뀌어 PR 코드가 러너에서 돌지 않게 한다.
이 검사는 **저장소 안의 실수**를 막는 장치다. 포크 PR은 자기 워크플로 파일로 실행되므로 이 검사로는 막을 수 없고,
저장소 설정 '모든 외부 기여자 승인'과 러너 격리에 의존한다 (완전한 차단이 아님, docs/CD.md §1).

규칙
1. deploy.yml이 아닌 워크플로
   - 'self-hosted' 글자가 주석 밖에 없어야 한다.
   - 모든 작업의 runs-on은 GitHub-hosted 표준 라벨(HOSTED_LABELS) 문자열이어야 한다 (표현식·그룹·사용자 지정 라벨 금지).
2. deploy.yml 트리거는 workflow_run {workflows: [Release tag], types: [completed]}과 workflow_dispatch만.
   (push·pull_request 금지: push로 실행되면 태그·브랜치 커밋 안의 고쳐진 deploy.yml이 러너에서 돌 수 있다.
   workflow_run은 언제나 main의 deploy.yml로 실행된다.) 워크플로 최상위 concurrency 금지 (작업 단위로만).
2-1. release-tag.yml (태그 푸시를 받는 유일한 워크플로)
   - name: Release tag (deploy.yml의 workflow_run이 이 이름을 가리킴), 트리거는 push: tags: ["v*"] 하나만
   - 권한은 contents: read, actions: read만. 주석 밖에 secrets. 를 쓰지 않음 (배포 키 등 Secret을 받지 않음)
   - 모든 작업 GitHub-hosted (규칙 1)
3. deploy.yml의 self-hosted 작업은 정확히 하나이고
   - environment: production (배포 키 Secret은 main 브랜치에서만 받음)
   - needs의 모든 작업이 GitHub-hosted이고, 그 작업의 if에 저장소·이벤트·ref·자동 배포 스위치 조건이 모두 있음
   - 자기 if에 github.repository 조건
   - 작업 단위 concurrency(cancel-in-progress: false), 그룹은 EXPECTED_GROUP 그대로:
     check·rollback·deploy를 각각 다른 그룹으로 (새 자동 배포가 대기 중인 롤백을 취소하지 못하게, PR #58 리뷰 P2)
   - 검증 작업이 태그 자동 배포(workflow_run)를 서버에 deploy-tag로 요청 (태그·main 포함 재확인, 롤백 뒤 보류를 서버가
     지키게). main push 자동 배포 요청(deploy-auto)은 쓰지 않음 (중복 배포 방지)
   - 액션(uses)을 쓰지 않고 run 단계만 (저장소 코드·외부 액션을 개인 PC에서 실행하지 않음)

한계: 조건 문자열이 **있는지** 확인하며, 조건식 전체의 논리를 증명하지는 않는다 (docs/CD.md §8).
"""

import re
import sys
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
DEPLOY = "deploy.yml"
RELEASE_TAG = "release-tag.yml"
RELEASE_TAG_NAME = "Release tag"
# GitHub-hosted 표준 러너 라벨만 (접두사가 같은 사용자 지정 라벨, 예: ubuntu-personal은 거부)
HOSTED_LABELS = {
    "ubuntu-latest",
    "ubuntu-24.04",
    "ubuntu-22.04",
    "ubuntu-24.04-arm",
    "ubuntu-22.04-arm",
    "windows-latest",
    "windows-2025",
    "windows-2022",
    "windows-11-arm",
    "macos-latest",
    "macos-15",
    "macos-14",
    "macos-13",
}
WORKFLOW_RUN = {"workflows": [RELEASE_TAG_NAME], "types": ["completed"]}
# 검증 작업(GitHub-hosted)의 if에 반드시 있어야 하는 조건 (공백은 무시하고 비교)
GATE_CONDITIONS = [
    "github.repository == 'yeeun5364/lemit'",
    "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'",
    "github.event_name == 'workflow_run'",
    "vars.CD_AUTO_DEPLOY == 'true'",
    "github.event.workflow_run.conclusion == 'success'",
    "github.event.workflow_run.event == 'push'",
    f"github.event.workflow_run.path == '.github/workflows/{RELEASE_TAG}'",
    "github.event.workflow_run.head_repository.full_name == github.repository",
]
# self-hosted 작업의 concurrency group (공백은 무시하고 정확히 비교)
EXPECTED_GROUP = (
    "${{ needs.plan.outputs.action == 'check' && 'deploy-production-check' || "
    "needs.plan.outputs.action == 'rollback' && 'deploy-production-rollback' || 'deploy-production' }}"
)
AUTO_DEPLOY_REQUEST = 'REMOTE="deploy-tag $VERSION_TAG $SHA"'
RELEASE_TAG_TRIGGER = {"push": {"tags": ["v*"]}}
RELEASE_TAG_PERMISSIONS = {"contents": "read", "actions": "read"}


def _squash(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _triggers(doc):
    on = doc.get("on", doc.get(True))  # YAML 1.1에서 on은 True로 읽힌다
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {t: None for t in on}
    return on or {}


def _is_hosted(job):
    runs_on = (job or {}).get("runs-on")
    return isinstance(runs_on, str) and runs_on in HOSTED_LABELS


def _is_self_hosted(job):
    runs_on = (job or {}).get("runs-on")
    if runs_on is None:
        return False
    return not _is_hosted(job)  # GitHub-hosted 문자열이 아니면 모두 self-hosted로 본다 (목록·그룹·표현식)


def check_other(name, text, doc):
    errors = []
    for n, line in enumerate(text.splitlines(), 1):
        if "self-hosted" in line.split("#", 1)[0]:
            errors.append(f"{name}:{n}: self-hosted 러너는 {DEPLOY}에서만 쓸 수 있습니다")
            break
    for key, job in (doc.get("jobs") or {}).items():
        if (job or {}).get("runs-on") is not None and not _is_hosted(job):
            errors.append(f"{name} jobs.{key}: runs-on은 GitHub-hosted 라벨 문자열이어야 합니다 (ubuntu-latest 등)")
    return errors


def check_deploy(doc):
    errors = []
    triggers = _triggers(doc)
    extra = set(triggers) - {"workflow_run", "workflow_dispatch"}
    if extra:
        errors.append(f"{DEPLOY}: 허용하지 않는 트리거 {sorted(extra)} (workflow_run·workflow_dispatch만)")
    if "workflow_run" in triggers and (triggers["workflow_run"] or {}) != WORKFLOW_RUN:
        errors.append(f"{DEPLOY}: workflow_run은 workflows: [{RELEASE_TAG_NAME}], types: [completed]만 허용합니다")

    if "concurrency" in doc:
        errors.append(f"{DEPLOY}: 워크플로 최상위 concurrency는 쓰지 않습니다 (검증을 통과한 작업 단위로만)")
    jobs = doc.get("jobs") or {}
    self_hosted = {k: j for k, j in jobs.items() if _is_self_hosted(j)}
    if len(self_hosted) != 1:
        errors.append(f"{DEPLOY}: self-hosted 작업은 정확히 하나여야 합니다 (지금 {len(self_hosted)}개)")
    for key, job in self_hosted.items():
        where = f"{DEPLOY} jobs.{key}"
        env = job.get("environment")
        if (env.get("name") if isinstance(env, dict) else env) != "production":
            errors.append(f"{where}: environment: production 이어야 합니다")
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else list(needs)
        if not needs:
            errors.append(f"{where}: needs로 GitHub-hosted 검증 작업 뒤에 실행해야 합니다")
        for need in needs:
            gate = jobs.get(need)
            if not _is_hosted(gate):
                errors.append(f"{where}: needs의 {need}는 GitHub-hosted 검증 작업이어야 합니다")
                continue
            cond = _squash(gate.get("if"))
            for c in GATE_CONDITIONS:
                if _squash(c) not in cond:
                    errors.append(f"{DEPLOY} jobs.{need}: if에 조건이 없습니다: {c}")
            runs = "\n".join(str((st or {}).get("run", "")) for st in gate.get("steps") or [])
            if AUTO_DEPLOY_REQUEST not in runs:
                errors.append(
                    f"{DEPLOY} jobs.{need}: 태그 자동 배포는 deploy-tag로 요청해야 합니다 ({AUTO_DEPLOY_REQUEST})"
                )
            if "deploy-auto" in runs:
                errors.append(
                    f"{DEPLOY} jobs.{need}: main push 자동 배포(deploy-auto)는 쓰지 않습니다 (태그 배포와 중복)"
                )
        if "github.repository ==" not in str(job.get("if", "")):
            errors.append(f"{where}: if에 github.repository 조건이 있어야 합니다")
        conc = job.get("concurrency")
        if not isinstance(conc, dict) or not conc.get("group") or conc.get("cancel-in-progress") is not False:
            errors.append(f"{where}: 작업 단위 concurrency(group, cancel-in-progress: false)가 있어야 합니다")
        else:
            if _squash(conc.get("group")) != _squash(EXPECTED_GROUP):
                errors.append(
                    f"{where}: concurrency group은 check·rollback·deploy를 각각 다른 그룹으로 나눠야 합니다: "
                    f"{EXPECTED_GROUP}"
                )
        for i, step in enumerate(job.get("steps") or []):
            if "uses" in (step or {}):
                errors.append(f"{where}.steps[{i}]: self-hosted 작업에서는 액션(uses)을 쓰지 않습니다")
    return errors


def check_release_tag(text, doc):
    errors = []
    if doc.get("name") != RELEASE_TAG_NAME:
        errors.append(
            f"{RELEASE_TAG}: name은 '{RELEASE_TAG_NAME}'이어야 합니다 ({DEPLOY}의 workflow_run이 이 이름을 가리킴)"
        )
    triggers = _triggers(doc)
    if triggers != RELEASE_TAG_TRIGGER:
        errors.append(f'{RELEASE_TAG}: 트리거는 push: tags: ["v*"] 하나만 허용합니다 (지금 {triggers})')
    if doc.get("permissions") != RELEASE_TAG_PERMISSIONS:
        errors.append(f"{RELEASE_TAG}: permissions는 contents: read, actions: read만 허용합니다")
    for key, job in (doc.get("jobs") or {}).items():
        if "permissions" in (job or {}):
            errors.append(f"{RELEASE_TAG} jobs.{key}: 작업 단위 permissions로 권한을 넓히지 않습니다")
        if "environment" in (job or {}):
            errors.append(f"{RELEASE_TAG} jobs.{key}: environment를 쓰지 않습니다 (배포 Secret을 받지 않음)")
    for n, line in enumerate(text.splitlines(), 1):
        if "secrets." in line.split("#", 1)[0]:
            errors.append(f"{RELEASE_TAG}:{n}: Secret을 쓰지 않습니다 (태그 커밋의 파일이 실행될 수 있음)")
            break
    return errors


def check(workflows_dir=WORKFLOWS):
    errors = []
    files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text) or {}
        if path.name == DEPLOY:
            errors += check_deploy(doc)
        else:
            errors += check_other(path.name, text, doc)
            if path.name == RELEASE_TAG:
                errors += check_release_tag(text, doc)
            elif "push" in _triggers(doc) and "tags" in (_triggers(doc).get("push") or {}):
                errors.append(f"{path.name}: 태그 푸시 트리거는 {RELEASE_TAG}에만 둡니다")
    names = {p.name for p in files}
    for required in (DEPLOY, RELEASE_TAG):
        if required not in names:
            errors.append(f"{required}가 없습니다")
    return errors


def main():
    errors = check()
    for e in errors:
        print(f"[workflow-policy] {e}")
    if errors:
        return 1
    print(
        "[workflow-policy] 통과: self-hosted 러너는 deploy.yml의 배포 작업 하나만, PR·push 트리거 없음, "
        "태그는 release-tag.yml(GitHub-hosted, Secret 없음) → workflow_run, 검증 작업 조건 있음"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
