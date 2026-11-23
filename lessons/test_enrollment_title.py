"""#75 레슨권 추가: 이전 레슨명을 기본으로 고르고, '직접 입력'으로 새 이름도 저장 (UC-03, S11)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from lessons.models import Enrollment
from students.models import Student

User = get_user_model()
CUSTOM = "__custom__"


def make_pass(student, title, start):
    return Enrollment.objects.create(student=student, title=title, total_count=8, start_date=start)


class EnrollmentTitleChoiceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(email="a@example.com", password="Lemit-pass-9876", name="A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])

    def post(self, **data):
        base = {"total_count": "8", "start_date": "2026-11-23"}
        return self.client.post(self.url, {**base, **data})

    def test_new_student_types_title_directly(self):
        r = self.client.get(self.url)
        form = r.context["form"]
        self.assertNotIn("title_choice", form.fields)
        self.assertTrue(form.fields["title"].required)
        self.assertNotContains(r, "직접 입력")
        self.assertRedirects(self.post(title="피아노 개인 레슨"), reverse("students:detail", args=[self.student.pk]))
        self.assertEqual(Enrollment.objects.get().title, "피아노 개인 레슨")

    def test_previous_titles_latest_first_without_duplicates_and_latest_is_default(self):
        make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 9, 1))
        deep = make_pass(self.student, "피아노 심화", datetime.date(2026, 10, 1))
        latest = make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 11, 1))  # 같은 이름은 한 번만
        form = self.client.get(self.url).context["form"]
        choices = list(form.fields["title_choice"].choices)
        # 값은 그 레슨명을 쓴 가장 최근 레슨권의 PK, 보이는 글자는 레슨명 (PR #86 리뷰 P3)
        self.assertEqual(
            choices, [(str(latest.pk), "피아노 개인 레슨"), (str(deep.pk), "피아노 심화"), (CUSTOM, "직접 입력")]
        )
        self.assertEqual(form["title_choice"].value(), str(latest.pk))  # 가장 최근 레슨명이 기본 선택

    def test_save_with_default_previous_title(self):
        prev = make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 10, 1))
        r = self.post(title_choice=str(prev.pk), title="")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Enrollment.objects.latest("id").title, "피아노 개인 레슨")

    def test_chosen_title_wins_over_leftover_typed_text(self):
        prev = make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 10, 1))
        self.post(title_choice=str(prev.pk), title="숨겨진 칸에 남은 글자")
        self.assertEqual(Enrollment.objects.latest("id").title, "피아노 개인 레슨")

    def test_custom_title_is_saved(self):
        make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 10, 1))
        self.post(title_choice=CUSTOM, title="  바이올린 체험  ")
        self.assertEqual(Enrollment.objects.latest("id").title, "바이올린 체험")

    def test_custom_without_title_is_error(self):
        make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 10, 1))
        r = self.post(title_choice=CUSTOM, title=" ")
        self.assertEqual(r.status_code, 200)
        self.assertIn("레슨명을 입력하세요.", r.context["form"].errors["title"])
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_forged_choice_is_rejected(self):
        make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 10, 1))
        other = Student.objects.create(teacher=self.teacher, name="이도윤")
        others = make_pass(other, "다른 학생 레슨", datetime.date(2026, 10, 1))
        for forged in (str(others.pk), "다른 학생 레슨", "피아노 개인 레슨"):  # 남의 레슨권 PK, 레슨명 문자열
            r = self.post(title_choice=forged, title="")
            self.assertEqual(r.status_code, 200)
            self.assertIn("title_choice", r.context["form"].errors)
        self.assertEqual(Enrollment.objects.filter(student=self.student).count(), 1)

    def test_previous_title_equal_to_custom_marker_is_kept_distinct(self):
        """PR #86 리뷰 P3: 실제 레슨명이 '__custom__'이어도 '직접 입력'과 구분된다."""
        odd = make_pass(self.student, CUSTOM, datetime.date(2026, 10, 1))
        self.post(title_choice=str(odd.pk), title="")
        self.assertEqual(Enrollment.objects.latest("id").title, CUSTOM)
        self.post(title_choice=CUSTOM, title="새 이름")
        self.assertEqual(Enrollment.objects.latest("id").title, "새 이름")

    def test_request_without_choice_uses_typed_title(self):
        """예전 화면·직접 POST처럼 title_choice가 없으면 입력한 레슨명을 쓴다(기존 동작 유지)."""
        make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 10, 1))
        self.post(title="새 8회권")
        self.assertEqual(Enrollment.objects.latest("id").title, "새 8회권")

    def test_only_this_students_titles_and_at_most_five(self):
        for i in range(7):
            make_pass(self.student, f"레슨{i}", datetime.date(2026, 1, 1) + datetime.timedelta(days=i))
        other = Student.objects.create(teacher=self.teacher, name="이도윤")
        make_pass(other, "남의 레슨", datetime.date(2026, 12, 1))
        labels = [t for _, t in self.client.get(self.url).context["form"].fields["title_choice"].choices]
        self.assertEqual(labels, ["레슨6", "레슨5", "레슨4", "레슨3", "레슨2", "직접 입력"])

    def test_page_renders_radios_and_toggle_box(self):
        prev = make_pass(self.student, "피아노 개인 레슨", datetime.date(2026, 10, 1))
        html = self.client.get(self.url).content.decode()
        self.assertIn(f'value="{prev.pk}"', html)
        self.assertIn("피아노 개인 레슨", html)
        self.assertIn('value="__custom__"', html)
        self.assertIn('data-custom-toggle="title_choice"', html)
        self.assertIn("새 레슨명", html)
