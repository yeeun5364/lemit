"""UC-02 수강생 등록·수정·조회·보관과 강사 A/B 데이터 격리 테스트."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from students.forms import StudentForm
from students.models import Student

User = get_user_model()


def make_teacher(email, name):
    return User.objects.create_user(email=email, password="lesson-time-2026!", name=name)


VALID = {
    "name": "김하늘",
    "phone": "010-1234-5678",
    "is_minor": "",
    "guardian_phone": "",
    "guardian_name": "",
    "memo": "",
}


class StudentModelTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")

    def test_minor_without_guardian_phone_is_rejected_by_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Student.objects.create(teacher=self.teacher, name="미성년", is_minor=True)

    def test_adult_without_guardian_is_ok(self):
        s = Student.objects.create(teacher=self.teacher, name="성인")
        self.assertTrue(s.is_active)


class StudentFormTests(TestCase):
    def test_minor_requires_guardian_phone(self):
        form = StudentForm({**VALID, "is_minor": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("guardian_phone", form.errors)

    def test_minor_with_guardian_phone_is_valid(self):
        self.assertTrue(StudentForm({**VALID, "is_minor": "on", "guardian_phone": "010-9999-0000"}).is_valid())

    def test_guardian_info_is_dropped_for_adults(self):
        """개인정보 최소 수집: 미성년자가 아니면 보호자 정보를 저장하지 않는다."""
        form = StudentForm({**VALID, "guardian_phone": "010-9999-0000", "guardian_name": "보호자"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["guardian_phone"], "")
        self.assertEqual(form.cleaned_data["guardian_name"], "")

    def test_name_is_required_and_trimmed(self):
        self.assertIn("name", StudentForm({**VALID, "name": "   "}).errors)
        form = StudentForm({**VALID, "name": "  김하늘  "})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["name"], "김하늘")

    def test_invalid_phone_is_rejected(self):
        self.assertIn("phone", StudentForm({**VALID, "phone": "전화번호"}).errors)

    def test_teacher_is_not_a_form_field(self):
        """강사는 폼으로 받지 않는다 (조작된 POST로 다른 강사에게 등록 불가)."""
        self.assertNotIn("teacher", StudentForm().fields)


class StudentViewTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.client.force_login(self.teacher)

    def test_create_assigns_logged_in_teacher_and_redirects_to_detail(self):
        res = self.client.post(reverse("students:create"), VALID)
        student = Student.objects.get(name="김하늘")
        self.assertRedirects(res, reverse("students:detail", args=[student.pk]))
        self.assertEqual(student.teacher, self.teacher)

    def test_create_minor_without_guardian_shows_error(self):
        res = self.client.post(reverse("students:create"), {**VALID, "is_minor": "on"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "미성년자는 보호자 연락처가 필요합니다.")
        self.assertFalse(Student.objects.exists())

    def test_update(self):
        s = Student.objects.create(teacher=self.teacher, name="김하늘")
        res = self.client.post(reverse("students:edit", args=[s.pk]), {**VALID, "name": "김하늘2", "memo": "피아노"})
        self.assertRedirects(res, reverse("students:detail", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual((s.name, s.memo), ("김하늘2", "피아노"))

    def test_list_search_and_archive_filter(self):
        Student.objects.create(teacher=self.teacher, name="김하늘")
        Student.objects.create(teacher=self.teacher, name="이도윤")
        Student.objects.create(teacher=self.teacher, name="박보관", is_active=False)
        url = reverse("students:list")
        res = self.client.get(url)
        self.assertContains(res, "김하늘")
        self.assertContains(res, "이도윤")
        self.assertNotContains(res, "박보관")  # 보관한 수강생은 기본으로 숨김
        self.assertContains(self.client.get(url, {"archived": "1"}), "박보관")
        res = self.client.get(url, {"q": "하늘"})
        self.assertContains(res, "김하늘")
        self.assertNotContains(res, "이도윤")

    def test_archive_and_restore(self):
        s = Student.objects.create(teacher=self.teacher, name="김하늘")
        self.assertRedirects(
            self.client.post(reverse("students:archive", args=[s.pk])), reverse("students:detail", args=[s.pk])
        )
        s.refresh_from_db()
        self.assertFalse(s.is_active)
        self.client.post(reverse("students:restore", args=[s.pk]))
        s.refresh_from_db()
        self.assertTrue(s.is_active)

    def test_archive_requires_post(self):
        s = Student.objects.create(teacher=self.teacher, name="김하늘")
        self.assertEqual(self.client.get(reverse("students:archive", args=[s.pk])).status_code, 405)
        s.refresh_from_db()
        self.assertTrue(s.is_active)

    def test_names_are_html_escaped(self):
        Student.objects.create(teacher=self.teacher, name="<script>x</script>")
        res = self.client.get(reverse("students:list"))
        self.assertNotContains(res, "<script>x</script>")
        self.assertContains(res, "&lt;script&gt;x&lt;/script&gt;")


class TeacherIsolationTests(TestCase):
    """강사 A/B 데이터 격리 (ERD 2-5, PR #36 리뷰 후속 1). 실제 Student 모델과 URL로 확인한다."""

    def setUp(self):
        self.a = make_teacher("a@example.com", "A강사")
        self.b = make_teacher("b@example.com", "B강사")
        self.a_student = Student.objects.create(teacher=self.a, name="에이학생")
        self.b_student = Student.objects.create(teacher=self.b, name="비학생", memo="B의 메모")
        self.b_archived = Student.objects.create(teacher=self.b, name="비보관학생", is_active=False)
        self.client.force_login(self.a)

    def test_list_shows_only_own_students(self):
        for params in ({}, {"archived": "1"}, {"q": "비"}):
            with self.subTest(params=params):
                res = self.client.get(reverse("students:list"), params)
                self.assertNotContains(res, "비학생")
                self.assertNotContains(res, "비보관학생")
        self.assertContains(self.client.get(reverse("students:list")), "에이학생")

    def test_other_teachers_student_pages_are_404(self):
        for name in ("students:detail", "students:edit"):
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.b_student.pk])).status_code, 404)

    def test_other_teachers_student_cannot_be_modified(self):
        res = self.client.post(reverse("students:edit", args=[self.b_student.pk]), {**VALID, "name": "바꿈"})
        self.assertEqual(res.status_code, 404)
        for name in ("students:archive", "students:restore"):
            target = self.b_student if name == "students:archive" else self.b_archived
            with self.subTest(action=name):
                self.assertEqual(self.client.post(reverse(name, args=[target.pk])).status_code, 404)
        self.b_student.refresh_from_db()
        self.b_archived.refresh_from_db()
        self.assertEqual((self.b_student.name, self.b_student.is_active), ("비학생", True))
        self.assertFalse(self.b_archived.is_active)

    def test_tampered_teacher_field_on_create_is_ignored(self):
        """조작된 POST에 teacher=B를 넣어도 로그인한 강사(A)의 수강생으로 저장된다."""
        self.client.post(reverse("students:create"), {**VALID, "name": "조작", "teacher": self.b.pk})
        self.assertEqual(Student.objects.get(name="조작").teacher, self.a)

    def test_missing_and_other_ids_look_the_same(self):
        """다른 강사의 수강생과 없는 수강생이 똑같이 404라서 존재 여부가 드러나지 않는다."""
        other = self.client.get(reverse("students:detail", args=[self.b_student.pk]))
        missing = self.client.get(reverse("students:detail", args=[999999]))
        self.assertEqual(other.status_code, missing.status_code)


class StudentLoginRequiredTests(TestCase):
    """URL 순회 테스트가 건너뛰는 경로 변수 화면도 로그인이 필요한지 직접 확인 (PR #36 리뷰 후속 1)."""

    def test_all_student_urls_require_login(self):
        teacher = make_teacher("a@example.com", "A강사")
        s = Student.objects.create(teacher=teacher, name="김하늘")
        login = reverse("login")
        for method, url in (
            ("get", reverse("students:list")),
            ("get", reverse("students:create")),
            ("get", reverse("students:detail", args=[s.pk])),
            ("get", reverse("students:edit", args=[s.pk])),
            ("post", reverse("students:edit", args=[s.pk])),
            ("post", reverse("students:archive", args=[s.pk])),
            ("post", reverse("students:restore", args=[s.pk])),
        ):
            with self.subTest(method=method, url=url):
                res = getattr(self.client, method)(url)
                self.assertEqual(res.status_code, 302)
                self.assertTrue(res["Location"].startswith(login))
        s.refresh_from_db()
        self.assertTrue(s.is_active)
