"""API 키 인증·권한·공통 오류 형식 (#102)."""

import datetime
import json
import logging

from django.core.cache import caches
from django.test import TestCase
from django.utils import timezone
from rest_framework.response import Response

from accounts.models import User
from api import keys, scopes
from api.models import ApiKey
from api.views import ApiView
from students.models import Student

ME = "/api/v1/me/"


def make_key(user, scope_list=(scopes.READ,), days=90, **extra):
    raw, prefix, digest = keys.generate()
    api_key = ApiKey.objects.create(
        user=user,
        name="테스트 키",
        prefix=prefix,
        key_hash=digest,
        scopes=list(scope_list),
        expires_at=timezone.now() + datetime.timedelta(days=days),
        **extra,
    )
    return raw, api_key


class ApiTestCase(TestCase):
    def setUp(self):
        caches["api_throttle"].clear()
        self.teacher = User.objects.create_user(email="t@example.com", password="pw-2026-lemit!", name="김강사")

    def get(self, path, raw=None, **headers):
        if raw is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {raw}"
        return self.client.get(path, **headers)

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        error = response.json()["error"]
        self.assertEqual(error["code"], code)
        for field in ("message", "hint", "fields", "retryable", "docs", "request_id"):
            self.assertIn(field, error)
        self.assertTrue(error["docs"].endswith(f"/api/docs/errors/#{code}"))
        self.assertEqual(error["request_id"], response["X-Request-ID"])
        return error


class MeTests(ApiTestCase):
    def test_valid_key_returns_owner_and_scopes(self):
        raw, api_key = make_key(self.teacher, [scopes.READ, scopes.SESSIONS_WRITE])
        response = self.get(ME, raw)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["teacher"], {"name": "김강사"})
        self.assertEqual(body["api_key"]["prefix"], api_key.prefix)
        self.assertEqual(body["api_key"]["scopes"], ["read", "sessions:write"])
        self.assertEqual(body["api_key"]["scopes_display"], ["읽기", "일정 변경"])
        self.assertTrue(body["api_key"]["expires_at"].endswith("+09:00"))  # 서울 시간 명시
        self.assertFalse(body["api_key"]["expires_soon"])
        self.assertTrue(response["X-Request-ID"].startswith("req_"))

    def test_key_without_read_scope_can_still_check_connection(self):
        raw, _ = make_key(self.teacher, [scopes.MESSAGES_SEND])
        self.assertEqual(self.get(ME, raw).status_code, 200)

    def test_last_used_updated_at_most_once_a_minute(self):
        raw, api_key = make_key(self.teacher)
        self.get(ME, raw)
        api_key.refresh_from_db()
        first = api_key.last_used_at
        self.assertIsNotNone(first)
        self.get(ME, raw)
        api_key.refresh_from_db()
        self.assertEqual(api_key.last_used_at, first)
        ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=first - datetime.timedelta(minutes=2))
        self.get(ME, raw)
        api_key.refresh_from_db()
        self.assertGreater(api_key.last_used_at, first - datetime.timedelta(minutes=2))

    def test_session_login_is_not_api_authentication(self):
        self.client.force_login(self.teacher)
        self.assertError(self.get(ME), 401, "not_authenticated")

    def test_no_cors_headers(self):
        raw, _ = make_key(self.teacher)
        response = self.get(ME, raw, HTTP_ORIGIN="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", response)


class AuthFailureTests(ApiTestCase):
    def test_missing_header(self):
        response = self.get(ME)
        error = self.assertError(response, 401, "not_authenticated")
        self.assertIn("Authorization: Bearer", error["hint"])
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="api"')

    def test_malformed_header(self):
        for value in ["lemit_abc", "Basic abc", "Bearer a b", "Bearer"]:
            with self.subTest(value=value):
                response = self.client.get(ME, HTTP_AUTHORIZATION=value)
                self.assertError(response, 401, "invalid_api_key")

    def test_unknown_key(self):
        self.assertError(self.get(ME, "lemit_" + "x" * 43), 401, "invalid_api_key")

    def test_revoked_key_is_same_as_unknown(self):
        raw, _ = make_key(self.teacher, revoked_at=timezone.now())
        unknown = self.assertError(self.get(ME, "lemit_" + "y" * 43), 401, "invalid_api_key")
        revoked = self.assertError(self.get(ME, raw), 401, "invalid_api_key")
        self.assertEqual(revoked["message"], unknown["message"])  # 폐기 여부를 드러내지 않음

    def test_inactive_account(self):
        raw, _ = make_key(self.teacher)
        User.objects.filter(pk=self.teacher.pk).update(is_active=False)
        self.assertError(self.get(ME, raw), 401, "invalid_api_key")

    def test_expired_key_tells_date(self):
        raw, api_key = make_key(self.teacher)
        expired_at = timezone.now() - datetime.timedelta(days=1)
        ApiKey.objects.filter(pk=api_key.pk).update(created_at=expired_at - datetime.timedelta(days=90))
        ApiKey.objects.filter(pk=api_key.pk).update(expires_at=expired_at)
        error = self.assertError(self.get(ME, raw), 401, "api_key_expired")
        self.assertIn(f"{timezone.localtime(expired_at).date():%Y-%m-%d}", error["message"])

    def test_student_account_rejected(self):
        account = User.objects.create_user(email="s@example.com", password="pw-2026-lemit!", name="김하늘")
        Student.objects.create(teacher=self.teacher, name="김하늘", account=account)
        raw, _ = make_key(account)
        self.assertError(self.get(ME, raw), 403, "account_not_allowed")

    def test_repeated_failures_block_ip(self):
        for _ in range(20):
            self.get(ME, "lemit_" + "z" * 43)
        raw, _ = make_key(self.teacher)
        response = self.get(ME, raw)  # 막힌 동안에는 올바른 키도 거절
        error = self.assertError(response, 429, "rate_limited")
        self.assertTrue(error["retryable"])
        self.assertGreater(error["retry_after"], 0)
        self.assertEqual(response["Retry-After"], str(error["retry_after"]))
        # 다른 IP는 영향 없음
        self.assertEqual(self.get(ME, raw, REMOTE_ADDR="10.0.0.2").status_code, 200)


