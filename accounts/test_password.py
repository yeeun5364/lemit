"""UC-15 비밀번호 찾기(S03)·변경(S04) 테스트 (#25)."""

import datetime
import re
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

User = get_user_model()
OLD = "Old-pass-9876"
NEW = "New-pass-5432"
LINK = re.compile(r"https?://[^/\s]+(/accounts/password-reset/[^/\s]+/[^/\s]+/)")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="teacher@example.com", password=OLD, name="강사")

    def request_reset(self, email):
        return self.client.post(reverse("password_reset"), {"email": email})

    def reset_link(self):
        self.assertEqual(len(mail.outbox), 1)
        m = LINK.search(mail.outbox[0].body)
        self.assertIsNotNone(m, mail.outbox[0].body)
        return m.group(1)

    def test_uses_django_reset_and_one_hour_timeout(self):
        from django.contrib.auth import views as auth_views

        from accounts import views

        self.assertTrue(issubclass(views.PasswordResetView, auth_views.PasswordResetView))
        self.assertTrue(issubclass(views.PasswordResetConfirmView, auth_views.PasswordResetConfirmView))
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 3600)

    def test_reset_pages_are_public(self):
        for name in ("password_reset", "password_reset_done", "password_reset_complete"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertContains(self.client.get(reverse("login")), reverse("password_reset"))

    def test_full_reset_flow(self):
        r = self.request_reset("Teacher@Example.com")  # 대소문자 구분 없이
        self.assertRedirects(r, reverse("password_reset_done"))
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["teacher@example.com"])
        self.assertIn("비밀번호 재설정", msg.subject)
        self.assertNotIn("\n", msg.subject)
        link = self.reset_link()

        r = self.client.get(link)  # 토큰을 세션에 옮기고 토큰 없는 주소로 이동
        self.assertEqual(r.status_code, 302)
        form_url = r["Location"]
        self.assertContains(self.client.get(form_url), "새 비밀번호 설정")
        r = self.client.post(form_url, {"new_password1": NEW, "new_password2": NEW})
        self.assertRedirects(r, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))
        self.assertTrue(self.client.login(email="teacher@example.com", password=NEW))

    def test_link_is_single_use(self):
        self.request_reset("teacher@example.com")
        link = self.reset_link()
        form_url = self.client.get(link)["Location"]
        self.client.post(form_url, {"new_password1": NEW, "new_password2": NEW})
        self.client.logout()
        r = self.client.get(link, follow=True)
        self.assertContains(r, "링크가 유효하지 않습니다")
        self.assertNotContains(r, 'name="new_password1"')

    def test_link_expires_after_one_hour(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse("password_reset_confirm", args=[uid, default_token_generator.make_token(self.user)])
        later = default_token_generator._now() + datetime.timedelta(seconds=3601)
        with mock.patch.object(default_token_generator, "_now", return_value=later):
            r = self.client.get(url, follow=True)
        self.assertContains(r, "링크가 유효하지 않습니다")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD))

    def test_unknown_email_looks_the_same(self):
        known = self.request_reset("teacher@example.com")
        unknown = self.request_reset("nobody@example.com")
        self.assertEqual(len(mail.outbox), 1)  # 없는 이메일에는 보내지 않음
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known["Location"], unknown["Location"])
        done = self.client.get(reverse("password_reset_done"))
        self.assertContains(done, "가입된 계정이 있으면")

    def test_send_failure_does_not_reveal_account(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            with self.assertLogs("django.contrib.auth", level="ERROR"):
                r = self.request_reset("teacher@example.com")
        self.assertRedirects(r, reverse("password_reset_done"))

    def test_invalid_token_shows_message(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        r = self.client.get(reverse("password_reset_confirm", args=[uid, "bad-token"]))
        self.assertContains(r, "링크가 유효하지 않습니다")
        self.assertContains(r, reverse("password_reset"))

    def test_logged_in_user_sees_reset_pages_in_app_layout(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("password_reset"))
        self.assertContains(r, "재설정 링크 받기")


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="teacher@example.com", password=OLD, name="강사")

    def test_requires_login(self):
        r = self.client.get(reverse("password_change"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r["Location"])

    def test_change_keeps_session_and_new_password_works(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("home")), reverse("password_change"))  # 사이드바 메뉴
        r = self.client.post(
            reverse("password_change"), {"old_password": OLD, "new_password1": NEW, "new_password2": NEW}, follow=True
        )
        self.assertRedirects(r, reverse("home"))
        self.assertContains(r, "비밀번호를 변경했습니다.")
        self.assertEqual(self.client.get(reverse("students:list")).status_code, 200)  # 로그인 유지
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))

    def test_wrong_current_password_is_rejected(self):
        self.client.force_login(self.user)
        r = self.client.post(
            reverse("password_change"), {"old_password": "wrong", "new_password1": NEW, "new_password2": NEW}
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("old_password", r.context["form"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD))


class EmailSettingsTests(TestCase):
    def test_mail_credentials_come_from_env_only(self):
        example = (settings.BASE_DIR / ".env.example").read_text(encoding="utf-8")
        for line in example.splitlines():
            if line.startswith(("EMAIL_HOST_USER=", "EMAIL_HOST_PASSWORD=")):
                self.assertEqual(line.split("=", 1)[1], "", line)  # 예시에 실제 값 없음
        settings_src = (settings.BASE_DIR / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn('env.str("EMAIL_HOST_PASSWORD"', settings_src)

    def test_backend_depends_on_env(self):
        """SMTP 설정이 있으면 Gmail SMTP(TLS 587), 없으면 개발은 콘솔·운영은 발송 안 함 (PR #68 리뷰 P1)."""
        import importlib
        import os

        import config.settings as conf

        def load(env):
            keys = ("EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD")
            with mock.patch.dict(os.environ, env):
                for k in keys:
                    if k not in env:
                        os.environ.pop(k, None)
                with mock.patch("environ.Env.read_env"):  # 개발 PC의 .env 값에 흔들리지 않게
                    return importlib.reload(conf)

        try:
            dev = load({"DJANGO_DEBUG": "True"})
            self.assertEqual(dev.EMAIL_BACKEND, "django.core.mail.backends.console.EmailBackend")
            prod_no_smtp = load({"DJANGO_DEBUG": "False"})
            self.assertEqual(prod_no_smtp.EMAIL_BACKEND, "accounts.mail.DisabledEmailBackend")
            smtp = {"EMAIL_HOST_USER": "lemit@gmail.com", "EMAIL_HOST_PASSWORD": "app-pass"}
            for debug in ("True", "False"):
                with self.subTest(debug=debug):
                    prod = load({"DJANGO_DEBUG": debug, **smtp})
                    self.assertEqual(prod.EMAIL_BACKEND, "django.core.mail.backends.smtp.EmailBackend")
                    self.assertEqual(
                        (prod.EMAIL_HOST, prod.EMAIL_PORT, prod.EMAIL_USE_TLS), ("smtp.gmail.com", 587, True)
                    )
                    self.assertEqual(prod.DEFAULT_FROM_EMAIL, "lemit@gmail.com")
        finally:
            importlib.reload(conf)


@override_settings(DEBUG=False, EMAIL_BACKEND="accounts.mail.DisabledEmailBackend")
class ProductionWithoutSmtpTests(TestCase):
    """PR #68 리뷰 P1: 운영에서 SMTP가 없으면 재설정 링크가 로그·stdout 어디에도 나오지 않는다."""

    def setUp(self):
        self.user = User.objects.create_user(email="teacher@example.com", password=OLD, name="강사")

    def capture_reset(self, email):
        import io
        import logging
        from contextlib import redirect_stderr, redirect_stdout

        out, err, logs = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                response = self.client.post(reverse("password_reset"), {"email": email})
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        return response, out.getvalue() + err.getvalue() + logs.getvalue()

    def test_reset_link_is_not_printed_or_logged(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        response, captured = self.capture_reset("teacher@example.com")
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertNotIn("/accounts/password-reset/" + uid, captured)
        self.assertNotIn(uid + "/", captured)
        self.assertNotIn("teacher@example.com", captured)  # 받는 사람도 남기지 않음
        self.assertNotIn("비밀번호 재설정 안내", captured)  # 메일 제목·본문도
        self.assertIn("보내지 않았습니다", captured)  # 운영자가 알 수 있는 경고만
        self.assertEqual(len(mail.outbox), 0)

    def test_same_response_for_unknown_email(self):
        known, _ = self.capture_reset("teacher@example.com")
        unknown, captured = self.capture_reset("nobody@example.com")
        self.assertEqual((known.status_code, known["Location"]), (unknown.status_code, unknown["Location"]))
        self.assertNotIn("nobody@example.com", captured)
