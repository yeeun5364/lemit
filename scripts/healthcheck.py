"""컨테이너 헬스체크 (Dockerfile HEALTHCHECK, compose). 표준 라이브러리만 쓴다 (이미지에 curl 없음).

/healthz/가 200이고 status가 "ok"(앱 + DB 정상)면 종료 코드 0.
메시지 분류 모델 상태는 컨테이너 생존 조건에 넣지 않는다. 모델이 없어도 일정 관리는 동작해야 하기 때문이다.
모델 상태는 배포 스모크 테스트(scripts/smoke_test.py)에서 따로 확인한다.

Host 헤더는 DJANGO_ALLOWED_HOSTS의 첫 값을 쓴다 (운영 도메인만 허용해도 헬스체크가 400이 나지 않게).
"""

import json
import os
import sys
import urllib.request

PORT = os.environ.get("PORT", "8000")


def host_header():
    explicit = os.environ.get("HEALTHCHECK_HOST")
    if explicit:
        return explicit
    hosts = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
    host = hosts[0] if hosts else "localhost"
    return host.lstrip(".")  # ".example.com" 같은 와일드카드 표기 처리


def main():
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/healthz/", headers={"Host": host_header()})
    try:
        with urllib.request.urlopen(req, timeout=4) as res:
            body = json.loads(res.read().decode())
    except Exception as e:  # 연결 실패·503·잘못된 JSON 모두 unhealthy
        print(f"unhealthy: {type(e).__name__}: {e}")
        return 1
    if body.get("status") != "ok":
        print(f"unhealthy: {body}")
        return 1
    print(f"healthy: db={body.get('db')} message_classifier={body.get('message_classifier')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
