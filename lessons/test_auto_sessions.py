"""#76 레슨권 등록 때 회차 자동 생성 (최소 범위): 첫 수업 날짜·시간·반복 요일로 총 회차만큼 '예정' 회차."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from lessons.models import Enrollment, Label, LessonSession
from students.models import Student

User = get_user_model()


def local(s):
    return timezone.localtime(s.start_at), timezone.localtime(s.end_at)


class AutoSessionTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(email="a@example.com", password="Lemit-pass-9876", name="A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="김하늘")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])

    def post(self, **extra):
        data = {"title": "피아노 개인 레슨", "total_count": "4", "start_date": "2026-11-23", **extra}
        return self.client.post(self.url, data, follow=True)

    def auto(self, **extra):
        data = {
            "make_sessions": "on",
            "first_date": "2026-11-24",  # 화요일
            "first_start": "16:00",
            "first_end": "17:00",
            **extra,
        }
        return self.post(**data)

    def test_without_checkbox_creates_no_sessions(self):
        self.post(first_date="2026-11-24", first_start="16:00", first_end="17:00")
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(LessonSession.objects.count(), 0)

    def test_weekly_on_first_dates_weekday_when_no_weekdays(self):
        r = self.auto(session_place="A 레슨실")
        e = Enrollment.objects.get()
        sessions = list(e.sessions.order_by("start_at"))
        self.assertEqual(
            [local(s)[0].date() for s in sessions],
            [datetime.date(2026, 11, 24) + datetime.timedelta(weeks=i) for i in range(4)],
        )
        self.assertTrue(
            all(local(s)[0].time() == datetime.time(16) and local(s)[1].time() == datetime.time(17) for s in sessions)
        )
        self.assertTrue(all(s.status == LessonSession.Status.SCHEDULED and s.place == "A 레슨실" for s in sessions))
        self.assertContains(r, "회차 4개를 만들었습니다(11/24~12/15)")

    def test_multiple_weekdays_fill_total_count(self):
        self.auto(total_count="5", weekdays=["1", "3"])  # 화·목
        days = [local(s)[0].date() for s in LessonSession.objects.order_by("start_at")]
        expected = ["2026-11-24", "2026-11-26", "2026-12-01", "2026-12-03", "2026-12-08"]
        self.assertEqual(days, [datetime.date.fromisoformat(d) for d in expected])

    def test_sessions_follow_enrollment_label_color_on_calendar(self):
        label = Label.objects.create(teacher=self.teacher, name="피아노", color="orange")
        self.auto(label=label.pk)
        r = self.client.get(reverse("lessons:schedule"), {"date": "2026-11-24"})
        cell = next(c for w in r.context["grid"] for c in w if c["date"] == datetime.date(2026, 11, 24))
        self.assertEqual(cell["dots"], ["orange"])
        self.assertContains(r, 'class="label-bar lc-orange"', html=False)

    def test_remaining_counts_stay_correct(self):
        self.auto()
        e = Enrollment.objects.with_usage().get()
        self.assertEqual((e.used_count, e.remaining_count), (0, 4))  # 예정 회차는 차감하지 않음

    def test_missing_inputs_are_errors_and_nothing_saved(self):
        r = self.post(make_sessions="on")
        form = r.context["form"]
        for name in ("first_date", "first_start", "first_end"):
            self.assertIn("회차를 함께 만들려면 입력하세요.", form.errors[name])
        self.assertEqual((Enrollment.objects.count(), LessonSession.objects.count()), (0, 0))

    def test_end_before_start_is_error(self):
        r = self.auto(first_end="15:00")
        self.assertIn("종료 시간은 시작 시간보다 늦어야 합니다.", r.context["form"].errors["first_end"])
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_first_date_weekday_must_be_selected(self):
        r = self.auto(weekdays=["3"])  # 첫 날짜는 화요일인데 목요일만
        self.assertIn("첫 수업 날짜의 요일을 반복 요일에 포함하세요.", r.context["form"].errors["weekdays"])
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_too_many_sessions_is_error(self):
        r = self.auto(total_count="101")
        self.assertIn("100회까지", r.context["form"].errors["total_count"][0])
        self.assertEqual(LessonSession.objects.count(), 0)

    def test_overlap_warns_but_saves(self):
        other = Student.objects.create(teacher=self.teacher, name="이도윤")
        e = Enrollment.objects.create(student=other, title="기타", total_count=8, start_date=datetime.date(2026, 11, 1))
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.datetime(2026, 12, 1, 16, 30), tz)
        LessonSession.objects.create(enrollment=e, start_at=start, end_at=start + datetime.timedelta(hours=1))
        r = self.auto()
        self.assertEqual(LessonSession.objects.filter(enrollment__student=self.student).count(), 4)
        self.assertContains(r, "만든 회차 중 1개가 다른 일정과 시간이 겹칩니다: 12/01")

    def test_page_shows_section(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("회차도 함께 만들기", html)
        self.assertIn('data-show-if-checked="make_sessions"', html)
        self.assertIn("반복 요일", html)
