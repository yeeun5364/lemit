"""UC-16 라벨 관리 (#27): 라벨 CRUD, 레슨권 라벨, 일정 색 표시, 강사 A/B 격리."""

import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from lessons.models import Enrollment, Label, LessonSession
from students.models import Student

User = get_user_model()
PASSWORD = "Lemit-pass-9876"
CSS = Path(settings.BASE_DIR) / "static" / "css" / "app.css"


def make_teacher(email, name):
    return User.objects.create_user(email=email, password=PASSWORD, name=name)


def make_enrollment(teacher, student_name="학생", label=None, title="피아노 개인 레슨"):
    student = Student.objects.create(teacher=teacher, name=student_name)
    return Enrollment.objects.create(
        student=student, title=title, total_count=8, start_date=datetime.date(2026, 11, 1), label=label
    )


def make_session(enrollment, day, hour=15, status=LessonSession.Status.SCHEDULED):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)), tz)
    return LessonSession.objects.create(
        enrollment=enrollment, start_at=start, end_at=start + datetime.timedelta(hours=1), status=status
    )


def day_cell(response, day):
    return next(c for week in response.context["grid"] for c in week if c["date"] == day)


class LabelTestCase(TestCase):
    def setUp(self):
        self.a = make_teacher("a@example.com", "강사A")
        self.b = make_teacher("b@example.com", "강사B")
        self.client.force_login(self.a)


