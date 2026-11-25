"""/api/ 요청의 request_id와 접근 로그 (#102).

- 서버가 만든 request_id(`req_` + 무작위 12자리)를 응답 헤더 X-Request-ID와 오류 본문에 넣는다.
  클라이언트가 보낸 X-Request-ID는 믿지 않는다 (로그 위조 방지).
- 접근 로그에는 request_id·키 앞부분·강사 번호·메서드·경로(쿼리 제외)·상태 코드·처리 시간만 남긴다.
  요청 본문·키 원문·Authorization 헤더는 남기지 않는다.
"""

import logging
import secrets
import time

logger = logging.getLogger("api")

API_PREFIX = "/api/"


def new_request_id():
    return "req_" + secrets.token_hex(6)


class ApiRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(API_PREFIX):
            return self.get_response(request)

        request.request_id = new_request_id()
        started = time.monotonic()
        response = self.get_response(request)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        response["X-Request-ID"] = request.request_id

        auth = getattr(request, "auth", None)
        logger.info(
            "api request_id=%s key=%s user=%s method=%s path=%s status=%s ms=%s",
            request.request_id,
            getattr(auth, "prefix", "-"),
            getattr(auth, "user_id", "-"),
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
        )
        return response
