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


class AdminChangeLogTests(TestCase):
    """관리자 화면의 변경 이력은 읽기 전용 (PR #73 리뷰).

    이력만 따로 지울 수 없고, 예정 회차를 지울 때만 함께 지워진다.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        from lessons.tests import PASSWORD

        admin_user = get_user_model().objects.create_superuser(
            email="admin@example.com", password=PASSWORD, name="운영자"
        )
        teacher = make_teacher("a@example.com", "A강사")
        self.enrollment = make_pass(Student.objects.create(teacher=teacher, name="박서연"))
        self.session = add_session(self.enrollment, day=21)
        self.log = SessionChangeLog.objects.create(
            session=self.session,
            old_start_at=local(20, 17),
            old_end_at=local(20, 18),
            new_start_at=local(21, 17),
            new_end_at=local(21, 18),
        )
        self.client.force_login(admin_user)

    def test_log_cannot_be_deleted_alone(self):
        url = reverse("admin:lessons_sessionchangelog_delete", args=[self.log.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(SessionChangeLog.objects.filter(pk=self.log.pk).exists())

    def test_log_bulk_delete_is_not_offered_or_accepted(self):
        changelist = reverse("admin:lessons_sessionchangelog_changelist")
        response = self.client.get(changelist)
        self.assertEqual(response.status_code, 200)  # 목록은 볼 수 있다
        self.assertNotContains(response, 'value="delete_selected"')
        self.client.post(changelist, {"action": "delete_selected", "_selected_action": [self.log.pk], "post": "yes"})
        self.assertTrue(SessionChangeLog.objects.filter(pk=self.log.pk).exists())

    def test_log_detail_is_read_only(self):
        url = reverse("admin:lessons_sessionchangelog_change", args=[self.log.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_save"')
        self.assertEqual(self.client.get(reverse("admin:lessons_sessionchangelog_add")).status_code, 403)

    def test_deleting_scheduled_session_in_admin_also_deletes_its_logs(self):
        url = reverse("admin:lessons_lessonsession_delete", args=[self.session.pk])
        confirm = self.client.get(url)
        self.assertEqual(confirm.status_code, 200)
        self.assertFalse(confirm.context["perms_lacking"])  # 이력 삭제 권한이 없다고 회차 삭제를 막지 않는다
        self.client.post(url, {"post": "yes"})
        self.assertFalse(LessonSession.objects.filter(pk=self.session.pk).exists())
        self.assertFalse(SessionChangeLog.objects.exists())

    def test_completed_session_with_logs_still_cannot_be_deleted(self):
        LessonSession.objects.filter(pk=self.session.pk).update(status=LessonSession.Status.COMPLETED)
        url = reverse("admin:lessons_lessonsession_delete", args=[self.session.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(SessionChangeLog.objects.filter(pk=self.log.pk).exists())