class LabelCrudTests(LabelTestCase):
    def test_create_edit_delete(self):
        r = self.client.post(reverse("lessons:label_create"), {"name": "  피아노 ", "color": "blue"})
        self.assertRedirects(r, reverse("lessons:label_list"))
        label = Label.objects.get(teacher=self.a)
        self.assertEqual((label.name, label.color), ("피아노", "blue"))

        r = self.client.post(reverse("lessons:label_edit", args=[label.pk]), {"name": "출강", "color": "green"})
        self.assertRedirects(r, reverse("lessons:label_list"))
        label.refresh_from_db()
        self.assertEqual((label.name, label.color), ("출강", "green"))

        r = self.client.post(reverse("lessons:label_delete", args=[label.pk]))
        self.assertRedirects(r, reverse("lessons:label_list"))
        self.assertFalse(Label.objects.filter(pk=label.pk).exists())

    def test_list_shows_only_my_labels_with_enrollment_count(self):
        mine = Label.objects.create(teacher=self.a, name="피아노", color="blue")
        make_enrollment(self.a, "학생1", mine)
        make_enrollment(self.a, "학생2", mine)
        Label.objects.create(teacher=self.b, name="B의라벨", color="red")
        r = self.client.get(reverse("lessons:label_list"))
        self.assertContains(r, "피아노")
        self.assertContains(r, "레슨권 2개")
        self.assertNotContains(r, "B의라벨")

    def test_color_must_be_one_of_eight(self):
        self.assertEqual(len(Label.Color.choices), 8)
        for color in ("pink", "", "#ff0000"):
            with self.subTest(color=color):
                r = self.client.post(reverse("lessons:label_create"), {"name": "피아노", "color": color})
                self.assertEqual(r.status_code, 200)
                self.assertIn("color", r.context["form"].errors)
        self.assertFalse(Label.objects.exists())
        r = self.client.get(reverse("lessons:label_create"))
        self.assertEqual(len(r.context["form"].fields["color"].choices), 8)  # 빈 선택지 없이 8색만

    def test_name_required_and_unique_per_teacher(self):
        Label.objects.create(teacher=self.a, name="피아노", color="blue")
        Label.objects.create(teacher=self.b, name="체험", color="orange")
        r = self.client.post(reverse("lessons:label_create"), {"name": "피아노", "color": "red"})
        self.assertContains(r, "같은 이름의 라벨이 이미 있습니다.")
        r = self.client.post(reverse("lessons:label_create"), {"name": "   ", "color": "red"})
        self.assertIn("name", r.context["form"].errors)
        # 다른 강사에게 같은 이름이 있어도 만들 수 있다
        r = self.client.post(reverse("lessons:label_create"), {"name": "체험", "color": "red"})
        self.assertEqual(r.status_code, 302)
        # 수정할 때 자기 이름은 그대로 저장할 수 있다
        own = Label.objects.get(teacher=self.a, name="피아노")
        r = self.client.post(reverse("lessons:label_edit", args=[own.pk]), {"name": "피아노", "color": "teal"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Label.objects.filter(teacher=self.a).count(), 2)

    def test_delete_keeps_enrollments_and_sessions_as_unlabeled(self):
        label = Label.objects.create(teacher=self.a, name="피아노", color="blue")
        e = make_enrollment(self.a, "학생", label)
        s = make_session(e, timezone.localdate(), status=LessonSession.Status.COMPLETED)
        page = self.client.get(reverse("lessons:label_edit", args=[label.pk]))
        self.assertContains(page, "레슨권 1개가 라벨 없음(회색)이 됩니다")
        r = self.client.post(reverse("lessons:label_delete", args=[label.pk]), follow=True)
        self.assertContains(r, "레슨권 1개가 라벨 없음이 되었습니다")
        e.refresh_from_db()
        s.refresh_from_db()
        self.assertIsNone(e.label)
        self.assertEqual(s.status, LessonSession.Status.COMPLETED)

    def test_delete_requires_post(self):
        label = Label.objects.create(teacher=self.a, name="피아노", color="blue")
        self.assertEqual(self.client.get(reverse("lessons:label_delete", args=[label.pk])).status_code, 405)
        self.assertTrue(Label.objects.filter(pk=label.pk).exists())

    def test_next_redirect_only_inside_site(self):
        e = make_enrollment(self.a)
        back = reverse("lessons:enrollment_label", args=[e.pk])
        r = self.client.post(reverse("lessons:label_create"), {"name": "피아노", "color": "blue", "next": back})
        self.assertRedirects(r, back)
        r = self.client.post(
            reverse("lessons:label_create"), {"name": "기타", "color": "green", "next": "https://evil.example/"}
        )
        self.assertRedirects(r, reverse("lessons:label_list"))

    def test_login_required(self):
        self.client.logout()
        for url in (reverse("lessons:label_list"), reverse("lessons:label_create")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)


class EnrollmentLabelTests(LabelTestCase):
    def setUp(self):
        super().setUp()
        self.piano = Label.objects.create(teacher=self.a, name="피아노", color="blue")
        self.b_label = Label.objects.create(teacher=self.b, name="B의라벨", color="red")
        self.student = Student.objects.create(teacher=self.a, name="학생")

    def post_enrollment(self, **extra):
        data = {"title": "피아노 개인 레슨", "total_count": 8, "start_date": "2026-11-22", **extra}
        return self.client.post(reverse("lessons:enrollment_create", args=[self.student.pk]), data)

    def test_create_enrollment_with_or_without_label(self):
        self.assertEqual(self.post_enrollment(label=self.piano.pk).status_code, 302)
        self.assertEqual(self.post_enrollment(label="").status_code, 302)
        labels = set(Enrollment.objects.values_list("label_id", flat=True))
        self.assertEqual(labels, {None, self.piano.pk})

    def test_form_offers_only_my_labels(self):
        r = self.client.get(reverse("lessons:enrollment_create", args=[self.student.pk]))
        self.assertEqual(list(r.context["form"].fields["label"].queryset), [self.piano])
        self.assertNotContains(r, "B의라벨")

    def test_forged_other_teachers_label_is_rejected(self):
        r = self.post_enrollment(label=self.b_label.pk)
        self.assertEqual(r.status_code, 200)
        self.assertIn("label", r.context["form"].errors)
        self.assertFalse(Enrollment.objects.exists())

        e = make_enrollment(self.a, "학생2", self.piano)
        r = self.client.post(reverse("lessons:enrollment_label", args=[e.pk]), {"label": self.b_label.pk})
        self.assertEqual(r.status_code, 200)
        self.assertIn("label", r.context["form"].errors)
        e.refresh_from_db()
        self.assertEqual(e.label, self.piano)

    def test_model_clean_rechecks_label_owner_before_save(self):
        e = Enrollment(
            student=self.student, title="x", total_count=1, start_date=datetime.date(2026, 11, 1), label=self.b_label
        )
        with self.assertRaises(ValidationError) as cm:
            e.full_clean()
        self.assertIn("label", cm.exception.message_dict)

    def test_change_and_clear_label_keeps_other_fields(self):
        e = make_enrollment(self.a, "학생2")
        url = reverse("lessons:enrollment_label", args=[e.pk])
        self.assertContains(self.client.get(url), "피아노")
        # 화면을 연 뒤 다른 탭에서 레슨권을 종료했어도 라벨 저장이 상태를 되돌리지 않는다
        Enrollment.objects.filter(pk=e.pk).update(status=Enrollment.Status.ENDED)
        r = self.client.post(url, {"label": self.piano.pk})
        self.assertRedirects(r, reverse("students:detail", args=[e.student_id]))
        e.refresh_from_db()
        self.assertEqual((e.label, e.status), (self.piano, Enrollment.Status.ENDED))
        self.client.post(url, {"label": ""})
        e.refresh_from_db()
        self.assertIsNone(e.label)

    def test_student_detail_shows_label_chip(self):
        e = make_enrollment(self.a, "학생2", self.piano)
        r = self.client.get(reverse("students:detail", args=[e.student_id]))
        self.assertContains(r, 'class="dot lc-blue"')
        self.assertContains(r, "라벨 변경")


class LabelIsolationTests(LabelTestCase):
    """강사 A·B 격리 (UC-16 수용 기준): B의 라벨·레슨권은 A에게 보이지도, 바뀌지도 않는다."""

    def test_other_teachers_label_pages_are_404_and_unchanged(self):
        b_label = Label.objects.create(teacher=self.b, name="B의라벨", color="red")
        for name, method, data in (
            ("lessons:label_edit", "get", None),
            ("lessons:label_edit", "post", {"name": "탈취", "color": "blue"}),
            ("lessons:label_delete", "post", None),
        ):
            with self.subTest(name=name, method=method):
                r = getattr(self.client, method)(reverse(name, args=[b_label.pk]), data)
                self.assertEqual(r.status_code, 404)
        b_label.refresh_from_db()
        self.assertEqual((b_label.name, b_label.color), ("B의라벨", "red"))

    def test_other_teachers_enrollment_label_page_is_404(self):
        b_e = make_enrollment(self.b, "B학생")
        mine = Label.objects.create(teacher=self.a, name="피아노", color="blue")
        url = reverse("lessons:enrollment_label", args=[b_e.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"label": mine.pk}).status_code, 404)
        b_e.refresh_from_db()
        self.assertIsNone(b_e.label)

    def test_schedule_ignores_other_teachers_label_filter(self):
        b_label = Label.objects.create(teacher=self.b, name="B의라벨", color="red")
        make_session(make_enrollment(self.a), timezone.localdate())
        r = self.client.get(reverse("lessons:schedule"), {"label": b_label.pk})
        self.assertIsNone(r.context["label"])
        self.assertEqual(len(r.context["day_sessions"]), 1)
        self.assertNotContains(r, "B의라벨")


