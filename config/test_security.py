"""운영 보안 설정 테스트 (#7). 환경변수로 켜는 설정이 실제로 적용되는지, HTTPS 강제에서도 헬스체크가 동작하는지."""

import importlib.util
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from config import settings as current_settings

BASE_ENV = {"DJANGO_SECRET_KEY": "x" * 60, "DATABASE_URL": "sqlite:///:memory:"}


def load_settings(**env):
    """config/settings.py를 주어진 환경변수로 새 모듈로 읽는다 (현재 Django 설정에는 영향 없음)."""
    spec = importlib.util.spec_from_file_location("settings_under_test", current_settings.__file__)
    module = importlib.util.module_from_spec(spec)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_") and k != "DATABASE_URL"}
    with mock.patch.dict(os.environ, {**clean, **BASE_ENV, **env}, clear=True), mock.patch("environ.Env.read_env"):
        spec.loader.exec_module(module)
    return module


class SecuritySettingsTests(TestCase):
    def test_defaults_are_safe_for_local_http(self):
        s = load_settings()
        self.assertFalse(getattr(s, "SECURE_SSL_REDIRECT", False))
        self.assertFalse(getattr(s, "SESSION_COOKIE_SECURE", False))
        self.assertFalse(hasattr(s, "SECURE_PROXY_SSL_HEADER"))  # 프록시 없이 X-Forwarded-Proto를 믿지 않음
        self.assertFalse(s.DEBUG)
        self.assertEqual(s.X_FRAME_OPTIONS, "DENY")
        self.assertTrue(s.SECURE_CONTENT_TYPE_NOSNIFF)

    def test_https_mode_turns_on_redirect_and_secure_cookies(self):
        s = load_settings(DJANGO_SECURE_HTTPS="True", DJANGO_BEHIND_PROXY="True")
        self.assertTrue(s.SECURE_SSL_REDIRECT)
        self.assertTrue(s.SESSION_COOKIE_SECURE)
        self.assertTrue(s.CSRF_COOKIE_SECURE)
        self.assertEqual(s.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))
        self.assertIn(r"^healthz/$", s.SECURE_REDIRECT_EXEMPT)
        self.assertEqual(s.SECURE_HSTS_SECONDS, 0)  # HSTS는 명시적으로 켤 때만

    def test_hsts_is_opt_in(self):
        self.assertEqual(
            load_settings(DJANGO_SECURE_HTTPS="True", DJANGO_HSTS_SECONDS="3600").SECURE_HSTS_SECONDS, 3600
        )


@override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^healthz/$"])
class HttpsRedirectTests(TestCase):
    """HTTPS 강제 상태에서 컨테이너 내부 HTTP 헬스체크가 리다이렉트로 실패하지 않아야 한다 (PR #39 리뷰)."""

    def test_healthz_is_exempt_from_https_redirect(self):
        res = self.client.get(reverse("healthz"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_other_pages_redirect_to_https(self):
        res = self.client.get(reverse("login"))
        self.assertEqual(res.status_code, 301)
        self.assertTrue(res["Location"].startswith("https://"))

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_request_via_https_proxy_is_not_redirected(self):
        user = get_user_model().objects.create_user(email="t@example.com", password="lesson-time-2026!", name="강사")
        self.client.force_login(user)
        res = self.client.get(reverse("home"), headers={"x-forwarded-proto": "https"})
        self.assertEqual(res.status_code, 200)
