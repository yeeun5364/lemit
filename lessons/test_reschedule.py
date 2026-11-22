"""UC-11 회차 일정 변경 (#18): 예정 회차의 날짜·시간·장소를 바꾸면 변경 전 일정을 이력으로 남긴다."""

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from lessons.models import Enrollment, LessonSession, SessionChangeLog
from lessons.tests import add_session, after_session_validation, make_pass, make_teacher
from students.models import Student


def local(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime(2026, 11, day, hour, minute))


class RescheduleTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.enrollment = make_pass(self.student)
        self.session = add_session(self.enrollment, day=21)  # 11/21 (토요일) 17:00–18:00, 장소 없음
        self.client.force_login(self.teacher)

    def edit(self, session=None, *, date="2026-11-21", start="17:00", end="18:00", place="", enrollment=None):
        session = session or self.session
        data = {
            "enrollment": (enrollment or session.enrollment).pk,
            "date": date,
            "start_time": start,
            "end_time": end,
            "place": place,
        }
        return self.client.post(reverse("lessons:session_edit", args=[session.pk]), data, follow=True)

    def detail(self, session=None):
        return self.client.get(reverse("lessons:session_detail", args=[(session or self.session).pk]))

    def test_date_change_moves_session_and_keeps_previous_schedule(self):
        r = self.edit(date="2026-11-28", start="15:00", end="16:00", place="A 레슨실")
        self.assertContains(r, "회차를 수정했습니다")
        self.session.refresh_from_db()
        self.assertEqual((self.session.start_at, self.session.end_at), (local(28, 15), local(28, 16)))

        log = SessionChangeLog.objects.get()
        self.assertEqual(
            (log.session, log.old_start_at, log.old_end_at, log.old_place),
            (self.session, local(21, 17), local(21, 18), ""),
        )
        self.assertEqual((log.new_start_at, log.new_end_at, log.new_place), (local(28, 15), local(28, 16), "A 레슨실"))

        # 수용 기준 1: 변경 후 일정 조회에 새 일시로 표시된다 (주간 보기: 새 주에만 있음)
        url = reverse("lessons:session_detail", args=[self.session.pk])
        week = reverse("lessons:schedule")
        self.assertContains(self.client.get(week, {"view": "week", "date": "2026-11-28"}), url)
        self.assertNotContains(self.client.get(week, {"view": "week", "date": "2026-11-21"}), url)

        # 수용 기준 2: 변경 전 일시를 확인할 수 있다 (회차 상세의 일정 변경 이력)
        html = self.detail().content.decode()
        self.assertIn("일정 변경 이력", html)
        history = html[html.index("일정 변경 이력") :]
        self.assertIn("11/21 (토요일) 17:00 – 18:00 · 장소 없음", history)
        self.assertIn("11/28 (토요일) 15:00 – 16:00 · A 레슨실", history)

    def test_place_only_change_is_logged(self):
        self.edit(place="B 레슨실")
        log = SessionChangeLog.objects.get()
        self.assertFalse(log.time_changed)
        self.assertTrue(log.place_changed)
        self.assertContains(self.detail(), "장소만 바뀜")

    def test_saving_without_schedule_change_leaves_no_history(self):
        self.edit()  # 그대로 저장
        other_pass = make_pass(self.student, title="피아노 심화")
        self.edit(enrollment=other_pass)  # 레슨권만 옮김: 일정 변경이 아니다
        self.session.refresh_from_db()
        self.assertEqual(self.session.enrollment, other_pass)
        self.assertFalse(SessionChangeLog.objects.exists())
        self.assertNotContains(self.detail(), "일정 변경 이력")

    def test_each_change_is_kept_newest_first(self):
        self.edit(start="18:00", end="19:00")
        self.edit(date="2026-11-22", start="18:00", end="19:00")
        logs = list(self.session.change_logs.all())
        self.assertEqual(len(logs), 2)
        self.assertEqual((logs[0].old_start_at, logs[0].new_start_at), (local(21, 18), local(22, 18)))  # 최신 먼저
        self.assertEqual((logs[1].old_start_at, logs[1].new_start_at), (local(21, 17), local(21, 18)))
        html = self.detail().content.decode()
        self.assertLess(html.index("11/22 (일요일) 18:00"), html.index("11/21 (토요일) 17:00"))

    def test_previous_value_is_read_after_lock(self):
        """화면을 연 뒤 다른 탭에서 시간을 바꿨다면, 그 값이 '변경 전'으로 남는다 (잠근 뒤 다시 읽은 값)."""

        def moved_elsewhere():
            LessonSession.objects.filter(pk=self.session.pk).update(start_at=local(21, 10), end_at=local(21, 11))

        with after_session_validation(moved_elsewhere):
            self.edit(start="15:00", end="16:00")
        log = SessionChangeLog.objects.get()
        self.assertEqual((log.old_start_at, log.new_start_at), (local(21, 10), local(21, 15)))

    def test_rejected_edit_leaves_no_history(self):
        def complete():
            LessonSession.objects.filter(pk=self.session.pk).update(status=LessonSession.Status.COMPLETED)

        with after_session_validation(complete):
            self.edit(start="09:00", end="10:00")
        self.assertFalse(SessionChangeLog.objects.exists())

    def test_invalid_form_leaves_no_history(self):
        r = self.client.post(
            reverse("lessons:session_edit", args=[self.session.pk]),
            {"enrollment": self.enrollment.pk, "date": "2026-11-21", "start_time": "18:00", "end_time": "17:00"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SessionChangeLog.objects.exists())

    def test_history_kept_after_status_change_and_removed_with_session(self):
        self.edit(start="18:00", end="19:00")
        LessonSession.objects.filter(pk=self.session.pk).update(status=LessonSession.Status.COMPLETED)
        self.assertContains(self.detail(), "일정 변경 이력")  # 완료 후에도 이력은 보인다
        LessonSession.objects.filter(pk=self.session.pk).update(status=LessonSession.Status.SCHEDULED)
        self.client.post(reverse("lessons:session_delete", args=[self.session.pk]))
        self.assertFalse(SessionChangeLog.objects.exists())  # 예정 회차를 지우면 이력도 함께

    def test_other_teacher_cannot_see_history(self):
        self.edit(start="18:00", end="19:00")
        other = make_teacher("b@example.com", "B강사")
        self.client.force_login(other)
        self.assertEqual(self.detail().status_code, 404)

    def test_archive_removes_scheduled_session_and_its_history(self):
        self.edit(start="18:00", end="19:00")
        self.client.post(reverse("students:archive", args=[self.student.pk]))
        self.assertFalse(Student.objects.get(pk=self.student.pk).is_active)
        self.assertFalse(LessonSession.objects.exists())
        self.assertFalse(SessionChangeLog.objects.exists())
        self.assertTrue(Enrollment.objects.exists())