class ScheduleColorTests(LabelTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.piano = Label.objects.create(teacher=self.a, name="피아노", color="blue")
        self.guitar = Label.objects.create(teacher=self.a, name="기타", color="green")

    def test_session_line_uses_label_color_or_gray(self):
        make_session(make_enrollment(self.a, "라벨학생", self.piano), self.today, 10)
        make_session(make_enrollment(self.a, "무라벨학생"), self.today, 11)
        html = self.client.get(reverse("lessons:schedule")).content.decode()
        self.assertIn('class="label-bar lc-blue"', html)
        self.assertIn('class="label-bar" aria-hidden', html)  # 라벨 없음 = 회색 기본
        self.assertIn('<span class="label-name">피아노</span>', html)  # 색만으로 전달하지 않음
        home = self.client.get(reverse("home")).content.decode()  # 대시보드 '오늘 수업'도 같은 줄
        self.assertIn('class="label-bar lc-blue"', home)

    def test_month_dots_merge_same_label_and_cap_at_three(self):
        pairs = (("보컬", "orange"), ("드럼", "purple"))
        extra = [Label.objects.create(teacher=self.a, name=n, color=c) for n, c in pairs]
        for i, label in enumerate([self.piano, self.guitar, *extra]):
            e = make_enrollment(self.a, f"학생{i}", label)
            make_session(e, self.today, 9 + i)
            make_session(e, self.today, 15 + i)  # 같은 라벨 두 번 → 점 하나
        cell = day_cell(self.client.get(reverse("lessons:schedule")), self.today)
        self.assertEqual(cell["count"], 8)
        self.assertEqual(cell["dots"], ["blue", "green", "orange"])

    def test_unlabeled_sessions_make_one_gray_dot(self):
        make_session(make_enrollment(self.a, "학생1"), self.today, 9)
        make_session(make_enrollment(self.a, "학생2"), self.today, 10)
        make_session(make_enrollment(self.a, "학생3", self.piano), self.today, 11)
        r = self.client.get(reverse("lessons:schedule"))
        self.assertEqual(day_cell(r, self.today)["dots"], ["", "blue"])
        self.assertContains(r, '<span class="dot"></span><span class="dot lc-blue"></span>', html=False)

    def test_filter_by_label_in_month_and_week_and_links_keep_it(self):
        make_session(make_enrollment(self.a, "피아노학생", self.piano), self.today, 10)
        make_session(make_enrollment(self.a, "기타학생", self.guitar), self.today, 11)
        r = self.client.get(reverse("lessons:schedule"), {"label": self.piano.pk})
        self.assertEqual([s.enrollment.student.name for s in r.context["day_sessions"]], ["피아노학생"])
        self.assertIn(f"label={self.piano.pk}", r.context["next_url"])
        r = self.client.get(reverse("lessons:schedule"), {"view": "week", "label": self.guitar.pk})
        names = [s.enrollment.student.name for d in r.context["week"] for s in d["sessions"]]
        self.assertEqual(names, ["기타학생"])

    def test_status_is_shape_not_color(self):
        """회차 상태는 라벨 색과 섞이지 않게 모양(체크·흐리게·취소선)과 글자로만 표시한다."""
        e = make_enrollment(self.a, "학생", self.piano)
        make_session(e, self.today, 9, LessonSession.Status.COMPLETED)
        make_session(e, self.today, 10, LessonSession.Status.CANCELLED)
        make_session(e, self.today, 11, LessonSession.Status.SCHEDULED)
        html = self.client.get(reverse("lessons:schedule")).content.decode()
        for status, text in (("completed", "완료"), ("cancelled", "휴강"), ("scheduled", "예정")):
            with self.subTest(status=status):
                self.assertIn(f"is-{status}", html)
                self.assertIn(text, html)
        # 막대는 상태와 상관없이 같은 라벨 색이고, 상태별로 막대 색을 바꾸는 CSS도 없다
        self.assertEqual(html.count('class="label-bar lc-blue"'), 3)
        css = CSS.read_text(encoding="utf-8")
        strike = ".session-row.is-cancelled .row-title, .session-row.is-absent .row-title { text-decoration: "
        strike += "line-through"
        self.assertIn(strike, css)
        self.assertNotRegex(css, r"\.is-(completed|cancelled|absent|scheduled)[^{]*\.label-bar")

    def test_query_count_does_not_grow_with_labeled_sessions(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("lessons:schedule"))
            return len(ctx)

        make_session(make_enrollment(self.a, "학생0", self.piano), self.today, 9)
        few = count()
        for i in range(1, 6):
            label = Label.objects.create(teacher=self.a, name=f"추가{i}", color="teal")
            make_session(make_enrollment(self.a, f"학생{i}", label), self.today, 9 + i)
        self.assertEqual(count(), few)


class LabelCalendarHintTests(LabelTestCase):
    """#77: 라벨이 달력에 보이는지 알려 주기. 운영에서 '라벨을 만들었는데 달력에 반영이 안 된다'는 보고는
    라벨이 붙은 레슨권에 회차가 0개인 경우였다. 만들기 → 레슨권에 붙이기 → 회차 등록 흐름을 화면 그대로 따라간다."""

    def label_row_hint(self, label):
        html = self.client.get(reverse("lessons:label_list")).content.decode()
        row = html.split(f'href="{reverse("lessons:label_edit", args=[label.pk])}"', 1)[1].split("</li>", 1)[0]
        return row

    def test_flow_label_then_enrollment_then_session_shows_color(self):
        today = timezone.localdate()
        # 1) 라벨 만들기 → 아직 레슨권에 안 붙음
        self.client.post(reverse("lessons:label_create"), {"name": "피아노", "color": "orange"})
        label = Label.objects.get(teacher=self.a, name="피아노")
        row = self.label_row_hint(label)
        self.assertIn("레슨권 0개 · 회차 0개", row)
        self.assertIn("아직 레슨권에 붙이지 않아 달력에 보이지 않아요", row)

        # 2) 레슨권에 붙이기 → 회차가 없다는 안내 (저장 메시지와 목록 모두)
        enrollment = make_enrollment(self.a, "김하늘")
        r = self.client.post(
            reverse("lessons:enrollment_label", args=[enrollment.pk]), {"label": label.pk}, follow=True
        )
        self.assertContains(r, "아직 회차가 없어 달력에는 보이지 않아요")
        row = self.label_row_hint(label)
        self.assertIn("레슨권 1개 · 회차 0개", row)
        self.assertIn("달력에 표시할 회차가 없어요. 라벨을 붙인 레슨권으로 일정을 추가하면", row)
        r = self.client.get(reverse("lessons:schedule"), {"label": label.pk})
        self.assertTrue(r.context["label_without_sessions"])
        self.assertContains(r, "라벨로 달력에 표시할 회차가 없어요")

        # 3) 그 레슨권으로 회차 등록 → 달력 점·목록 막대가 고른 색, 목록은 일정 바로가기
        data = {"enrollment": enrollment.pk, "date": today.isoformat(), "start_time": "16:00", "end_time": "17:00"}
        self.client.post(reverse("lessons:session_create"), data)
        row = self.label_row_hint(label)
        self.assertIn("레슨권 1개 · 회차 1개", row)
        link = f"{reverse('lessons:schedule')}?label={label.pk}&amp;date={today.isoformat()}"
        self.assertIn(link, row)
        r = self.client.get(reverse("lessons:schedule"), {"label": label.pk, "date": today.isoformat()})
        self.assertFalse(r.context["label_without_sessions"])
        self.assertNotContains(r, "라벨로 달력에 표시할 회차가 없어요")
        self.assertEqual(day_cell(r, today)["dots"], ["orange"])  # 달력 점
        self.assertContains(r, '<span class="dot lc-orange"></span>', html=False)
        self.assertContains(r, 'class="label-bar lc-orange"', html=False)  # 그날 목록 막대
        week = self.client.get(reverse("lessons:schedule"), {"view": "week", "date": today.isoformat()})
        self.assertContains(week, 'class="label-bar lc-orange"', html=False)  # 주간 목록 막대

    def test_attaching_label_to_enrollment_with_sessions_has_no_empty_hint(self):
        label = Label.objects.create(teacher=self.a, name="기타", color="green")
        enrollment = make_enrollment(self.a, "이도윤")
        make_session(enrollment, timezone.localdate())
        r = self.client.post(
            reverse("lessons:enrollment_label", args=[enrollment.pk]), {"label": label.pk}, follow=True
        )
        self.assertContains(r, "라벨을 붙였습니다")
        self.assertNotContains(r, "아직 회차가 없어")

    def test_schedule_link_goes_to_next_session_else_last(self):
        label = Label.objects.create(teacher=self.a, name="보컬", color="purple")
        enrollment = make_enrollment(self.a, "박서연", label)
        today = timezone.localdate()
        past = today - datetime.timedelta(days=40)
        make_session(enrollment, past)
        self.assertIn(f"date={past.isoformat()}", self.label_row_hint(label))  # 다가오는 회차 없음 → 마지막 회차
        soon = today + datetime.timedelta(days=45)
        make_session(enrollment, soon)
        make_session(enrollment, today + datetime.timedelta(days=60))
        self.assertIn(f"date={soon.isoformat()}", self.label_row_hint(label))  # 가장 가까운 다가오는 회차

    def test_archived_students_sessions_are_not_counted(self):
        label = Label.objects.create(teacher=self.a, name="체험", color="blue")
        enrollment = make_enrollment(self.a, "보관학생", label)
        make_session(enrollment, timezone.localdate())
        Student.objects.filter(pk=enrollment.student_id).update(is_active=False)  # 일정 화면에서도 빠짐
        row = self.label_row_hint(label)
        self.assertIn("회차 0개", row)
        # PR #85 리뷰 P3: 보관 수강생에게는 일정을 추가할 수 없으므로 보관 해제·다른 레슨권을 안내
        self.assertIn("모두 보관한 수강생의 것이에요", row)
        self.assertIn("수강생 보관을 해제하세요", row)
        self.assertNotIn("일정을 추가하면", row)
        r = self.client.get(reverse("lessons:schedule"), {"label": label.pk})
        self.assertTrue(r.context["label_without_sessions"])
        self.assertContains(r, "보관한 수강생이라면 보관을 해제하세요")

    def test_archived_and_active_enrollments_guide_to_add_sessions(self):
        """보관 수강생 레슨권과 활성 수강생 레슨권(회차 없음)에 함께 붙어 있으면 일정 추가 안내."""
        label = Label.objects.create(teacher=self.a, name="혼합", color="green")
        archived = make_enrollment(self.a, "보관학생", label)
        make_session(archived, timezone.localdate())
        Student.objects.filter(pk=archived.student_id).update(is_active=False)
        make_enrollment(self.a, "활성학생", label)
        row = self.label_row_hint(label)
        self.assertIn("레슨권 2개 · 회차 0개", row)
        self.assertIn("라벨을 붙인 레슨권으로 일정을 추가하면", row)
        self.assertNotIn("모두 보관한 수강생", row)

    def test_counts_only_my_labels(self):
        mine = Label.objects.create(teacher=self.a, name="내라벨", color="blue")
        theirs = Label.objects.create(teacher=self.b, name="B라벨", color="blue")
        make_session(make_enrollment(self.b, "B학생", theirs), timezone.localdate())
        html = self.client.get(reverse("lessons:label_list")).content.decode()
        self.assertNotIn("B라벨", html)
        self.assertIn("레슨권 0개 · 회차 0개", self.label_row_hint(mine))
        r = self.client.get(reverse("lessons:schedule"), {"label": theirs.pk})  # 남의 라벨 거르기는 무시
        self.assertIsNone(r.context["label"])
        self.assertFalse(r.context["label_without_sessions"])
