"""운영 로그 테스트 (#44): 500 오류 상세가 표준 출력에 남고, 요청 본문·쿠키 같은 비밀은 남지 않는다."""

import io
import logging
from contextlib import contextmanager

from django.contrib.auth.decorators import login_not_required
from django.test import Client, TestCase, override_settings
from django.urls import path


@login_not_required
def boom(request):
    raise RuntimeError("테스트용 의도적 오류")


urlpatterns = [path("boom/", boom)]


@contextmanager
def captured(logger_name):
    """로거에 달린 stdout 핸들러의 출력을 잠시 메모리로 받는다 (실제 설정된 핸들러를 그대로 검사)."""
    handlers = [h for h in logging.getLogger(logger_name).handlers if isinstance(h, logging.StreamHandler)]
    assert handlers, f"{logger_name}에 출력 핸들러가 없습니다 (settings.LOGGING 확인)"
    buf = io.StringIO()
    old = [h.setStream(buf) for h in handlers]
    try:
        yield buf
    finally:
        for h, stream in zip(handlers, old, strict=True):
            h.setStream(stream)


@override_settings(ROOT_URLCONF="config.test_logging", DEBUG=False)
class ServerErrorLoggingTests(TestCase):
    def test_500_traceback_is_written_to_stdout(self):
        client = Client(raise_request_exception=False)
        with captured("django.request") as out:
            res = client.post(
                "/boom/",
                {"password": "hunter2-secret", "body": "학생 연락처 010-1234-5678"},
                HTTP_COOKIE="sessionid=topsecretsession",
            )
        self.assertEqual(res.status_code, 500)
        log = out.getvalue()
        self.assertIn("Internal Server Error: /boom/", log)
        self.assertIn("Traceback", log)
        self.assertIn("RuntimeError: 테스트용 의도적 오류", log)
        # 요청 본문·쿠키는 로그에 남지 않는다
        for secret in ("hunter2-secret", "010-1234-5678", "topsecretsession"):
            self.assertNotIn(secret, log)

    def test_stdout_handler_is_configured_for_django_request(self):
        logger = logging.getLogger("django.request")
        self.assertFalse(logger.propagate)
        self.assertLessEqual(logger.getEffectiveLevel(), logging.ERROR)


class AppLoggingTests(TestCase):
    def test_inbox_logs_go_to_stdout(self):
        with captured("inbox") as out:
            logging.getLogger("inbox.classifier").warning("메시지 분류 모델을 쓸 수 없음 (테스트)")
        self.assertIn("WARNING inbox.classifier: 메시지 분류 모델을 쓸 수 없음 (테스트)", out.getvalue())
