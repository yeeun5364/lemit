"""로그인·비밀번호 재설정 시도 횟수 제한 테스트 (#7)."""

import datetime
from unittest import mock

from django.contrib.auth import SESSION_KEY, get_user_model
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import throttle
from accounts.models import LoginAttempt

User = get_user_model()
PASSWORD = "Lemit-pass-9876"


class LoginThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="teacher@example.com", password=PASSWORD, name="강사")

    def login(self, email="teacher@example.com", password="wrong-pass", ip="203.0.113.10"):
        return self.client.post(reverse("login"), {"username": email, "password": password}, REMOTE_ADDR=ip)

    def test_blocks_after_five_failures_even_with_right_password(self):
        for _ in range(throttle.PER_EMAIL):
            r = self.login()
            self.assertContains(r, "이메일 또는 비밀번호가 올바르지 않습니다.")
        r = self.login(password=PASSWORD)
        self.assertContains(r, throttle.BLOCKED_MESSAGE)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_unknown_email_is_blocked_the_same_way(self):
        for _ in range(throttle.PER_EMAIL):
            self.login(email="nobody@example.com")
        self.assertContains(self.login(email="nobody@example.com"), throttle.BLOCKED_MESSAGE)

    def test_email_is_case_insensitive_and_other_accounts_unaffected(self):
        for _ in range(throttle.PER_EMAIL):
            self.login(email="Teacher@Example.com")
        self.assertContains(self.login(email="teacher@example.com", password=PASSWORD), throttle.BLOCKED_MESSAGE)
        User.objects.create_user(email="other@example.com", password=PASSWORD, name="다른 강사")
        r = self.login(email="other@example.com", password=PASSWORD, ip="198.51.100.7")
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_success_clears_email_failures(self):
        for _ in range(throttle.PER_EMAIL - 1):
            self.login()
        self.assertRedirects(self.login(password=PASSWORD), reverse("home"), fetch_redirect_response=False)
        self.client.logout()
        self.assertFalse(LoginAttempt.objects.filter(kind="login", email="teacher@example.com").exists())
        for _ in range(throttle.PER_EMAIL - 1):
            self.login()
        self.assertRedirects(self.login(password=PASSWORD), reverse("home"), fetch_redirect_response=False)

    def test_block_expires_after_window(self):
        for _ in range(throttle.PER_EMAIL):
            self.login()
        later = timezone.now() + throttle.WINDOW + datetime.timedelta(seconds=1)
        with mock.patch("django.utils.timezone.now", return_value=later):
            r = self.login(password=PASSWORD)
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_per_ip_limit_across_many_emails(self):
        for i in range(throttle.PER_IP):
            self.login(email=f"spray{i}@example.com", ip="203.0.113.99")
        r = self.login(password=PASSWORD, ip="203.0.113.99")
        self.assertContains(r, throttle.BLOCKED_MESSAGE)
        r = self.login(password=PASSWORD, ip="198.51.100.1")  # 다른 IP의 정상 사용자는 들어감
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_old_records_are_purged(self):
        old = LoginAttempt.objects.create(kind="login", email="old@example.com", ip="203.0.113.1")
        LoginAttempt.objects.filter(pk=old.pk).update(created_at=timezone.now() - throttle.RETENTION * 2)
        self.login()
        self.assertFalse(LoginAttempt.objects.filter(pk=old.pk).exists())


class ClientIpTests(TestCase):
    def request(self, **meta):
        return RequestFactory().get("/", **meta)

    def test_forwarded_header_ignored_without_proxy(self):
        r = self.request(REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="1.2.3.4")
        self.assertEqual(throttle.client_ip(r), "10.0.0.5")  # 위조 가능한 헤더를 믿지 않음

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_behind_proxy_uses_last_forwarded_address(self):
        r = self.request(REMOTE_ADDR="172.18.0.3", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.10")
        self.assertEqual(throttle.client_ip(r), "203.0.113.10")  # Caddy가 덧붙인 실제 접속 주소
        bad = self.request(REMOTE_ADDR="172.18.0.3", HTTP_X_FORWARDED_FOR="not-an-ip")
        self.assertEqual(throttle.client_ip(bad), "172.18.0.3")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ResetThrottleTests(TestCase):
    def setUp(self):
        User.objects.create_user(email="teacher@example.com", password=PASSWORD, name="강사")

    def test_too_many_reset_requests_stop_sending_but_look_the_same(self):
        responses = [
            self.client.post(reverse("password_reset"), {"email": "teacher@example.com"}, REMOTE_ADDR="203.0.113.10")
            for _ in range(throttle.PER_EMAIL + 2)
        ]
        self.assertEqual(len(mail.outbox), throttle.PER_EMAIL)
        self.assertEqual({(r.status_code, r["Location"]) for r in responses}, {(302, reverse("password_reset_done"))})