class ScopeView(ApiView):
    required_scope = scopes.SESSIONS_WRITE

    def get(self, request):
        return Response({"ok": True})


class BrokenView(ApiView):
    def get(self, request):
        raise RuntimeError("내부 경로 /srv/secret 노출 금지")


class ScopeAndServerErrorTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.factory_raw, _ = make_key(self.teacher, [scopes.READ])

    def call(self, view_class, raw):
        from rest_framework.test import APIRequestFactory

        request = APIRequestFactory().get("/api/v1/test/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        request.request_id = "req_test"
        response = view_class.as_view()(request)
        response.render()
        return response

    def test_insufficient_scope(self):
        response = self.call(ScopeView, self.factory_raw)
        self.assertEqual(response.status_code, 403)
        error = json.loads(response.content)["error"]
        self.assertEqual(error["code"], "insufficient_scope")
        self.assertIn("일정 변경", error["message"])
        self.assertIn("읽기", error["message"])

    def test_scope_granted(self):
        raw, _ = make_key(self.teacher, [scopes.SESSIONS_WRITE])
        self.assertEqual(self.call(ScopeView, raw).status_code, 200)

    def test_unexpected_error_is_json_500_without_internals(self):
        with self.assertLogs("api", level="ERROR") as logs:
            response = self.call(BrokenView, self.factory_raw)
        self.assertEqual(response.status_code, 500)
        error = json.loads(response.content)["error"]
        self.assertEqual(error["code"], "server_error")
        self.assertEqual(error["request_id"], "req_test")
        self.assertNotIn("/srv/secret", response.content.decode())
        self.assertIn("req_test", "\n".join(logs.output))  # 원인은 request_id와 함께 로그에만


class CommonErrorFormatTests(ApiTestCase):
    def test_unknown_api_path_is_json_404(self):
        self.assertError(self.client.get("/api/v1/nothing/"), 404, "not_found")

    def test_web_404_stays_html(self):
        self.client.force_login(self.teacher)
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("application/json", response["Content-Type"])

    def test_method_not_allowed(self):
        raw, _ = make_key(self.teacher)
        response = self.client.post(ME, {}, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertError(response, 405, "method_not_allowed")
        self.assertIn("GET", response["Allow"])

    def test_student_session_does_not_redirect_api(self):
        account = User.objects.create_user(email="s2@example.com", password="pw-2026-lemit!", name="학생")
        Student.objects.create(teacher=self.teacher, name="학생", account=account)
        self.client.force_login(account)
        self.assertError(self.get(ME), 401, "not_authenticated")  # 수강생 화면으로 보내지 않음


class RateLimitTests(ApiTestCase):
    def test_per_key_limit(self):
        from api.throttling import ApiKeyRateThrottle

        ApiKeyRateThrottle.THROTTLE_RATES = {"api_key": "3/min"}
        try:
            raw, _ = make_key(self.teacher)
            other_raw, _ = make_key(self.teacher)
            for _ in range(3):
                self.assertEqual(self.get(ME, raw).status_code, 200)
            response = self.get(ME, raw)
            error = self.assertError(response, 429, "rate_limited")
            self.assertTrue(error["retryable"])
            self.assertIn(f"{error['retry_after']}초", error["message"])
            self.assertEqual(self.get(ME, other_raw).status_code, 200)  # 키마다 따로 센다
        finally:
            from rest_framework.settings import api_settings

            ApiKeyRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES

    def test_counts_are_in_shared_db_cache(self):
        from api.throttling import ApiKeyRateThrottle

        self.assertEqual(type(ApiKeyRateThrottle.cache).__name__, "DatabaseCache")


class LoggingTests(ApiTestCase):
    def test_access_log_has_no_secrets(self):
        raw, api_key = make_key(self.teacher)
        with self.assertLogs("api", level="INFO") as logs:
            response = self.get(ME + "?q=private", raw)
        output = "\n".join(logs.output)
        self.assertIn(response["X-Request-ID"], output)
        self.assertIn(api_key.prefix, output)
        self.assertIn(f"user={self.teacher.pk}", output)
        self.assertNotIn(raw, output)
        self.assertNotIn(raw[len(api_key.prefix) :], output)
        self.assertNotIn("Bearer", output)
        self.assertNotIn("private", output)  # 쿼리 문자열 제외

    def test_failed_auth_log_has_no_key(self):
        bad = "lemit_" + "q" * 43
        with self.assertLogs("api", level="INFO") as logs:
            self.get(ME, bad)
        self.assertNotIn(bad, "\n".join(logs.output))


class DocsTests(TestCase):
    def test_docs_are_public(self):
        logging.disable(logging.CRITICAL)
        try:
            self.assertEqual(self.client.get("/api/docs/").status_code, 200)
            schema = self.client.get("/api/schema/")
            self.assertEqual(schema.status_code, 200)
            self.assertIn(b"/api/v1/me/", schema.content)
            errors = self.client.get("/api/docs/errors/")
            self.assertContains(errors, 'id="insufficient_scope"')
        finally:
            logging.disable(logging.NOTSET)
