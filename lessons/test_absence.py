"""UC-10 결석 처리 (#17) 테스트."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from lessons.models import Enrollment, LessonSession
from students.models import Student

User = get_user_model()
Status = LessonSession.Status


class AbsenceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(email="a@example.com", password="Lemit-pass-9876", name="강사A")
        self.other = User.objects.create_user(email="b@example.com", password="Lemit-pass-9876", name="강사B")
        self.student = Student.objects.create(teacher=self.teacher, name="학생")
        self.enrollment = Enrollment.objects.create(
            student=self.student, title="피아노", total_count=2, start_date=datetime.date(2026, 11, 1)
        )
        self.client.force_login(self.teacher)

    def session(self, days=-1, status=Status.SCHEDULED, **extra):
        day = timezone.localdate() + datetime.timedelta(days=days)
        start = timezone.make_aware(datetime.datetime.combine(day, datetime.time(15)))
        return LessonSession.objects.create(
            enrollment=self.enrollment,
            start_at=start,
            end_at=start + datetime.timedelta(hours=1),
            status=status,
            **extra,
        )

    def remaining(self):
        return Enrollment.objects.with_usage().get(pk=self.enrollment.pk).remaining_count

    def absent(self, s, **data):
        return self.client.post(reverse("lessons:session_absent", args=[s.pk]), data, follow=True)

    def test_choice_is_required_and_nothing_saved_without_it(self):
        s = self.session()
        for data in ({}, {"reason": "감기"}, {"deducted": ""}, {"deducted": "maybe"}):
            with self.subTest(data=data):
                r = self.client.post(reverse("lessons:session_absent", args=[s.pk]), data)
                self.assertEqual(r.status_code, 400)
                self.assertContains(r, "차감 여부를", status_code=400)
                self.assertContains(r, 'id="absent-box" open', status_code=400)  # 폼을 펼친 채로 오류 표시
                s.refresh_from_db()
                self.assertEqual((s.status, s.absence_deducted, s.reason), (Status.SCHEDULED, False, ""))
        # 처음 화면에서는 아무 것도 미리 골라져 있지 않다 (기본값 없음)
        page = self.client.get(reverse("lessons:session_detail", args=[s.pk])).content.decode()
        self.assertIn('name="deducted" value="yes"', page)
        self.assertNotIn("checked", page[page.index('id="absent-box"') : page.index("결석으로 기록")])

    def test_deducted_absence_uses_one_count_and_cannot_have_makeup(self):
        s = self.session()
        before = self.remaining()
        r = self.absent(s, deducted="yes", reason="당일 취소")
        self.assertContains(r, "결석(차감)으로 처리했습니다")
        s.refresh_from_db()
        self.assertEqual((s.status, s.absence_deducted, s.reason), (Status.ABSENT, True, "당일 취소"))
        self.assertEqual(self.remaining(), before - 1)
        self.assertNotContains(r, "보강 필요")
        self.assertNotContains(r, reverse("lessons:session_makeup", args=[s.pk]))
        m = self.client.get(reverse("lessons:session_makeup", args=[s.pk]), follow=True)
        self.assertContains(m, "차감한 결석에는 보강을 등록할 수 없습니다")

    def test_undeducted_absence_keeps_count_and_leads_to_makeup(self):
        s = self.session()
        before = self.remaining()
        r = self.absent(s, deducted="no")
        self.assertContains(r, "결석(미차감)으로 처리했습니다")
        s.refresh_from_db()
        self.assertEqual((s.status, s.absence_deducted), (Status.ABSENT, False))
        self.assertEqual(self.remaining(), before)
        self.assertContains(r, "보강 필요")
        self.assertContains(r, reverse("lessons:session_makeup", args=[s.pk]))
        # 보강 등록 → 보강 완료 시 1회만 차감 (UC-09로 이어짐)
        day = (timezone.localdate() + datetime.timedelta(days=2)).isoformat()
        self.client.post(
            reverse("lessons:session_makeup", args=[s.pk]), {"date": day, "start_time": "16:00", "end_time": "17:00"}
        )
        makeup = LessonSession.objects.get(makeup_for=s)
        self.client.post(reverse("lessons:session_complete", args=[makeup.pk]))
        self.assertEqual(self.remaining(), before - 1)
        detail = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertEqual(detail.context["makeup_needed"], [])

    def test_overflow_warning_when_deducting_beyond_total(self):
        self.session(days=-3, status=Status.COMPLETED, completed_at=timezone.now())
        self.session(days=-2, status=Status.COMPLETED, completed_at=timezone.now())
        r = self.absent(self.session(), deducted="yes")
        self.assertContains(r, "회차 초과")
        self.assertEqual(self.remaining(), -1)

    def test_only_scheduled_and_double_click_once(self):
        done = self.session(days=-2, status=Status.COMPLETED, completed_at=timezone.now())
        r = self.absent(done, deducted="no")
        self.assertContains(r, "예정 상태인 회차만 결석 처리할 수 있습니다")
        done.refresh_from_db()
        self.assertEqual(done.status, Status.COMPLETED)
        s = self.session()
        self.absent(s, deducted="no", reason="첫 번째")
        self.absent(s, deducted="yes", reason="두 번째")  # 이미 결석 → 바뀌지 않음 (차감도 추가되지 않음)
        s.refresh_from_db()
        self.assertEqual((s.absence_deducted, s.reason), (False, "첫 번째"))

    def test_unabsent_restores_scheduled_and_deduction(self):
        s = self.session()
        before = self.remaining()
        self.absent(s, deducted="yes", reason="실수")
        self.assertEqual(self.remaining(), before - 1)
        r = self.client.post(reverse("lessons:session_unabsent", args=[s.pk]), follow=True)
        self.assertContains(r, "결석을 취소하고 예정으로 되돌렸습니다")
        s.refresh_from_db()
        self.assertEqual((s.status, s.absence_deducted, s.reason), (Status.SCHEDULED, False, ""))
        self.assertEqual(self.remaining(), before)

    def test_unabsent_refused_when_makeup_linked(self):
        s = self.session(status=Status.ABSENT)
        start = timezone.now() + datetime.timedelta(days=3)
        LessonSession.objects.create(
            enrollment=self.enrollment, start_at=start, end_at=start + datetime.timedelta(hours=1), makeup_for=s
        )
        r = self.client.post(reverse("lessons:session_unabsent", args=[s.pk]), follow=True)
        self.assertContains(r, "보강이 연결된 결석은 취소할 수 없습니다")
        s.refresh_from_db()
        self.assertEqual(s.status, Status.ABSENT)

    def test_post_only_and_owner_isolation(self):
        s = self.session()
        self.assertEqual(self.client.get(reverse("lessons:session_absent", args=[s.pk])).status_code, 405)
        self.client.force_login(self.other)
        for name, data in (("lessons:session_absent", {"deducted": "yes"}), ("lessons:session_unabsent", {})):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name, args=[s.pk]), data).status_code, 404)
        s.refresh_from_db()
        self.assertEqual(s.status, Status.SCHEDULED)

    def test_schedule_and_detail_show_absence_by_shape_and_text(self):
        self.session(days=0, status=Status.ABSENT, absence_deducted=True)
        html = self.client.get(reverse("lessons:schedule")).content.decode()
        self.assertIn("is-absent", html)  # 취소선 모양 (SCREENS §4)
        self.assertIn("결석·차감", html)
