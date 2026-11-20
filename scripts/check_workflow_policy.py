"""Self-hosted 러너 사용 규칙 검사 (#56). CI 린트 작업에서:  python scripts/check_workflow_policy.py

공개 저장소에서 개인 PC의 Self-hosted 러너를 쓰므로, 워크플로가 실수로 바뀌어 PR 코드가 러너에서 돌지 않게 한다.
(악의적인 포크 PR은 자기 워크플로 파일을 바꿀 수 있으므로, 그 경우는 저장소 설정 '외부 기여자 승인'이 막는다.
 이 검사는 저장소 안의 실수를 막는 장치다. docs/CD.md §보안)

규칙
1. self-hosted 러너는 deploy.yml만 쓴다. 다른 워크플로에는 'self-hosted' 글자 자체가 없어야 한다.
2. deploy.yml의 트리거는 workflow_run(CI, main 브랜치)과 workflow_dispatch만. pull_request·push 등은 금지.
3. deploy.yml에서 self-hosted 작업은
   - environment: production (배포 키 Secret은 main 브랜치에서만 받음)
   - needs로 GitHub-hosted 검증 작업 뒤에 실행
   - if에 github.repository 조건 (포크에서 실행 안 함)
   - 액션(uses)을 쓰지 않고 run 단계만 (저장소 코드·외부 액션을 개인 PC에서 실행하지 않음)
"""

import sys
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
DEPLOY = "deploy.yml"
ALLOWED_TRIGGERS = {"workflow_run", "workflow_dispatch"}


def _labels(runs_on):
    """runs-on 값(문자열·목록·{group, labels})에서 라벨 문자열을 모두 꺼낸다."""
    if isinstance(runs_on, str):
        return [runs_on]
    if isinstance(runs_on, list):
        return [str(x) for x in runs_on]
    if isinstance(runs_on, dict):
        out = []
        for v in runs_on.values():
            out.extend(_labels(v))
        return out
    return [] if runs_on is None else [str(runs_on)]


def _triggers(doc):
    on = doc.get("on", doc.get(True))  # YAML 1.1에서 on은 True로 읽힌다
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {t: None for t in on}
    return on or {}


def _is_self_hosted(job):
    labels = _labels(job.get("runs-on"))
    return any("self-hosted" in label for label in labels) or isinstance(job.get("runs-on"), dict)


def check_text(name, text):
    """deploy.yml이 아닌 워크플로: 주석을 뺀 본문에 self-hosted가 있으면 안 된다 (표현식으로 넣는 경우까지)."""
    if name == DEPLOY:
        return []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if "self-hosted" in code:
            return [f"{name}:{n}: self-hosted 러너는 {DEPLOY}에서만 쓸 수 있습니다"]
    return []


def check_deploy(doc):
    errors = []
    triggers = _triggers(doc)
    extra = set(triggers) - ALLOWED_TRIGGERS
    if extra:
        errors.append(f"{DEPLOY}: 허용하지 않는 트리거 {sorted(extra)} (workflow_run·workflow_dispatch만)")
    run = triggers.get("workflow_run")
    if "workflow_run" in triggers:
        run = run or {}
        if run.get("workflows") != ["CI"] or run.get("branches") != ["main"]:
            errors.append(f"{DEPLOY}: workflow_run은 workflows: [CI], branches: [main]이어야 합니다")
    jobs = doc.get("jobs") or {}
    self_hosted = {k: j for k, j in jobs.items() if _is_self_hosted(j or {})}
    if not self_hosted:
        errors.append(f"{DEPLOY}: self-hosted 작업이 없습니다")
    for key, job in self_hosted.items():
        where = f"{DEPLOY} jobs.{key}"
        env = job.get("environment")
        env_name = env.get("name") if isinstance(env, dict) else env
        if env_name != "production":
            errors.append(f"{where}: environment: production 이어야 합니다")
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else list(needs)
        if not needs or any(_is_self_hosted(jobs.get(n) or {}) for n in needs):
            errors.append(f"{where}: needs로 GitHub-hosted 검증 작업 뒤에 실행해야 합니다")
        if "github.repository ==" not in str(job.get("if", "")):
            errors.append(f"{where}: if에 github.repository 조건이 있어야 합니다")
        for i, step in enumerate(job.get("steps") or []):
            if "uses" in (step or {}):
                errors.append(f"{where}.steps[{i}]: self-hosted 작업에서는 액션(uses)을 쓰지 않습니다")
    return errors


def check(workflows_dir=WORKFLOWS):
    errors = []
    files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        errors += check_text(path.name, text)
        if path.name == DEPLOY:
            errors += check_deploy(yaml.safe_load(text) or {})
        else:
            doc = yaml.safe_load(text) or {}
            for key, job in (doc.get("jobs") or {}).items():
                if _is_self_hosted(job or {}):
                    errors.append(f"{path.name} jobs.{key}: self-hosted 러너는 {DEPLOY}에서만 쓸 수 있습니다")
    if not any(p.name == DEPLOY for p in files):
        errors.append(f"{DEPLOY}가 없습니다")
    return errors


def main():
    errors = check()
    for e in errors:
        print(f"[workflow-policy] {e}")
    if errors:
        return 1
    print("[workflow-policy] 통과: self-hosted 러너는 deploy.yml의 배포 작업만, PR 트리거 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
