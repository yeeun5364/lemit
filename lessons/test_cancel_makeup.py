"""UC-08 휴강 처리(#15)·UC-09 보강 일정 등록(#16) 테스트."""

import datetime

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from lessons.models import Enrollment, LessonSession
from students.models import Student

User = get_user_model()
Status = LessonSession.Status


def at(days, hour=15):
    day = timezone.localdate() + datetime.timedelta(days=days)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)), timezone.get_current_timezone())


class Base(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(email="a@example.com", password="Lemit-pass-9876", name="강사A")
        self.other = User.objects.create_user(email="b@example.com", password="Lemit-pass-9876", name="강사B")
        self.student = Student.objects.create(teacher=self.teacher, name="학생")
        self.enrollment = Enrollment.objects.create(
            student=self.student, title="피아노", total_count=4, start_date=datetime.date(2026, 11, 1)
        )
        self.client.force_login(self.teacher)

    def session(self, days=1, status=Status.SCHEDULED, enrollment=None, **extra):
        start = at(days)
        return LessonSession.objects.create(
            enrollment=enrollment or self.enrollment,
            start_at=start,
            end_at=start + datetime.timedelta(hours=1),
            status=status,
            **extra,
        )

    def remaining(self):
        return Enrollment.objects.with_usage().get(pk=self.enrollment.pk).remaining_count

    def post_makeup(self, original, days=3, **extra):
        data = {
            "date": (timezone.localdate() + datetime.timedelta(days=days)).isoformat(),
            "start_time": "16:00",
            "end_time": "17:00",
            "place": "B 레슨실",
            **extra,
        }
        return self.client.post(reverse("lessons:session_makeup", args=[original.pk]), data)


class CancelTests(Base):
    def test_cancel_keeps_remaining_and_saves_reason(self):
        s = self.session()
        before = self.remaining()
        r = self.client.post(reverse("lessons:session_cancel", args=[s.pk]), {"reason": "강사 개인 사정"}, follow=True)
        self.assertRedirects(r, reverse("lessons:session_detail", args=[s.pk]))
        self.assertContains(r, "레슨권은 차감하지 않습니다")
        self.assertContains(r, "보강 필요")
        self.assertContains(r, reverse("lessons:session_makeup", args=[s.pk]))  # 보강 등록 안내
        s.refresh_from_db()
        self.assertEqual((s.status, s.reason), (Status.CANCELLED, "강사 개인 사정"))
        self.assertEqual(self.remaining(), before)  # 휴강은 잔여에 영향 없음

    def test_reason_is_optional(self):
        s = self.session()
        self.client.post(reverse("lessons:session_cancel", args=[s.pk]), {})
        s.refresh_from_db()
        self.assertEqual((s.status, s.reason), (Status.CANCELLED, ""))

    def test_only_scheduled_can_be_cancelled_and_double_click_is_once(self):
        done = self.session(status=Status.COMPLETED, completed_at=timezone.now())
        before = self.remaining()
        r = self.client.post(reverse("lessons:session_cancel", args=[done.pk]), {"reason": "x"}, follow=True)
        self.assertContains(r, "예정 상태인 회차만 휴강 처리할 수 있습니다")
        done.refresh_from_db()
        self.assertEqual(done.status, Status.COMPLETED)
        self.assertEqual(self.remaining(), before)  # 완료(차감) 회차가 휴강으로 바뀌어 잔여가 늘지 않음

        s = self.session(days=2)
        self.client.post(reverse("lessons:session_cancel", args=[s.pk]), {"reason": "첫 번째"})
        self.client.post(reverse("lessons:session_cancel", args=[s.pk]), {"reason": "두 번째"})
        s.refresh_from_db()
        self.assertEqual(s.reason, "첫 번째")

    def test_cancel_requires_post_and_owner(self):
        s = self.session()
        self.assertEqual(self.client.get(reverse("lessons:session_cancel", args=[s.pk])).status_code, 405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("lessons:session_cancel", args=[s.pk])).status_code, 404)
        s.refresh_from_db()
        self.assertEqual(s.status, Status.SCHEDULED)

    def test_uncancel_restores_scheduled_unless_makeup_exists(self):
        s = self.session(status=Status.CANCELLED, reason="사정")
        self.client.post(reverse("lessons:session_uncancel", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual((s.status, s.reason), (Status.SCHEDULED, ""))

        c = self.session(days=2, status=Status.CANCELLED)
        self.session(days=4, makeup_for=c)
        r = self.client.post(reverse("lessons:session_uncancel", args=[c.pk]), follow=True)
        self.assertContains(r, "보강이 연결된 휴강은 취소할 수 없습니다")
        c.refresh_from_db()
        self.assertEqual(c.status, Status.CANCELLED)

    def test_makeup_needed_badge_and_student_detail_collection(self):
        need = self.session(days=-3, status=Status.CANCELLED, reason="감기")
        absent = self.session(days=-2, status=Status.ABSENT)  # 미차감 결석도 보강 대상
        covered = self.session(days=-1, status=Status.CANCELLED)
        self.session(days=2, makeup_for=covered)
        self.session(days=-4, status=Status.ABSENT, absence_deducted=True)  # 차감 결석은 대상 아님
        r = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertEqual([s.pk for s in r.context["makeup_needed"]], [need.pk, absent.pk])
        self.assertContains(r, "보강 필요")
        detail = self.client.get(reverse("lessons:session_detail", args=[covered.pk]))
        self.assertNotContains(detail, "보강 필요")
        self.assertContains(detail, "보강 회차")

    def test_schedule_shows_badge_without_extra_queries(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("lessons:schedule"))
            return len(ctx)

        self.session(days=0, status=Status.CANCELLED)
        few = count()
        for _ in range(4):
            self.session(days=0, status=Status.CANCELLED)
        self.assertEqual(count(), few)
        self.assertContains(self.client.get(reverse("lessons:schedule")), "보강 필요")


class MakeupTests(Base):
    def test_create_makeup_linked_both_ways(self):
        original = self.session(days=-1, status=Status.CANCELLED)
        r = self.post_makeup(original)
        makeup = LessonSession.objects.get(makeup_for=original)
        self.assertRedirects(r, reverse("lessons:session_detail", args=[makeup.pk]))
        self.assertEqual(
            (makeup.status, makeup.enrollment, makeup.place), (Status.SCHEDULED, self.enrollment, "B 레슨실")
        )
        self.assertEqual(timezone.localtime(makeup.start_at).time(), datetime.time(16))
        d = self.client.get(reverse("lessons:session_detail", args=[makeup.pk]))
        self.assertContains(d, "원래 회차")
        self.assertContains(d, reverse("lessons:session_detail", args=[original.pk]))
        o = self.client.get(reverse("lessons:session_detail", args=[original.pk]))
        self.assertContains(o, "보강 회차")
        self.assertContains(o, reverse("lessons:session_detail", args=[makeup.pk]))
        self.assertNotContains(o, "보강 필요")

    def test_only_one_makeup_per_session(self):
        original = self.session(status=Status.CANCELLED)
        self.post_makeup(original)
        r = self.post_makeup(original, days=5)
        self.assertRedirects(r, reverse("lessons:session_detail", args=[original.pk]))
        self.assertEqual(LessonSession.objects.filter(makeup_for=original).count(), 1)
        r = self.client.get(reverse("lessons:session_makeup", args=[original.pk]), follow=True)
        self.assertContains(r, "보강은 하나만 등록할 수 있습니다")

    def test_deducted_absence_and_other_statuses_cannot_have_makeup(self):
        cases = [
            (
                self.session(days=-2, status=Status.ABSENT, absence_deducted=True),
                "차감한 결석에는 보강을 등록할 수 없습니다",
            ),
            (self.session(days=-3, status=Status.COMPLETED, completed_at=timezone.now()), "휴강이나 차감하지 않은"),
            (self.session(days=2), "휴강이나 차감하지 않은"),
        ]
        for original, text in cases:
            with self.subTest(status=original.status):
                r = self.post_makeup(original)
                self.assertRedirects(
                    r, reverse("lessons:session_detail", args=[original.pk]), fetch_redirect_response=False
                )
                self.assertContains(self.client.get(r["Location"]), text)
                self.assertFalse(LessonSession.objects.filter(makeup_for=original).exists())

    def test_undeducted_absence_can_have_makeup(self):
        original = self.session(days=-1, status=Status.ABSENT)
        self.post_makeup(original)
        self.assertTrue(LessonSession.objects.filter(makeup_for=original).exists())

    def test_completing_makeup_deducts_exactly_once(self):
        original = self.session(days=-1, status=Status.CANCELLED)
        start = self.remaining()
        self.post_makeup(original)
        makeup = LessonSession.objects.get(makeup_for=original)
        self.assertEqual(self.remaining(), start)  # 등록만으로는 차감 없음
        self.client.post(reverse("lessons:session_complete", args=[makeup.pk]))
        self.client.post(reverse("lessons:session_complete", args=[makeup.pk]))  # 두 번 눌러도
        self.assertEqual(self.remaining(), start - 1)
        original.refresh_from_db()
        self.assertEqual(original.status, Status.CANCELLED)  # 원래 회차는 차감되지 않은 채 그대로

    def test_invalid_time_and_owner_isolation(self):
        original = self.session(status=Status.CANCELLED)
        r = self.post_makeup(original, start_time="17:00", end_time="16:00")
        self.assertEqual(r.status_code, 200)
        self.assertIn("end_time", r.context["form"].errors)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("lessons:session_makeup", args=[original.pk])).status_code, 404)
        self.assertEqual(self.post_makeup(original).status_code, 404)
        self.assertFalse(LessonSession.objects.filter(makeup_for=original).exists())

    def test_ended_enrollment_or_archived_student_refused(self):
        original = self.session(status=Status.CANCELLED)
        Enrollment.objects.filter(pk=self.enrollment.pk).update(status=Enrollment.Status.ENDED)
        r = self.post_makeup(original)
        self.assertContains(self.client.get(r["Location"]), "종료된 레슨권")
        Enrollment.objects.filter(pk=self.enrollment.pk).update(status=Enrollment.Status.ACTIVE)
        Student.objects.filter(pk=self.student.pk).update(is_active=False)
        r = self.post_makeup(original)
        self.assertContains(self.client.get(r["Location"]), "보관된 수강생")
        self.assertFalse(LessonSession.objects.filter(makeup_for=original).exists())

    def test_deleting_makeup_makes_original_need_makeup_again(self):
        original = self.session(days=-1, status=Status.CANCELLED)
        self.post_makeup(original)
        makeup = LessonSession.objects.get(makeup_for=original)
        self.client.post(reverse("lessons:session_delete", args=[makeup.pk]))
        r = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertEqual([s.pk for s in r.context["makeup_needed"]], [original.pk])
