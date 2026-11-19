"""새 web 컨테이너 스모크 테스트 (#5, #7). 컨테이너 안에서 실행한다:

    docker compose exec web python scripts/smoke_test.py

확인 항목 (하나라도 실패하면 종료 코드 1):
1. /healthz/ 응답이 200이고 status=ok, db=ok
2. /healthz/의 message_classifier.status == "ok" (HTTP 200만으로는 모델 상태를 알 수 없음, PR #34·#38 리뷰)
3. manage.py check
4. 적용 안 된 마이그레이션 없음 (migrate --check)
5. python -m ml.verify: 이미지 안의 모델 파일·라이브러리 버전·데이터 일치, 점수 재현
6. 실제 추론 1건: 서비스 코드(inbox.classifier)로 문장을 분류해 분류값·모델 버전 확인 (DB에 쓰지 않음)

웹 화면 동선(로그인 → 수강생 메시지 등록 → 배지)은 배포 후 브라우저로 확인한다 (#7).
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from healthcheck import host_header  # noqa: E402

PORT = os.environ.get("PORT", "8000")
SAMPLE = "선생님 수요일에 학교 행사가 있어서 못 갈 것 같아요. 금요일에 보강 가능할까요?"
results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((True, name, detail or ""))
    except Exception as e:
        results.append((False, name, f"{type(e).__name__}: {e}"))


def healthz():
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/healthz/", headers={"Host": host_header()})
    with urllib.request.urlopen(req, timeout=5) as res:
        assert res.status == 200, f"HTTP {res.status}"
        return json.loads(res.read().decode())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.core.management import call_command

    body = {}

    def health():
        body.update(healthz())
        assert body.get("status") == "ok" and body.get("db") == "ok", body
        return "status=ok db=ok"

    def classifier_status():
        mc = body.get("message_classifier", {})
        assert mc.get("status") == "ok", f"message_classifier={mc}"
        return f"version={mc.get('version')}"

    def migrations():
        call_command("migrate", check=True, plan=False, verbosity=0)
        return "적용 안 된 마이그레이션 없음"

    def ml_verify():
        from ml import verify

        assert verify.main() == 0, "ml.verify 실패"
        return "통과"

    def inference():
        from inbox import classifier

        p = classifier.classify(SAMPLE)
        assert p.model_version == body.get("message_classifier", {}).get("version"), "healthz와 모델 버전이 다름"
        return f"{p.label} ({p.confidence}), model={p.model_version}"

    check("/healthz/ 응답", health)
    check("/healthz/ message_classifier", classifier_status)
    check("manage.py check", lambda: call_command("check", verbosity=0) or "이상 없음")
    check("migrate --check", migrations)
    check("ml.verify", ml_verify)
    check("실제 추론 1건", inference)

    print("\n=== 스모크 테스트 결과 ===")
    for ok, name, detail in results:
        print(f"[{'통과' if ok else '실패'}] {name}: {detail}")
    failed = [r for r in results if not r[0]]
    print(f"=== {'모두 통과' if not failed else f'{len(failed)}개 실패'} ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
