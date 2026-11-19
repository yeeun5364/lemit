from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase


class UserModelTests(TestCase):
    def test_create_user_with_email(self):
        user = get_user_model().objects.create_user(
            email="Teacher@Example.com", password="s3cure-pass!", name="김강사"
        )
        self.assertEqual(user.email, "Teacher@example.com")  # 도메인 부분은 소문자로 정규화
        self.assertTrue(user.check_password("s3cure-pass!"))
        self.assertNotEqual(user.password, "s3cure-pass!")  # 해시로 저장
        self.assertFalse(user.is_staff)

    def test_login_with_email(self):
        get_user_model().objects.create_user(email="t@example.com", password="s3cure-pass!", name="김강사")
        self.assertIsNotNone(authenticate(email="t@example.com", password="s3cure-pass!"))
        self.assertIsNone(authenticate(email="t@example.com", password="wrong"))

    def test_email_is_unique(self):
        User = get_user_model()
        User.objects.create_user(email="t@example.com", password="x", name="A")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="t@example.com", password="y", name="B")

    def test_create_superuser(self):
        admin = get_user_model().objects.create_superuser(email="admin@example.com", password="x", name="관리자")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)


class SettingsTests(TestCase):
    def test_uses_postgresql(self):
        self.assertEqual(connection.vendor, "postgresql")

    def test_locale_and_timezone(self):
        self.assertEqual(settings.LANGUAGE_CODE, "ko-kr")
        self.assertEqual(settings.TIME_ZONE, "Asia/Seoul")
        self.assertTrue(settings.USE_TZ)
