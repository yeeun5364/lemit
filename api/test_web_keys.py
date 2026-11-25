"""웹 화면 '설정 > API 키' 발급·연장·폐기 (#102)."""

import datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import LoginAttempt, User
from api import keys, scopes
from api.forms import PasswordConfirmForm
from api.models import ApiKey
from api.test_auth import make_key
from students.models import Student

PASSWORD = "pw-2026-lemit!"


class KeyWebTestCase(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(email="t@example.com", password=PASSWORD, name="김강사")
        self.other = User.objects.create_user(email="o@example.com", password=PASSWORD, name="최강사")
        self.client.force_login(self.teacher)

    def create(self, **data):
        payload = {"name": "에이전트", "scopes": [scopes.READ], "expires_in": 90, "password": PASSWORD} | data
        return self.client.post(reverse("api_keys:create"), payload)


class CreateTests(KeyWebTestCase):
    def test_create_shows_raw_key_once_and_stores_only_hash(self):
        response = self.create(scopes=[scopes.MESSAGES_SEND, scopes.READ])
        self.assertEqual(response.status_code, 200)
        raw = response.context["raw_key"]
        self.assertTrue(raw.startswith("lemit_"))
        self.assertContains(response, raw)
        self.assertEqual(response["Cache-Control"], "no-store")

        api_key = ApiKey.objects.get(user=self.teacher)
        self.assertEqual(api_key.key_hash, keys.hash_key(raw))
        self.assertEqual(api_key.prefix, raw[:10])
        self.assertEqual(api_key.scopes, ["read", "messages:send"])  # 정해진 순서로 저장
        self.assertAlmostEqual(
            (api_key.expires_at - timezone.now()).total_seconds(), datetime.timedelta(days=90).total_seconds(), delta=60
        )
        for field in ApiKey._meta.concrete_fields:
            self.assertNotEqual(getattr(api_key, field.attname), raw)

        # 목록에는 원문이 다시 나오지 않는다
        listing = self.client.get(reverse("api_keys:list"))
        self.assertNotContains(listing, raw)
        self.assertContains(listing, api_key.prefix)

        # 발급한 키로 바로 API를 쓸 수 있다
        self.client.logout()
        me = self.client.get("/api/v1/me/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(me.status_code, 200)

    def test_wrong_password_rejected(self):
        response = self.create(password="wrong-password")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "password", "비밀번호가 맞지 않습니다.")
        self.assertFalse(ApiKey.objects.exists())

    def test_scope_and_expiry_required_and_validated(self):
        response = self.create(scopes=[], expires_in=7)
        form = response.context["form"]
        self.assertFormError(form, "scopes", "범위를 하나 이상 고르세요.")
        self.assertFormError(form, "expires_in", "기간을 다시 고르세요.")
        self.assertFalse(ApiKey.objects.exists())

    def test_limit_of_five_usable_keys(self):
        for _ in range(ApiKey.MAX_USABLE_PER_USER):
            make_key(self.teacher)
        make_key(self.teacher, revoked_at=timezone.now())  # 폐기된 키는 세지 않음
        response = self.create()
        self.assertContains(response, "5개까지")
        self.assertEqual(ApiKey.objects.filter(user=self.teacher).usable().count(), 5)
        self.assertTrue(self.client.get(reverse("api_keys:create")).context["limit_reached"])

    def test_student_account_cannot_open_key_pages(self):
        account = User.objects.create_user(email="s@example.com", password=PASSWORD, name="김하늘")
        Student.objects.create(teacher=self.teacher, name="김하늘", account=account)
        self.client.force_login(account)
        self.assertRedirects(self.client.get(reverse("api_keys:list")), reverse("portal:lessons"))
        self.assertEqual(self.create(password=PASSWORD).status_code, 403)
        self.assertFalse(ApiKey.objects.filter(user=account).exists())

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse("api_keys:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class ExtendRevokeTests(KeyWebTestCase):
    def test_extend_keeps_same_key_value(self):
        raw, api_key = make_key(self.teacher, days=10)
        response = self.client.post(
            reverse("api_keys:extend", args=[api_key.pk]), {"expires_in": 365, "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("api_keys:list"))
        api_key.refresh_from_db()
        self.assertGreater(api_key.expires_at, timezone.now() + datetime.timedelta(days=364))
        self.assertEqual(api_key.key_hash, keys.hash_key(raw))

    def test_extend_requires_password(self):
        _, api_key = make_key(self.teacher, days=10)
        before = api_key.expires_at
        self.client.post(reverse("api_keys:extend", args=[api_key.pk]), {"expires_in": 365, "password": "nope"})
        api_key.refresh_from_db()
        self.assertEqual(api_key.expires_at, before)

    def test_expired_or_revoked_key_cannot_be_extended(self):
        _, revoked = make_key(self.teacher, revoked_at=timezone.now())
        response = self.client.post(
            reverse("api_keys:extend", args=[revoked.pk]), {"expires_in": 90, "password": PASSWORD}, follow=True
        )
        self.assertContains(response, "연장할 수 없어요")
        revoked.refresh_from_db()
        self.assertIsNotNone(revoked.revoked_at)

    def test_revoke_blocks_api_immediately(self):
        raw, api_key = make_key(self.teacher)
        self.client.post(reverse("api_keys:revoke", args=[api_key.pk]))
        api_key.refresh_from_db()
        self.assertIsNotNone(api_key.revoked_at)
        response = self.client.get("/api/v1/me/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(response.json()["error"]["code"], "invalid_api_key")

    def test_other_teachers_key_is_404(self):
        _, others = make_key(self.other)
        for name in ("api_keys:extend", "api_keys:revoke"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[others.pk])).status_code, 404)
                self.assertEqual(
                    self.client.post(
                        reverse(name, args=[others.pk]), {"expires_in": 90, "password": PASSWORD}
                    ).status_code,
                    404,
                )
        others.refresh_from_db()
        self.assertIsNone(others.revoked_at)

    def test_list_shows_only_my_keys_and_soon_badge(self):
        make_key(self.teacher, days=5)
        _, others = make_key(self.other)
        response = self.client.get(reverse("api_keys:list"))
        self.assertContains(response, "곧 만료")
        self.assertNotContains(response, others.prefix)

    def test_dashboard_warns_about_expiring_keys(self):
        make_key(self.teacher, days=5)
        self.assertContains(self.client.get(reverse("home")), "14일 안에 만료되는 API 키가 1개")
        ApiKey.objects.all().delete()
        make_key(self.teacher, days=60)
        self.assertNotContains(self.client.get(reverse("home")), "만료되는 API 키")


class ExtendRaceTests(KeyWebTestCase):
    """연장 중 만료 경계·동시 발급 (#102 리뷰 P1)."""

    def extend(self, api_key, password=PASSWORD):
        return self.client.post(
            reverse("api_keys:extend", args=[api_key.pk]), {"expires_in": 90, "password": password}, follow=True
        )

    def test_key_expiring_during_password_check_is_not_extended(self):
        _, api_key = make_key(self.teacher, days=1)

        def expire_then_accept(user, raw_password):
            ApiKey.objects.filter(pk=api_key.pk).update(
                created_at=timezone.now() - datetime.timedelta(days=2),
                expires_at=timezone.now() - datetime.timedelta(seconds=1),
            )
            return True

        with mock.patch.object(User, "check_password", expire_then_accept):
            response = self.extend(api_key)
        self.assertContains(response, "연장할 수 없어요")
        api_key.refresh_from_db()
        self.assertLess(api_key.expires_at, timezone.now())  # 되살아나지 않음

    def test_extend_cannot_exceed_limit_with_concurrent_issue(self):
        # 유효 4개 + 곧 만료될 키 A. A의 연장 비밀번호 확인 사이에 A가 만료되고, 그 틈에 다른 요청이 5번째 키를 발급
        for _ in range(4):
            make_key(self.teacher)
        _, expiring = make_key(self.teacher, days=1)

        def race(user, raw_password):
            ApiKey.objects.filter(pk=expiring.pk).update(
                created_at=timezone.now() - datetime.timedelta(days=2),
                expires_at=timezone.now() - datetime.timedelta(seconds=1),
            )
            make_key(self.teacher)  # 만료 직후라 개수 제한(5개)을 통과한 발급
            return True

        with mock.patch.object(User, "check_password", race):
            self.extend(expiring)
        self.assertEqual(ApiKey.objects.filter(user=self.teacher).usable().count(), 5)

    def test_create_and_extend_lock_teacher_row(self):
        _, api_key = make_key(self.teacher, days=10)
        for url, data in [
            (reverse("api_keys:extend", args=[api_key.pk]), {"expires_in": 90, "password": PASSWORD}),
            (
                reverse("api_keys:create"),
                {"name": "a", "scopes": [scopes.READ], "expires_in": 90, "password": PASSWORD},
            ),
        ]:
            with self.subTest(url=url), CaptureQueriesContext(connection) as queries:
                self.client.post(url, data)
            locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
            self.assertTrue(any('"accounts_user"' in sql for sql in locks), locks)


class PasswordAttemptLimitTests(KeyWebTestCase):
    """비밀번호 재확인 실패 제한: 발급·연장이 횟수를 함께 쓴다 (#102 리뷰 P1)."""

    def setUp(self):
        super().setUp()
        _, self.api_key = make_key(self.teacher, days=10)

    def extend(self, password):
        return self.client.post(
            reverse("api_keys:extend", args=[self.api_key.pk]), {"expires_in": 90, "password": password}
        )

    def test_failures_shared_between_create_and_extend_then_blocked(self):
        for _ in range(3):
            self.create(password="wrong")
        for _ in range(2):
            self.extend("wrong")
        response = self.create(password=PASSWORD)  # 맞는 비밀번호도 막힌 동안은 거절
        self.assertFormError(response.context["form"], "password", PasswordConfirmForm.BLOCKED_MESSAGE)
        self.assertEqual(ApiKey.objects.filter(user=self.teacher).count(), 1)
        before = self.api_key.expires_at
        self.assertContains(self.extend(PASSWORD), "시도가 너무 많습니다")
        self.api_key.refresh_from_db()
        self.assertEqual(self.api_key.expires_at, before)

    def test_block_lifts_after_window(self):
        for _ in range(5):
            self.create(password="wrong")
        LoginAttempt.objects.update(created_at=timezone.now() - datetime.timedelta(minutes=16))
        self.assertEqual(self.create(password=PASSWORD).status_code, 200)
        self.assertEqual(ApiKey.objects.filter(user=self.teacher).count(), 2)

    def test_success_resets_account_count_and_is_separate_from_login(self):
        for _ in range(4):
            self.create(password="wrong")
        self.create(password=PASSWORD)
        for _ in range(4):
            self.create(password="wrong")
        self.assertIn("raw_key", self.create(password=PASSWORD).context)
        kinds = set(LoginAttempt.objects.values_list("kind", flat=True))
        self.assertEqual(kinds, {LoginAttempt.Kind.API_KEY})  # 로그인 실패 집계와 섞이지 않음
