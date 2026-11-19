"""UC-01 회원가입·로그인·로그아웃과 접근 정책 테스트."""

from django.contrib.auth import SESSION_KEY, get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

User = get_user_model()
PASSWORD = "lesson-time-2026!"

# 로그인 없이 열리는 화면 (SCREENS §1 접근 정책). 여기 없는 화면은 모두 로그인 필수여야 한다.
PUBLIC_URL_NAMES = {"login", "signup", "healthz"}


def make_teacher(email="teacher@example.com", name="김강사", **extra):
    return User.objects.create_user(email=email, password=PASSWORD, name=name, **extra)


class SignupTests(TestCase):
    url = reverse("signup")

    def post(self, **overrides):
        data = {"email": "new@example.com", "name": "이강사", "password1": PASSWORD, "password2": PASSWORD}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_page_is_public(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_signup_creates_teacher_logs_in_and_redirects_home(self):
        res = self.post()
        self.assertRedirects(res, reverse("home"))
        user = User.objects.get(email="new@example.com")
        self.assertEqual(user.name, "이강사")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)  # Django 기본 해시로 저장
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertFalse(user.is_staff)
        self.assertEqual(int(self.client.session[SESSION_KEY]), user.pk)  # 가입 후 자동 로그인

    def test_duplicate_email_is_rejected_case_insensitively(self):
        make_teacher(email="Same@Example.com")
        res = self.post(email="same@example.com")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "이미 가입된 이메일입니다.")
        self.assertEqual(User.objects.filter(email__iexact="same@example.com").count(), 1)

    def test_password_mismatch_is_rejected(self):
        res = self.post(password2="different-pass-2026!")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_weak_password_is_rejected(self):
        res = self.post(password1="12345678", password2="12345678")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_name_is_required(self):
        res = self.post(name="")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_logged_in_teacher_is_sent_home(self):
        self.client.force_login(make_teacher())
        self.assertRedirects(self.client.get(self.url), reverse("home"))


class LoginTests(TestCase):
    url = reverse("login")

    def setUp(self):
        self.teacher = make_teacher()

    def test_page_is_public(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "이메일")

    def test_login_redirects_home(self):
        res = self.client.post(self.url, {"username": "teacher@example.com", "password": PASSWORD})
        self.assertRedirects(res, reverse("home"))
        self.assertEqual(int(self.client.session[SESSION_KEY]), self.teacher.pk)

    def test_email_is_case_insensitive(self):
        res = self.client.post(self.url, {"username": "TEACHER@Example.COM", "password": PASSWORD})
        self.assertRedirects(res, reverse("home"))

    def test_wrong_password_and_unknown_email_show_same_message(self):
        """가입 여부를 드러내지 않도록 두 경우 메시지가 같다."""
        for email, password in (("teacher@example.com", "wrong-pass"), ("nobody@example.com", PASSWORD)):
            with self.subTest(email=email):
                res = self.client.post(self.url, {"username": email, "password": password})
                self.assertEqual(res.status_code, 200)
                self.assertContains(res, "이메일 또는 비밀번호가 올바르지 않습니다.")
                self.assertNotIn(SESSION_KEY, self.client.session)

    def test_inactive_teacher_cannot_log_in(self):
        self.teacher.is_active = False
        self.teacher.save()
        res = self.client.post(self.url, {"username": "teacher@example.com", "password": PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_next_parameter_returns_to_original_page(self):
        res = self.client.get(reverse("home"))
        self.assertRedirects(res, f"{self.url}?next=/", fetch_redirect_response=False)
        res = self.client.post(self.url, {"username": "teacher@example.com", "password": PASSWORD, "next": "/"})
        self.assertRedirects(res, "/")

    def test_external_next_is_ignored(self):
        """로그인 후 외부 사이트로 보내는 공격(open redirect)을 막는다."""
        res = self.client.post(
            self.url, {"username": "teacher@example.com", "password": PASSWORD, "next": "https://evil.example/"}
        )
        self.assertRedirects(res, reverse("home"))

    def test_logged_in_teacher_is_sent_home(self):
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.get(self.url), reverse("home"))


class LogoutTests(TestCase):
    def setUp(self):
        self.client.force_login(make_teacher())

    def test_post_logs_out_and_redirects_to_login(self):
        res = self.client.post(reverse("logout"))
        self.assertRedirects(res, reverse("login"))
        self.assertNotIn(SESSION_KEY, self.client.session)
        # 로그아웃 후에는 서비스 화면에 다시 들어갈 수 없다
        self.assertRedirects(self.client.get(reverse("home")), f"{reverse('login')}?next=/")

    def test_get_is_not_allowed(self):
        """링크(GET)로 로그아웃되지 않게 POST만 허용 (CSRF 보호)."""
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertIn(SESSION_KEY, self.client.session)


def _iter_patterns(patterns, prefix="", namespace=None):
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = p.namespace or namespace
            yield from _iter_patterns(p.url_patterns, prefix + str(p.pattern), ns)
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p.name, namespace


class AccessPolicyTests(TestCase):
    """기본 거부: 새 화면을 추가할 때 로그인 검사를 빠뜨려도 막히는지 모든 URL로 확인한다."""

    def test_every_non_public_url_requires_login(self):
        checked = []
        for route, name, namespace in _iter_patterns(get_resolver().url_patterns):
            if namespace == "admin" or "<" in route or route.startswith("^"):
                continue  # 관리자는 별도 정책, 경로 변수가 있는 화면은 각 앱 테스트에서 확인
            url = "/" + route
            res = self.client.get(url)
            if name in PUBLIC_URL_NAMES:
                self.assertNotEqual(res.status_code, 302, f"{url} 공개 화면인데 로그인으로 보냄")
            else:
                self.assertEqual(res.status_code, 302, f"{url} 로그인 없이 열림")
                self.assertTrue(res["Location"].startswith(reverse("login")), f"{url} → {res['Location']}")
            checked.append(url)
        self.assertIn("/", checked)
        self.assertIn("/accounts/logout/", checked)

    def test_post_actions_also_require_login(self):
        res = self.client.post(reverse("logout"))
        self.assertTrue(res["Location"].startswith(reverse("login")))

    def test_admin_requires_staff(self):
        res = self.client.get("/admin/")
        self.assertEqual(res.status_code, 302)
        self.client.force_login(make_teacher())  # 일반 강사는 관리자 화면에 들어갈 수 없다
        res = self.client.get("/admin/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login/", res["Location"])

    def test_home_shows_teacher_name_and_logout_form(self):
        self.client.force_login(make_teacher(name="박강사"))
        res = self.client.get(reverse("home"))
        self.assertContains(res, "박강사")
        self.assertContains(res, f'action="{reverse("logout")}"')


class EmailConstraintTests(TestCase):
    def test_db_rejects_same_email_with_different_case(self):
        make_teacher(email="Case@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_teacher(email="case@example.com")
