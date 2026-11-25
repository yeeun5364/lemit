"""API 오류 로그에서 민감한 값을 빼는 정책 (#102 리뷰 P2).

예외 메시지(str(exc))에는 요청에서 온 값이 그대로 섞일 수 있다.
예: DB 제약 위반의 "Failing row contains (…, 김하늘, …)", 입력값을 담은 ValueError.
그래서 API 오류 로그에는 다음만 남긴다.

- 예외 종류 이름 (원인 예외가 이어져 있으면 그 종류까지)
- 코드 위치: 파일·줄·함수와 그 줄의 소스 코드 (소스 코드는 요청 값이 아니다)

예외 메시지·지역 변수·요청 본문·헤더는 남기지 않는다. 원인을 좁힐 때는 request_id로 시각과 위치를 찾고
같은 입력을 개발 환경에서 재현한다.

이 모듈은 로깅 설정(settings.LOGGING)이 앱 준비 전에 불러오므로 Django 모델을 import하지 않는다.
"""

import logging
import traceback

API_PREFIX = "/api/"
MAX_CHAIN = 5


def _chain(exc):
    """exc와, 이어진 원인 예외(__cause__ 또는 처리 중 발생한 __context__)를 바깥부터 차례로."""
    seen = []
    while exc is not None and exc not in seen and len(seen) < MAX_CHAIN:
        seen.append(exc)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return seen


def describe_exception(exc):
    """예외 메시지 없이 종류와 코드 위치만 담은 여러 줄 문자열."""
    lines = []
    for index, item in enumerate(_chain(exc)):
        kind = f"{type(item).__module__}.{type(item).__qualname__}"
        lines.append(("예외: " if index == 0 else "원인: ") + kind + " (메시지는 기록하지 않음)")
        frames = traceback.extract_tb(item.__traceback__)
        lines.extend(line.rstrip("\n") for line in frames.format())
    return "\n".join(lines)


class RedactApiExceptionFilter(logging.Filter):
    """django.request가 /api/ 요청의 처리되지 않은 예외를 기록할 때 traceback의 예외 메시지를 뺀다.

    DRF 안의 예외는 api.exceptions가 직접 기록하므로, 이 필터는 미들웨어처럼 DRF 밖에서 난 예외용이다.
    웹 화면 요청의 로그는 바꾸지 않는다.
    """

    def filter(self, record):
        request = getattr(record, "request", None)
        path = getattr(request, "path", "") or ""
        if record.exc_info and record.exc_info[1] is not None and path.startswith(API_PREFIX):
            record.msg = f"{record.getMessage()}\n{describe_exception(record.exc_info[1])}"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True
