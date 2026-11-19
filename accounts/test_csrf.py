"""CSRF 보호 실검증 (PR #36 리뷰 후속 2).

Django 테스트 클라이언트는 기본으로 CSRF 검사를 하지 않으므로 enforce_csrf_checks=True로 브라우저와 같은 조건을 만든다.
화면에서 받은 토큰으로 보내면 성공하고, 토큰 없이 보내면 403이어야 한다.
"""

import re

from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from students.models import Student

User = get_user_model()
PASSWORD = "lesson-time-2026!"


def form_token(response):
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode())
    assert match, "폼에 CSRF 토큰이 없습니다"
    return match.group(1)


class CsrfTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.teacher = User.objects.create_user(email="t@example.com", password=PASSWORD, name="김강사")

    def test_signup(self):
        data = {"email": "new@example.com", "name": "새강사", "password1": PASSWORD, "password2": PASSWORD}
        self.assertEqual(self.client.post(reverse("signup"), data).status_code, 403)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())
        token = form_token(self.client.get(reverse("signup")))
        res = self.client.post(reverse("signup"), {**data, "csrfmiddlewaretoken": token})
        self.assertRedirects(res, reverse("home"))

    def test_login(self):
        data = {"username": "t@example.com", "password": PASSWORD}
        self.assertEqual(self.client.post(reverse("login"), data).status_code, 403)
        self.assertNotIn(SESSION_KEY, self.client.session)
        token = form_token(self.client.get(reverse("login")))
        self.assertRedirects(
            self.client.post(reverse("login"), {**data, "csrfmiddlewaretoken": token}), reverse("home")
        )

    def test_logout(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 403)
        self.assertIn(SESSION_KEY, self.client.session)  # 토큰 없는 요청으로는 로그아웃되지 않음
        token = form_token(self.client.get(reverse("home")))  # 상단 로그아웃 폼의 토큰
        self.assertRedirects(self.client.post(reverse("logout"), {"csrfmiddlewaretoken": token}), reverse("login"))

    def test_student_create_and_archive(self):
        self.client.force_login(self.teacher)
        data = {"name": "김하늘", "phone": "", "guardian_phone": "", "guardian_name": "", "memo": ""}
        self.assertEqual(self.client.post(reverse("students:create"), data).status_code, 403)
        self.assertFalse(Student.objects.exists())
        token = form_token(self.client.get(reverse("students:create")))
        self.client.post(reverse("students:create"), {**data, "csrfmiddlewaretoken": token})
        student = Student.objects.get(name="김하늘")
        self.assertEqual(self.client.post(reverse("students:archive", args=[student.pk])).status_code, 403)
        student.refresh_from_db()
        self.assertTrue(student.is_active)
