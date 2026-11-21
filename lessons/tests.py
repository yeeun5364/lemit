"""UC-03 레슨권 등록·종료, 잔여 회차 계산(ERD 2-2), 강사 A/B 격리 테스트."""

import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from lessons.models import Enrollment, Label, LessonSession
from students.models import Student

User = get_user_model()
PASSWORD = "lesson-time-2026!"


def make_teacher(email, name):
    return User.objects.create_user(email=email, password=PASSWORD, name=name)


def make_pass(student, title="피아노 개인 레슨", total=8, **kwargs):
    return Enrollment.objects.create(
        student=student, title=title, total_count=total, start_date=datetime.date(2026, 11, 1), **kwargs
    )


def add_session(enrollment, status=LessonSession.Status.SCHEDULED, deducted=False, day=1):
    start = timezone.make_aware(datetime.datetime(2026, 11, day, 17, 0))
    return LessonSession.objects.create(
        enrollment=enrollment,
        start_at=start,
        end_at=start + datetime.timedelta(hours=1),
        status=status,
        absence_deducted=deducted,
    )


def usage(enrollment):
    return Enrollment.objects.with_usage().get(pk=enrollment.pk)


class UsageCalculationTests(TestCase):
    """잔여 = 총 − (완료 + 차감한 결석). 예정·휴강·미차감 결석은 차감하지 않는다."""

    def setUp(self):
        teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=teacher, name="박서연")
        self.enrollment = make_pass(self.student, total=8)

    def test_new_pass_has_full_remaining(self):
        e = usage(self.enrollment)
        self.assertEqual((e.used_count, e.remaining_count), (0, 8))

    def test_only_completed_and_deducted_absence_count(self):
        S = LessonSession.Status
        add_session(self.enrollment, S.COMPLETED, day=1)
        add_session(self.enrollment, S.COMPLETED, day=2)
        add_session(self.enrollment, S.ABSENT, deducted=True, day=3)
        add_session(self.enrollment, S.ABSENT, deducted=False, day=4)
        add_session(self.enrollment, S.CANCELLED, day=5)
        add_session(self.enrollment, S.SCHEDULED, day=6)
        e = usage(self.enrollment)
        self.assertEqual((e.used_count, e.remaining_count), (3, 5))

    def test_remaining_can_go_negative_and_pass_is_not_auto_ended(self):
        e = make_pass(self.student, title="2회권", total=2)
        for day in (1, 2, 3):
            add_session(e, LessonSession.Status.COMPLETED, day=day)
        e = usage(e)
        self.assertEqual(e.remaining_count, -1)
        self.assertEqual(e.status, Enrollment.Status.ACTIVE)  # 자동 종료하지 않음 (#2)
        self.assertIn(e, Enrollment.objects.exhausted())

    def test_counts_are_per_pass(self):
        other = make_pass(self.student, title="바이올린", total=4)
        add_session(self.enrollment, LessonSession.Status.COMPLETED)
        self.assertEqual(usage(other).used_count, 0)
        self.assertEqual(usage(self.enrollment).used_count, 1)


class ModelConstraintTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")

    def test_total_count_must_be_at_least_one_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_pass(self.student, total=0)

    def test_session_end_must_be_after_start(self):
        e = make_pass(self.student)
        start = timezone.now()
        with self.assertRaises(IntegrityError), transaction.atomic():
            LessonSession.objects.create(enrollment=e, start_at=start, end_at=start)

    def test_deducted_flag_only_on_absent(self):
        e = make_pass(self.student)
        with self.assertRaises(IntegrityError), transaction.atomic():
            add_session(e, LessonSession.Status.COMPLETED, deducted=True)

    def test_label_must_belong_to_same_teacher(self):
        other = make_teacher("b@example.com", "B강사")
        foreign = Label.objects.create(teacher=other, name="피아노", color=Label.Color.BLUE)
        e = Enrollment(student=self.student, title="x", total_count=1, start_date=datetime.date.today(), label=foreign)
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_makeup_only_for_cancelled_or_undeducted_absence(self):
        e = make_pass(self.student)
        S = LessonSession.Status
        for original, ok in (
            (add_session(e, S.CANCELLED, day=1), True),
            (add_session(e, S.ABSENT, deducted=False, day=2), True),
            (add_session(e, S.ABSENT, deducted=True, day=3), False),  # 차감한 결석은 보강 불가
            (add_session(e, S.COMPLETED, day=4), False),
        ):
            start = timezone.make_aware(datetime.datetime(2026, 12, 1, 17, 0))
            makeup = LessonSession(
                enrollment=e, start_at=start, end_at=start + datetime.timedelta(hours=1), makeup_for=original
            )
            with self.subTest(status=original.status, deducted=original.absence_deducted):
                if ok:
                    makeup.full_clean()
                else:
                    with self.assertRaises(ValidationError):
                        makeup.full_clean()


class EnrollmentCreateViewTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])

    def post(self, **overrides):
        data = {"title": "피아노 개인 레슨", "total_count": "8", "start_date": "2026-11-20", **overrides}
        return self.client.post(self.url, data)

    def test_url_matches_screen_spec(self):
        self.assertEqual(self.url, f"/students/{self.student.pk}/enrollments/new/")

    def test_get_shows_form_with_today_as_start(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "레슨권 추가")
        self.assertEqual(response.context["form"]["start_date"].value(), timezone.localdate())
        self.assertFalse(response.context["form"].has_exhausted)
        self.assertNotContains(response, "소진된 레슨권 종료")

    def test_create_pass_and_show_remaining_as_total(self):
        response = self.post()
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        e = Enrollment.objects.get()
        self.assertEqual((e.student, e.title, e.total_count, e.status), (self.student, "피아노 개인 레슨", 8, "active"))
        detail = self.client.get(response.url)
        self.assertContains(detail, "피아노 개인 레슨")
        self.assertEqual(detail.context["active_enrollments"][0].remaining_count, 8)

    def test_total_count_below_one_is_rejected(self):
        for value in ("0", "-1", ""):
            with self.subTest(value=value):
                response = self.post(total_count=value)
                self.assertEqual(response.status_code, 200)
                self.assertIn("total_count", response.context["form"].errors)
        self.assertFalse(Enrollment.objects.exists())

    def test_blank_title_is_rejected(self):
        response = self.post(title="   ")
        self.assertIn("title", response.context["form"].errors)

    def test_student_cannot_be_changed_by_post(self):
        other_student = Student.objects.create(teacher=self.teacher, name="다른 학생")
        self.post(student=str(other_student.pk))
        self.assertEqual(Enrollment.objects.get().student, self.student)

    def test_multiple_active_passes_allowed(self):
        self.post(title="피아노")
        self.post(title="작곡")
        self.assertEqual(Enrollment.objects.filter(student=self.student).active().count(), 2)

    def test_archived_student_cannot_get_new_pass(self):
        self.student.is_active = False
        self.student.save()
        response = self.post()
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.assertFalse(Enrollment.objects.exists())


class ExhaustedPassProposalTests(TestCase):
    """소진된 진행 중 레슨권이 있으면 새 레슨권 등록 때 종료를 제안한다 (기본 체크, 강사가 해제 가능)."""

    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])
        self.used_up = make_pass(self.student, title="이전 4회권", total=4)
        for day in range(1, 5):
            add_session(self.used_up, LessonSession.Status.COMPLETED, day=day)
        self.still_left = make_pass(self.student, title="남은 레슨권", total=8)

    def data(self, **extra):
        return {"title": "새 8회권", "total_count": "8", "start_date": "2026-11-20", **extra}

    def test_form_offers_only_exhausted_active_pass_checked_by_default(self):
        form = self.client.get(self.url).context["form"]
        field = form.fields["end_exhausted"]
        self.assertEqual(list(field.queryset), [self.used_up])
        self.assertEqual(field.initial, [self.used_up])
        self.assertContains(self.client.get(self.url), "이전 4회권")

    def test_checked_pass_is_ended(self):
        self.client.post(self.url, self.data(end_exhausted=[self.used_up.pk]))
        self.used_up.refresh_from_db()
        self.still_left.refresh_from_db()
        self.assertEqual(self.used_up.status, Enrollment.Status.ENDED)
        self.assertEqual(self.still_left.status, Enrollment.Status.ACTIVE)
        self.assertTrue(Enrollment.objects.filter(title="새 8회권").exists())

    def test_unchecked_pass_stays_active(self):
        self.client.post(self.url, self.data())
        self.used_up.refresh_from_db()
        self.assertEqual(self.used_up.status, Enrollment.Status.ACTIVE)

    def test_cannot_end_non_exhausted_pass_by_tampered_post(self):
        response = self.client.post(self.url, self.data(end_exhausted=[self.still_left.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("end_exhausted", response.context["form"].errors)
        self.still_left.refresh_from_db()
        self.assertEqual(self.still_left.status, Enrollment.Status.ACTIVE)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())


class EnrollmentEndTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.enrollment = make_pass(self.student)
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_end", args=[self.enrollment.pk])

    def test_end_pass_even_with_remaining(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, Enrollment.Status.ENDED)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_detail_separates_active_and_ended(self):
        make_pass(self.student, title="작곡 레슨")
        self.client.post(self.url)
        response = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertEqual([e.title for e in response.context["active_enrollments"]], ["작곡 레슨"])
        self.assertEqual([e.title for e in response.context["ended_enrollments"]], ["피아노 개인 레슨"])
        self.assertContains(response, "종료된 레슨권 1개")


class TeacherIsolationTests(TestCase):
    """강사 B는 강사 A의 수강생에게 레슨권을 추가하거나 A의 레슨권을 종료·조회할 수 없다 (404)."""

    def setUp(self):
        a = make_teacher("a@example.com", "A강사")
        self.b = make_teacher("b@example.com", "B강사")
        self.a_student = Student.objects.create(teacher=a, name="A의 학생")
        self.a_pass = make_pass(self.a_student)
        self.client.force_login(self.b)

    def test_cannot_add_pass_to_other_teachers_student(self):
        url = reverse("lessons:enrollment_create", args=[self.a_student.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        data = {"title": "x", "total_count": "8", "start_date": "2026-11-20"}
        self.assertEqual(self.client.post(url, data).status_code, 404)
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_cannot_end_other_teachers_pass(self):
        response = self.client.post(reverse("lessons:enrollment_end", args=[self.a_pass.pk]))
        self.assertEqual(response.status_code, 404)
        self.a_pass.refresh_from_db()
        self.assertEqual(self.a_pass.status, Enrollment.Status.ACTIVE)

    def test_cannot_end_other_teachers_exhausted_pass_via_proposal(self):
        """B가 자기 수강생 레슨권을 만들면서 A의 레슨권 번호를 '종료' 값으로 보내도 A의 레슨권은 바뀌지 않는다."""
        b_student = Student.objects.create(teacher=self.b, name="B의 학생")
        for day in range(1, 9):
            add_session(self.a_pass, LessonSession.Status.COMPLETED, day=day)
        url = reverse("lessons:enrollment_create", args=[b_student.pk])
        data = {"title": "x", "total_count": "8", "start_date": "2026-11-20", "end_exhausted": [self.a_pass.pk]}
        self.client.post(url, data)
        self.a_pass.refresh_from_db()
        self.assertEqual(self.a_pass.status, Enrollment.Status.ACTIVE)

    def test_login_required(self):
        self.client.logout()
        url = reverse("lessons:enrollment_create", args=[self.a_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse(settings.LOGIN_URL)))


# ---------------------------------------------------------------------------
# PR #55 Codex 리뷰 반영
# ---------------------------------------------------------------------------


class UsageJoinTests(TestCase):
    """회차 조건과 함께 써도 차감 회차가 부풀거나 빠지지 않는다 (서브쿼리 집계, PR #55 리뷰 1·2차)."""

    def setUp(self):
        teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=teacher, name="박서연")

    def test_join_after_usage_does_not_inflate(self):
        e = make_pass(self.student, total=3)
        add_session(e, LessonSession.Status.COMPLETED, day=1)
        for day in (2, 3, 4):
            add_session(e, LessonSession.Status.SCHEDULED, day=day)
        joined = Enrollment.objects.with_usage().filter(sessions__status="scheduled").distinct().get()
        self.assertEqual((joined.used_count, joined.remaining_count), (1, 2))

    def test_filter_before_usage_does_not_drop_deducted(self):
        """총 1회, 완료 1·예정 1: 예정 회차로 먼저 걸러도 완료 1회가 집계되어 소진으로 잡힌다."""
        e = make_pass(self.student, total=1)
        add_session(e, LessonSession.Status.COMPLETED, day=1)
        add_session(e, LessonSession.Status.SCHEDULED, day=2)
        pre = Enrollment.objects.filter(sessions__status="scheduled").with_usage().get()
        self.assertEqual((pre.used_count, pre.remaining_count), (1, 0))
        self.assertTrue(Enrollment.objects.filter(sessions__status="scheduled").exhausted().exists())

    def test_pass_without_sessions_counts_zero(self):
        e = make_pass(self.student, total=2)
        self.assertEqual(Enrollment.objects.with_usage().get(pk=e.pk).used_count, 0)


class EnrollmentCreateRaceTests(TestCase):
    """검증과 저장 사이에 상태가 바뀌거나, 조작·지난 선택이 오면 새 레슨권을 만들지 않는다."""

    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:enrollment_create", args=[self.student.pk])

    def data(self, **extra):
        return {"title": "새 8회권", "total_count": "8", "start_date": "2026-11-20", **extra}

    def used_up_pass(self):
        e = make_pass(self.student, title="이전 2회권", total=2)
        sessions = [add_session(e, LessonSession.Status.COMPLETED, day=d) for d in (1, 2)]
        return e, sessions

    def after_validation(self, change):
        """폼 검증 직후(저장 트랜잭션 전)에 change()를 실행해 다른 탭·관리자의 변경을 흉내 낸다."""
        from unittest import mock

        from lessons.forms import EnrollmentForm

        original = EnrollmentForm.is_valid

        def is_valid(form):
            ok = original(form)
            change()
            return ok

        return mock.patch.object(EnrollmentForm, "is_valid", is_valid)

    def test_tampered_choice_without_candidates_is_error_and_no_new_pass(self):
        other = make_teacher("b@example.com", "B강사")
        foreign = make_pass(Student.objects.create(teacher=other, name="B의 학생"))
        response = self.client.post(self.url, self.data(end_exhausted=[foreign.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("end_exhausted", response.context["form"].errors)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())

    def test_already_ended_choice_from_other_tab_is_error(self):
        e, _ = self.used_up_pass()
        Enrollment.objects.filter(pk=e.pk).update(status=Enrollment.Status.ENDED)  # 다른 탭에서 먼저 종료
        response = self.client.post(self.url, self.data(end_exhausted=[e.pk]))
        self.assertIn("end_exhausted", response.context["form"].errors)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())
        # 후보가 사라졌으므로 '소진 레슨권이 있습니다' 안내는 빼고 오류만 연결한다
        self.assertNotContains(response, "잔여 회차를 다 쓴 진행 중 레슨권이 있습니다")
        self.assertContains(response, 'aria-describedby="id_end_exhausted_error"')

    def test_pass_no_longer_exhausted_after_validation_is_not_ended(self):
        e, sessions = self.used_up_pass()

        def reopen():  # 관리자가 완료 1회를 예정으로 되돌림 → 잔여 1
            LessonSession.objects.filter(pk=sessions[0].pk).update(status=LessonSession.Status.SCHEDULED)

        with self.after_validation(reopen):
            response = self.client.post(self.url, self.data(end_exhausted=[e.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("end_exhausted", form.errors)
        # 최신 후보로 다시 만든 폼: 후보가 없으니 '소진 레슨권이 있습니다' 안내는 빠지고, 입력값은 유지
        self.assertFalse(form.has_exhausted)
        self.assertNotContains(response, "잔여 회차를 다 쓴 진행 중 레슨권이 있습니다")
        self.assertEqual(form["title"].value(), "새 8회권")
        e.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.ACTIVE)
        self.assertFalse(Enrollment.objects.filter(title="새 8회권").exists())

    def test_student_archived_after_validation_gets_no_pass(self):
        def archive():
            Student.objects.filter(pk=self.student.pk).update(is_active=False)

        with self.after_validation(archive):
            response = self.client.post(self.url, self.data())
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.assertFalse(Enrollment.objects.exists())

    def test_ended_count_and_updated_at(self):
        e, _ = self.used_up_pass()
        before = e.updated_at
        response = self.client.post(self.url, self.data(end_exhausted=[e.pk]), follow=True)
        self.assertContains(response, "소진된 레슨권 1개를 종료했습니다.")
        e.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.ENDED)
        self.assertGreater(e.updated_at, before)

    def test_error_is_linked_to_fieldset_for_screen_readers(self):
        e, _ = self.used_up_pass()
        Enrollment.objects.filter(pk=e.pk).update(status=Enrollment.Status.ENDED)
        make_pass(self.student, title="다른 소진", total=1)
        add_session(Enrollment.objects.get(title="다른 소진"), LessonSession.Status.COMPLETED, day=5)
        response = self.client.post(self.url, self.data(end_exhausted=[e.pk]))
        self.assertContains(response, 'aria-describedby="id_end_exhausted_helptext id_end_exhausted_error"')
        self.assertContains(response, 'id="id_end_exhausted_error"')


class MakeupOriginalGuardTests(TestCase):
    """보강이 연결된 원래 회차를 보강할 수 없는 상태로 바꾸면 거절한다 (중복 차감 방지)."""

    def test_cannot_deduct_absence_that_has_makeup(self):
        teacher = make_teacher("a@example.com", "A강사")
        e = make_pass(Student.objects.create(teacher=teacher, name="박서연"))
        original = add_session(e, LessonSession.Status.ABSENT, deducted=False, day=1)
        makeup = add_session(e, day=2)
        makeup.makeup_for = original
        makeup.full_clean()
        makeup.save()
        original.absence_deducted = True
        with self.assertRaises(ValidationError):
            original.full_clean()
        original.refresh_from_db()
        original.status = LessonSession.Status.COMPLETED
        with self.assertRaises(ValidationError):
            original.full_clean()

    def test_session_without_makeup_can_change_freely(self):
        teacher = make_teacher("a@example.com", "A강사")
        s = add_session(make_pass(Student.objects.create(teacher=teacher, name="박서연")), day=1)
        s.status = LessonSession.Status.ABSENT
        s.absence_deducted = True
        s.full_clean()


class AdminSessionDeleteTests(TestCase):
    """관리자 화면도 '예정' 회차만 삭제할 수 있고 일괄 삭제는 없다 (ERD §3 기록 보존)."""

    def setUp(self):
        admin_user = User.objects.create_superuser(email="admin@example.com", password=PASSWORD, name="운영자")
        teacher = make_teacher("a@example.com", "A강사")
        e = make_pass(Student.objects.create(teacher=teacher, name="박서연"))
        self.done = add_session(e, LessonSession.Status.COMPLETED, day=1)
        self.planned = add_session(e, day=2)
        self.client.force_login(admin_user)

    def test_completed_session_cannot_be_deleted(self):
        url = reverse("admin:lessons_lessonsession_delete", args=[self.done.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(LessonSession.objects.filter(pk=self.done.pk).exists())

    def test_scheduled_session_can_be_deleted(self):
        url = reverse("admin:lessons_lessonsession_delete", args=[self.planned.pk])
        self.client.post(url, {"post": "yes"})
        self.assertFalse(LessonSession.objects.filter(pk=self.planned.pk).exists())

    def test_bulk_delete_action_is_removed(self):
        response = self.client.get(reverse("admin:lessons_lessonsession_changelist"))
        self.assertNotContains(response, 'value="delete_selected"')


# ---------------------------------------------------------------------------
# PR #55 Codex 2차 리뷰 반영
# ---------------------------------------------------------------------------


class Round2Tests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.client.force_login(self.teacher)

    def test_makeup_cycle_is_rejected(self):
        e = make_pass(self.student)
        a = add_session(e, LessonSession.Status.CANCELLED, day=1)
        b = add_session(e, LessonSession.Status.CANCELLED, day=2)
        b.makeup_for = a
        b.full_clean()
        b.save()
        a.makeup_for = b
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_direct_end_of_already_ended_pass_reports_nothing(self):
        e = make_pass(self.student, status=Enrollment.Status.ENDED)
        response = self.client.post(reverse("lessons:enrollment_end", args=[e.pk]), follow=True)
        self.assertNotContains(response, "레슨권을 종료했습니다")

    def test_direct_end_updates_timestamp(self):
        e = make_pass(self.student)
        before = e.updated_at
        self.client.post(reverse("lessons:enrollment_end", args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.ENDED)
        self.assertGreater(e.updated_at, before)

    def test_student_edit_does_not_undo_concurrent_archive(self):
        """수정 화면을 연 뒤 다른 탭에서 보관하면, 수정 저장이 보관을 되돌리지 않는다."""
        from unittest import mock

        from students.forms import StudentForm

        original = StudentForm.is_valid

        def is_valid(form):
            ok = original(form)
            Student.objects.filter(pk=self.student.pk).update(is_active=False)  # 다른 탭에서 보관
            return ok

        data = {"name": "박서연2", "phone": "", "is_minor": "", "guardian_phone": "", "guardian_name": "", "memo": ""}
        with mock.patch.object(StudentForm, "is_valid", is_valid):
            self.client.post(reverse("students:edit", args=[self.student.pk]), data)
        self.student.refresh_from_db()
        self.assertEqual(self.student.name, "박서연2")
        self.assertFalse(self.student.is_active)


# ---------------------------------------------------------------------------
# UC-04 회차 등록·수정·삭제 (S06·S07)
# ---------------------------------------------------------------------------


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime(2026, 11, day, hour, minute))


def warnings_of(response):
    return [str(m) for m in response.context["messages"] if m.level_tag == "warning"]


class SessionCreateTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.enrollment = make_pass(self.student, total=8)
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:session_create")

    def post(self, follow=False, **overrides):
        data = {
            "enrollment": str(self.enrollment.pk),
            "date": "2026-11-21",
            "start_time": "16:00",
            "end_time": "17:00",
            "place": "A 레슨실",
            **overrides,
        }
        return self.client.post(self.url, data, follow=follow)

    def test_urls_match_screen_spec(self):
        self.assertEqual(self.url, "/sessions/new/")
        self.assertEqual(reverse("lessons:session_edit", args=[3]), "/sessions/3/edit/")

    def test_create_scheduled_session_in_seoul_time_and_redirect(self):
        response = self.post()
        s = LessonSession.objects.get()
        self.assertRedirects(response, reverse("lessons:session_detail", args=[s.pk]))
        self.assertEqual(s.status, LessonSession.Status.SCHEDULED)
        self.assertEqual((s.start_at, s.end_at), (at(21, 16), at(21, 17)))
        self.assertEqual(str(timezone.localtime(s.start_at).tzinfo), "Asia/Seoul")
        self.assertEqual(s.place, "A 레슨실")

    def test_status_cannot_be_set_by_post(self):
        self.post(status="completed")
        self.assertEqual(LessonSession.objects.get().status, LessonSession.Status.SCHEDULED)

    def test_end_must_be_after_start(self):
        for end in ("16:00", "15:30"):
            with self.subTest(end=end):
                response = self.post(end_time=end)
                self.assertEqual(response.status_code, 200)
                self.assertIn("end_time", response.context["form"].errors)
        self.assertFalse(LessonSession.objects.exists())

    def test_preselect_ignores_archived_student(self):
        self.student.is_active = False
        self.student.save()
        response = self.client.get(self.url, {"student": self.student.pk})
        self.assertIsNone(response.context["form"]["enrollment"].value())
        self.assertTrue(response.context["back_url"].startswith(reverse("lessons:schedule")))

    def test_preselect_from_student_detail(self):
        response = self.client.get(self.url, {"student": self.student.pk})
        self.assertEqual(response.context["form"]["enrollment"].value(), self.enrollment.pk)
        self.assertEqual(response.context["back_url"], reverse("students:detail", args=[self.student.pk]))

    def test_only_active_passes_of_active_students_are_choices(self):
        ended = make_pass(self.student, title="종료", status=Enrollment.Status.ENDED)
        archived = Student.objects.create(teacher=self.teacher, name="보관", is_active=False)
        archived_pass = make_pass(archived)
        choices = list(self.client.get(self.url).context["form"].fields["enrollment"].queryset)
        self.assertEqual(choices, [self.enrollment])
        for e in (ended, archived_pass):
            self.assertIn("enrollment", self.post(enrollment=str(e.pk)).context["form"].errors)

    def test_no_active_pass_shows_guidance(self):
        self.enrollment.status = Enrollment.Status.ENDED
        self.enrollment.save()
        self.assertContains(self.client.get(self.url), "진행 중인 레슨권이 없습니다")

    def test_success_message_without_warnings(self):
        response = self.post(follow=True)
        self.assertContains(response, "회차를 등록했습니다.")
        self.assertEqual(warnings_of(response), [])


class SessionWarningTests(TestCase):
    """겹침·잔여 초과는 저장을 막지 않고 저장 후 경고만 한다."""

    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.other_student = Student.objects.create(teacher=self.teacher, name="이도윤")
        self.enrollment = make_pass(self.student, total=8)
        self.other_pass = make_pass(self.other_student, title="바이올린")
        self.client.force_login(self.teacher)

    def create(self, enrollment=None, date="2026-11-21", start="16:00", end="17:00"):
        data = {"enrollment": (enrollment or self.enrollment).pk, "date": date, "start_time": start, "end_time": end}
        return self.client.post(reverse("lessons:session_create"), data, follow=True)

    def existing(self, enrollment, start, end, status=LessonSession.Status.SCHEDULED):
        return LessonSession.objects.create(enrollment=enrollment, start_at=start, end_at=end, status=status)

    def test_overlap_with_my_other_session_warns_but_saves(self):
        self.existing(self.other_pass, at(21, 16, 30), at(21, 17, 30))
        response = self.create()
        self.assertEqual(LessonSession.objects.count(), 2)
        self.assertEqual(len(warnings_of(response)), 1)
        self.assertIn("이도윤 회차가 있습니다", warnings_of(response)[0])

    def test_adjacent_cancelled_and_other_teacher_sessions_do_not_warn(self):
        self.existing(self.other_pass, at(21, 15), at(21, 16))  # 끝과 시작이 맞닿음
        self.existing(self.other_pass, at(21, 16), at(21, 17), LessonSession.Status.CANCELLED)
        b = make_teacher("b@example.com", "B강사")
        b_pass = make_pass(Student.objects.create(teacher=b, name="B의 학생"))
        self.existing(b_pass, at(21, 16), at(21, 17))
        self.assertEqual(warnings_of(self.create()), [])

    def test_over_remaining_warns_from_first_excess(self):
        """잔여 3회: 예정 3회까지는 경고 없음, 4회째부터 경고 (UC-04 예)."""
        small = make_pass(self.student, title="3회권", total=3)
        for day in (22, 23, 24):
            self.assertEqual(warnings_of(self.create(small, date=f"2026-11-{day}")), [])
        response = self.create(small, date="2026-11-25")
        self.assertEqual(len(warnings_of(response)), 1)
        self.assertIn("잔여 3회, 예정 4회", warnings_of(response)[0])

    def test_completed_sessions_reduce_remaining(self):
        small = make_pass(self.student, title="2회권", total=2)
        self.existing(small, at(1, 10), at(1, 11), LessonSession.Status.COMPLETED)
        self.assertEqual(warnings_of(self.create(small, date="2026-11-22")), [])  # 잔여 1, 예정 1
        self.assertIn("잔여 1회, 예정 2회", warnings_of(self.create(small, date="2026-11-23"))[0])

    def test_edit_counts_the_session_once(self):
        small = make_pass(self.student, title="1회권", total=1)
        s = self.existing(small, at(22, 10), at(22, 11))
        data = {"enrollment": small.pk, "date": "2026-11-22", "start_time": "11:00", "end_time": "12:00"}
        response = self.client.post(reverse("lessons:session_edit", args=[s.pk]), data, follow=True)
        self.assertEqual(warnings_of(response), [])
        self.assertContains(response, "회차를 수정했습니다.")


class SessionEditDeleteTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.enrollment = make_pass(self.student)
        self.session = add_session(self.enrollment, day=21)
        self.client.force_login(self.teacher)

    def test_detail_shows_session_and_pass(self):
        response = self.client.get(reverse("lessons:session_detail", args=[self.session.pk]))
        self.assertContains(response, "박서연 · 피아노 개인 레슨")
        self.assertContains(response, "2026년 11월 21일")
        self.assertContains(response, "17:00 – 18:00")
        self.assertTrue(response.context["can_delete"])

    def test_edit_prefills_local_date_and_time(self):
        form = self.client.get(reverse("lessons:session_edit", args=[self.session.pk])).context["form"]
        self.assertEqual(str(form["date"].value()), "2026-11-21")
        self.assertEqual(form["start_time"].value().strftime("%H:%M"), "17:00")

    def test_edit_keeps_ended_pass_selectable_for_its_session(self):
        self.enrollment.status = Enrollment.Status.ENDED
        self.enrollment.save()
        form = self.client.get(reverse("lessons:session_edit", args=[self.session.pk])).context["form"]
        self.assertIn(self.enrollment, form.fields["enrollment"].queryset)

    def test_only_scheduled_can_be_edited_or_deleted(self):
        self.session.status = LessonSession.Status.COMPLETED
        self.session.save()
        detail = reverse("lessons:session_detail", args=[self.session.pk])
        self.assertRedirects(self.client.get(reverse("lessons:session_edit", args=[self.session.pk])), detail)
        self.assertRedirects(self.client.post(reverse("lessons:session_delete", args=[self.session.pk])), detail)
        self.assertTrue(LessonSession.objects.filter(pk=self.session.pk).exists())

    def test_delete_scheduled(self):
        response = self.client.post(reverse("lessons:session_delete", args=[self.session.pk]))
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.assertFalse(LessonSession.objects.exists())

    def test_delete_requires_post(self):
        self.assertEqual(self.client.get(reverse("lessons:session_delete", args=[self.session.pk])).status_code, 405)

    def test_student_detail_lists_sessions_grouped_by_pass(self):
        add_session(self.enrollment, LessonSession.Status.COMPLETED, day=14)
        response = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertEqual([s.pk for s in response.context["sessions"]][0], self.session.pk)  # 최신순
        self.assertContains(response, "회차 이력")
        self.assertContains(response, f"{reverse('lessons:session_create')}?student={self.student.pk}")


class ArchiveDeletesScheduledSessionsTests(TestCase):
    def test_archive_deletes_only_scheduled_sessions(self):
        teacher = make_teacher("a@example.com", "A강사")
        student = Student.objects.create(teacher=teacher, name="박서연")
        e = make_pass(student)
        add_session(e, LessonSession.Status.SCHEDULED, day=21)
        add_session(e, LessonSession.Status.SCHEDULED, day=22)
        done = add_session(e, LessonSession.Status.COMPLETED, day=14)
        self.client.force_login(teacher)
        detail = self.client.get(reverse("students:detail", args=[student.pk]))
        self.assertContains(detail, "예정 회차 2개가 삭제됩니다.")
        response = self.client.post(reverse("students:archive", args=[student.pk]), follow=True)
        self.assertContains(response, "남은 예정 회차 2개를 삭제했습니다.")
        self.assertEqual(list(LessonSession.objects.all()), [done])


class SessionIsolationTests(TestCase):
    """강사 B는 A의 회차를 보거나 고치거나 지울 수 없고, A의 레슨권으로 회차를 만들 수 없다."""

    def setUp(self):
        a = make_teacher("a@example.com", "A강사")
        self.b = make_teacher("b@example.com", "B강사")
        self.a_pass = make_pass(Student.objects.create(teacher=a, name="A의 학생"))
        self.a_session = add_session(self.a_pass, day=21)
        self.b_pass = make_pass(Student.objects.create(teacher=self.b, name="B의 학생"))
        self.client.force_login(self.b)

    def test_cannot_see_edit_delete_other_teachers_session(self):
        pk = self.a_session.pk
        self.assertEqual(self.client.get(reverse("lessons:session_detail", args=[pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("lessons:session_edit", args=[pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("lessons:session_delete", args=[pk])).status_code, 404)
        self.assertTrue(LessonSession.objects.filter(pk=pk).exists())

    def test_cannot_create_with_other_teachers_pass(self):
        data = {"enrollment": self.a_pass.pk, "date": "2026-11-21", "start_time": "16:00", "end_time": "17:00"}
        response = self.client.post(reverse("lessons:session_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("enrollment", response.context["form"].errors)
        self.assertEqual(LessonSession.objects.count(), 1)

    def test_cannot_move_own_session_to_other_teachers_pass(self):
        mine = add_session(self.b_pass, day=22)
        data = {"enrollment": self.a_pass.pk, "date": "2026-11-22", "start_time": "16:00", "end_time": "17:00"}
        response = self.client.post(reverse("lessons:session_edit", args=[mine.pk]), data)
        self.assertIn("enrollment", response.context["form"].errors)
        mine.refresh_from_db()
        self.assertEqual(mine.enrollment, self.b_pass)

    def test_preselect_ignores_other_teachers_ids(self):
        response = self.client.get(reverse("lessons:session_create"), {"enrollment": self.a_pass.pk})
        self.assertIsNone(response.context["form"]["enrollment"].value())
        response = self.client.get(reverse("lessons:session_create"), {"student": self.a_pass.student_id})
        self.assertIsNone(response.context["form"]["enrollment"].value())


# ---------------------------------------------------------------------------
# UC-04 회차 저장의 수강생 행 잠금·재확인 (ERD 동시 변경 규칙)
# ---------------------------------------------------------------------------


def after_session_validation(change):
    """SessionForm 검증 직후(잠금 트랜잭션 전)에 change()를 실행해 다른 탭·요청의 변경을 흉내 낸다."""
    from unittest import mock

    from lessons.forms import SessionForm

    original = SessionForm.is_valid

    def is_valid(form):
        ok = original(form)
        change()
        return ok

    return mock.patch.object(SessionForm, "is_valid", is_valid)


class SessionSaveRecheckTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.enrollment = make_pass(self.student)
        self.client.force_login(self.teacher)

    def data(self, enrollment=None, start="16:00", end="17:00"):
        return {
            "enrollment": (enrollment or self.enrollment).pk,
            "date": "2026-11-21",
            "start_time": start,
            "end_time": end,
            "place": "",
        }

    # --- 등록 ---
    def test_create_rejected_if_student_archived_after_validation(self):
        def archive():
            Student.objects.filter(pk=self.student.pk).update(is_active=False)

        with after_session_validation(archive):
            response = self.client.post(reverse("lessons:session_create"), self.data())
        self.assertEqual(response.status_code, 200)
        self.assertIn("보관된 수강생", str(response.context["form"].non_field_errors()))
        self.assertFalse(LessonSession.objects.exists())

    def test_create_rejected_if_pass_ended_after_validation(self):
        def end():
            Enrollment.objects.filter(pk=self.enrollment.pk).update(status=Enrollment.Status.ENDED)

        with after_session_validation(end):
            response = self.client.post(reverse("lessons:session_create"), self.data())
        self.assertIn("종료된 레슨권", str(response.context["form"].non_field_errors()))
        self.assertFalse(LessonSession.objects.exists())

    # --- 수정 ---
    def test_edit_rejected_if_session_completed_after_validation(self):
        s = add_session(self.enrollment, day=21)

        def complete():
            LessonSession.objects.filter(pk=s.pk).update(status=LessonSession.Status.COMPLETED)

        with after_session_validation(complete):
            response = self.client.post(
                reverse("lessons:session_edit", args=[s.pk]), self.data(start="09:00", end="10:00")
            )
        self.assertRedirects(response, reverse("lessons:session_detail", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.status, LessonSession.Status.COMPLETED)
        self.assertEqual(timezone.localtime(s.start_at).hour, 17)  # 시간도 그대로

    def test_edit_of_session_deleted_meanwhile_goes_to_student(self):
        s = add_session(self.enrollment, day=21)

        def delete():
            LessonSession.objects.filter(pk=s.pk).delete()

        with after_session_validation(delete):
            response = self.client.post(reverse("lessons:session_edit", args=[s.pk]), self.data())
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.assertFalse(LessonSession.objects.exists())

    def test_edit_keeps_fields_changed_elsewhere(self):
        """수정은 폼 필드만 저장하므로, 화면을 연 뒤 다른 곳에서 바뀐 사유는 덮어쓰지 않는다."""
        s = add_session(self.enrollment, day=21)

        def set_reason():
            LessonSession.objects.filter(pk=s.pk).update(reason="다른 탭에서 남긴 메모")

        with after_session_validation(set_reason):
            self.client.post(reverse("lessons:session_edit", args=[s.pk]), self.data(start="09:00", end="10:00"))
        s.refresh_from_db()
        self.assertEqual(timezone.localtime(s.start_at).hour, 9)
        self.assertEqual(s.reason, "다른 탭에서 남긴 메모")

    def test_edit_cannot_move_session_to_another_students_pass(self):
        other = make_pass(Student.objects.create(teacher=self.teacher, name="이도윤"), title="바이올린")
        s = add_session(self.enrollment, day=21)
        form = self.client.get(reverse("lessons:session_edit", args=[s.pk])).context["form"]
        self.assertNotIn(other, form.fields["enrollment"].queryset)
        response = self.client.post(reverse("lessons:session_edit", args=[s.pk]), self.data(enrollment=other))
        self.assertIn("enrollment", response.context["form"].errors)
        s.refresh_from_db()
        self.assertEqual(s.enrollment, self.enrollment)

    def test_edit_can_move_to_another_pass_of_same_student(self):
        second = make_pass(self.student, title="작곡")
        s = add_session(self.enrollment, day=21)
        self.client.post(reverse("lessons:session_edit", args=[s.pk]), self.data(enrollment=second))
        s.refresh_from_db()
        self.assertEqual(s.enrollment, second)

    def test_edit_cannot_move_to_pass_ended_after_validation(self):
        second = make_pass(self.student, title="작곡")
        s = add_session(self.enrollment, day=21)

        def end_second():
            Enrollment.objects.filter(pk=second.pk).update(status=Enrollment.Status.ENDED)

        with after_session_validation(end_second):
            self.client.post(reverse("lessons:session_edit", args=[s.pk]), self.data(enrollment=second))
        s.refresh_from_db()
        self.assertEqual(s.enrollment, self.enrollment)

    def test_edit_keeps_working_when_own_pass_was_ended(self):
        """지금 레슨권이 종료됐어도 그 회차의 시간은 고칠 수 있다 (레슨권을 바꾸지 않는 경우)."""
        s = add_session(self.enrollment, day=21)
        Enrollment.objects.filter(pk=self.enrollment.pk).update(status=Enrollment.Status.ENDED)
        self.client.post(reverse("lessons:session_edit", args=[s.pk]), self.data(start="09:00", end="10:00"))
        s.refresh_from_db()
        self.assertEqual(timezone.localtime(s.start_at).hour, 9)

    # --- 삭제 ---
    def test_delete_rejected_if_session_completed_after_check(self):
        from unittest import mock

        from lessons import views

        s = add_session(self.enrollment, day=21)
        original = views._owned_session

        def stale_fetch(request, pk):
            obj = original(request, pk)  # '예정'으로 읽은 뒤
            LessonSession.objects.filter(pk=pk).update(status=LessonSession.Status.COMPLETED)  # 다른 요청이 완료
            return obj

        with mock.patch.object(views, "_owned_session", stale_fetch):
            response = self.client.post(reverse("lessons:session_delete", args=[s.pk]))
        self.assertRedirects(response, reverse("lessons:session_detail", args=[s.pk]))
        self.assertTrue(LessonSession.objects.filter(pk=s.pk, status=LessonSession.Status.COMPLETED).exists())


class SessionArchiveConcurrencyTests(TransactionTestCase):
    """실제 두 DB 연결: 보관 트랜잭션이 수강생 행을 잡고 있는 동안 회차 등록은 기다렸다가, 보관이 끝나면 거절된다.

    잠금·재확인이 없으면 검증 시점에는 수강생이 '수강 중'이라 회차가 저장되고, 보관된 수강생에게 예정 회차가 남는다.
    """

    def test_create_waits_for_archive_lock_then_rejects(self):
        import threading

        from django.db import connection

        teacher = make_teacher("a@example.com", "A강사")
        student = Student.objects.create(teacher=teacher, name="박서연")
        enrollment = make_pass(student)
        self.client.force_login(teacher)
        locked, release = threading.Event(), threading.Event()

        def archive_in_other_connection():
            try:
                with transaction.atomic():
                    s = Student.objects.select_for_update().get(pk=student.pk)
                    s.is_active = False
                    s.save(update_fields=["is_active", "updated_at"])
                    locked.set()
                    release.wait(5)
            finally:
                connection.close()

        worker = threading.Thread(target=archive_in_other_connection)
        worker.start()
        self.assertTrue(locked.wait(5))
        # 보관은 아직 커밋 전: 폼 검증은 '수강 중'으로 통과하고, 저장 단계의 잠금에서 기다린다
        threading.Timer(0.5, release.set).start()
        data = {"enrollment": enrollment.pk, "date": "2026-11-21", "start_time": "16:00", "end_time": "17:00"}
        response = self.client.post(reverse("lessons:session_create"), data)
        worker.join(5)
        self.assertEqual(response.status_code, 200)
        self.assertIn("보관된 수강생", str(response.context["form"].non_field_errors()))
        self.assertFalse(LessonSession.objects.exists())


# ---------------------------------------------------------------------------
# UC-06 수업 완료 처리·완료 취소
# ---------------------------------------------------------------------------


class SessionCompleteTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        self.enrollment = make_pass(self.student, total=8)
        self.session = add_session(self.enrollment, day=19)
        self.client.force_login(self.teacher)

    def complete(self, session=None, follow=True):
        return self.client.post(reverse("lessons:session_complete", args=[(session or self.session).pk]), follow=follow)

    def uncomplete(self, session=None, follow=True):
        return self.client.post(
            reverse("lessons:session_uncomplete", args=[(session or self.session).pk]), follow=follow
        )

    def test_urls_match_screen_spec(self):
        self.assertEqual(reverse("lessons:session_complete", args=[5]), "/sessions/5/complete/")
        self.assertEqual(reverse("lessons:session_uncomplete", args=[5]), "/sessions/5/uncomplete/")

    def test_complete_deducts_one_and_shows_counts(self):
        """시연 흐름: 8회권에서 완료 1회 → 진행 1 / 잔여 7."""
        response = self.complete()
        self.assertRedirects(response, reverse("lessons:session_detail", args=[self.session.pk]))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, LessonSession.Status.COMPLETED)
        self.assertIsNotNone(self.session.completed_at)
        self.assertContains(response, "진행 1회 / 잔여 7회")
        e = usage(self.enrollment)
        self.assertEqual((e.used_count, e.remaining_count), (1, 7))
        detail = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertEqual(detail.context["active_enrollments"][0].remaining_count, 7)

    def test_uncomplete_restores_remaining(self):
        self.complete()
        response = self.uncomplete()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, LessonSession.Status.SCHEDULED)
        self.assertIsNone(self.session.completed_at)
        self.assertContains(response, "진행 0회 / 잔여 8회")
        self.assertEqual(usage(self.enrollment).remaining_count, 8)

    def test_archived_student_cannot_uncomplete(self):
        self.complete()
        Student.objects.filter(pk=self.student.pk).update(is_active=False)
        page = self.client.get(reverse("lessons:session_detail", args=[self.session.pk]))
        self.assertNotContains(page, reverse("lessons:session_uncomplete", args=[self.session.pk]))
        response = self.uncomplete()
        self.assertContains(response, "보관된 수강생의 회차는 완료 취소할 수 없습니다")
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, LessonSession.Status.COMPLETED)

    def test_double_complete_deducts_once(self):
        self.complete()
        response = self.complete()
        self.assertContains(response, "예정 상태인 회차만 완료 처리할 수 있습니다")
        self.assertEqual(usage(self.enrollment).used_count, 1)

    def test_only_completed_can_be_uncompleted(self):
        response = self.uncomplete()
        self.assertContains(response, "완료 상태인 회차만 완료 취소할 수 있습니다")
        for status in (LessonSession.Status.CANCELLED, LessonSession.Status.ABSENT):
            s = add_session(self.enrollment, status, day=20)
            self.uncomplete(s)
            self.complete(s)
            s.refresh_from_db()
            self.assertEqual(s.status, status)

    def test_complete_over_remaining_is_allowed_with_warning(self):
        one = make_pass(self.student, title="1회권", total=1)
        first, second = add_session(one, day=10), add_session(one, day=11)
        self.assertEqual(warnings_of(self.complete(first)), [])
        response = self.complete(second)
        second.refresh_from_db()
        self.assertEqual(second.status, LessonSession.Status.COMPLETED)
        self.assertIn("회차 초과", warnings_of(response)[0])
        self.assertEqual(usage(one).remaining_count, -1)

    def test_detail_buttons_follow_status(self):
        url = reverse("lessons:session_detail", args=[self.session.pk])
        self.assertContains(self.client.get(url), reverse("lessons:session_complete", args=[self.session.pk]))
        self.complete()
        page = self.client.get(url)
        self.assertContains(page, reverse("lessons:session_uncomplete", args=[self.session.pk]))
        self.assertNotContains(page, reverse("lessons:session_complete", args=[self.session.pk]))
        self.assertNotContains(page, reverse("lessons:session_edit", args=[self.session.pk]))  # 완료 회차는 수정 불가

    def test_future_session_asks_confirmation(self):
        future = add_session(self.enrollment, day=19)
        future.start_at = timezone.now() + datetime.timedelta(days=1)
        future.end_at = future.start_at + datetime.timedelta(hours=1)
        future.save()
        page = self.client.get(reverse("lessons:session_detail", args=[future.pk]))
        self.assertContains(page, "아직 시작 전인 수업입니다")
        past = self.client.get(reverse("lessons:session_detail", args=[self.session.pk]))  # 2026-11-19 (과거)
        self.assertNotContains(past, "아직 시작 전인 수업입니다")
        self.complete(future)  # 서버는 허용 (확인은 화면에서)
        future.refresh_from_db()
        self.assertEqual(future.status, LessonSession.Status.COMPLETED)

    def test_requires_post(self):
        for name in ("lessons:session_complete", "lessons:session_uncomplete"):
            self.assertEqual(self.client.get(reverse(name, args=[self.session.pk])).status_code, 405)

    def test_completed_session_shows_check_in_history(self):
        self.complete()
        page = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertContains(page, "session-row is-completed")

    def test_session_deleted_meanwhile_redirects_to_student(self):
        from unittest import mock

        from lessons import views

        original = views._owned_session

        def fetch_then_delete(request, pk):
            obj = original(request, pk)
            LessonSession.objects.filter(pk=pk).delete()  # 다른 탭에서 삭제
            return obj

        with mock.patch.object(views, "_owned_session", fetch_then_delete):
            response = self.client.post(reverse("lessons:session_complete", args=[self.session.pk]))
        self.assertRedirects(response, reverse("students:detail", args=[self.student.pk]))
        self.assertEqual(usage(self.enrollment).used_count, 0)


class SessionCompleteIsolationTests(TestCase):
    def test_other_teacher_cannot_complete_or_uncomplete(self):
        a = make_teacher("a@example.com", "A강사")
        b = make_teacher("b@example.com", "B강사")
        e = make_pass(Student.objects.create(teacher=a, name="A의 학생"))
        planned = add_session(e, day=1)
        done = add_session(e, LessonSession.Status.COMPLETED, day=2)
        self.client.force_login(b)
        self.assertEqual(self.client.post(reverse("lessons:session_complete", args=[planned.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("lessons:session_uncomplete", args=[done.pk])).status_code, 404)
        planned.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual((planned.status, done.status), ("scheduled", "completed"))


class SessionCompleteConcurrencyTests(TransactionTestCase):
    """실제 두 DB 연결: 다른 연결이 수강생 행을 잡고 같은 회차를 완료한 뒤 커밋하면,
    기다리던 완료 요청은 다시 차감하지 않는다."""

    def test_concurrent_complete_deducts_once(self):
        import threading

        from django.db import connection

        teacher = make_teacher("a@example.com", "A강사")
        student = Student.objects.create(teacher=teacher, name="박서연")
        e = make_pass(student, total=8)
        s = add_session(e, day=19)
        self.client.force_login(teacher)
        locked, release = threading.Event(), threading.Event()

        def complete_in_other_connection():
            try:
                with transaction.atomic():
                    Student.objects.select_for_update().get(pk=student.pk)
                    LessonSession.objects.filter(pk=s.pk).update(status="completed", completed_at=timezone.now())
                    locked.set()
                    release.wait(5)
            finally:
                connection.close()

        worker = threading.Thread(target=complete_in_other_connection)
        worker.start()
        self.assertTrue(locked.wait(5))
        threading.Timer(0.5, release.set).start()
        response = self.client.post(reverse("lessons:session_complete", args=[s.pk]), follow=True)
        worker.join(5)
        self.assertContains(response, "예정 상태인 회차만 완료 처리할 수 있습니다")
        self.assertEqual(Enrollment.objects.with_usage().get(pk=e.pk).used_count, 1)


# UC-05 일정 조회 (S05 월간·S05-W 주간, 대시보드 오늘 수업)
# ---------------------------------------------------------------------------


class ScheduleDateTests(TestCase):
    def test_week_starts_on_sunday(self):
        from lessons.schedule import week_start

        self.assertEqual(week_start(datetime.date(2026, 11, 19)), datetime.date(2026, 11, 15))  # 목 → 일
        self.assertEqual(week_start(datetime.date(2026, 11, 15)), datetime.date(2026, 11, 15))  # 일 그대로

    def test_month_grid_covers_whole_weeks(self):
        from lessons.schedule import month_weeks

        weeks = month_weeks(datetime.date(2026, 11, 19))
        self.assertEqual(weeks[0][0], datetime.date(2026, 11, 1))  # 2026-11-01은 일요일
        self.assertEqual(weeks[-1][-1], datetime.date(2026, 12, 5))
        self.assertTrue(all(len(w) == 7 for w in weeks))
        feb = month_weeks(datetime.date(2026, 2, 10))
        self.assertEqual(feb[0][0], datetime.date(2026, 2, 1))

    def test_add_months_clamps_to_month_end(self):
        from lessons.schedule import add_months

        self.assertEqual(add_months(datetime.date(2026, 1, 31), 1), datetime.date(2026, 2, 28))
        self.assertEqual(add_months(datetime.date(2026, 12, 15), 1), datetime.date(2027, 1, 15))
        self.assertEqual(add_months(datetime.date(2026, 1, 15), -1), datetime.date(2025, 12, 15))

    def test_parse_date_falls_back(self):
        from lessons.schedule import parse_date

        default = datetime.date(2026, 11, 20)
        for bad in ("", "2026-13-01", "abc", "0001-01-01", None):
            self.assertEqual(parse_date(bad, default), default)
        self.assertEqual(parse_date("2026-11-21", default), datetime.date(2026, 11, 21))


class ScheduleViewTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.sky = Student.objects.create(teacher=self.teacher, name="김하늘")
        self.yun = Student.objects.create(teacher=self.teacher, name="이도윤")
        self.sky_pass = make_pass(self.sky, title="피아노")
        self.yun_pass = make_pass(self.yun, title="바이올린")
        self.client.force_login(self.teacher)
        self.url = reverse("lessons:schedule")

    def at_session(self, enrollment, day, hour, minute=0, status=LessonSession.Status.SCHEDULED, **kw):
        start = timezone.make_aware(datetime.datetime(2026, 11, day, hour, minute))
        return LessonSession.objects.create(
            enrollment=enrollment, start_at=start, end_at=start + datetime.timedelta(hours=1), status=status, **kw
        )

    def get(self, **params):
        return self.client.get(self.url, params)

    def test_url(self):
        self.assertEqual(self.url, "/schedule/")

    def test_month_marks_days_and_lists_selected_day_in_time_order(self):
        late = self.at_session(self.sky_pass, 19, 17, 30)
        early = self.at_session(self.yun_pass, 19, 15)
        self.at_session(self.sky_pass, 12, 17)
        res = self.get(date="2026-11-19")
        counts = {c["date"].day: c["count"] for w in res.context["grid"] for c in w if c["in_month"]}
        self.assertEqual((counts[19], counts[12], counts[13]), (2, 1, 0))
        self.assertEqual(res.context["day_sessions"], [early, late])
        self.assertContains(res, "이도윤 · 바이올린")
        self.assertContains(res, reverse("lessons:session_detail", args=[early.pk]))  # 누르면 상세

    def test_default_is_this_month_with_today_selected(self):
        res = self.get()
        self.assertEqual(res.context["view"], "month")
        self.assertEqual(res.context["selected"], timezone.localdate())

    def test_local_day_boundary_is_seoul(self):
        """서울 23:30 회차(UTC로는 같은 날 14:30)·00:30 회차(UTC로는 전날)가 서울 날짜에 붙는다."""
        night = self.at_session(self.sky_pass, 21, 23, 30)
        dawn = self.at_session(self.sky_pass, 22, 0, 30)
        self.assertEqual(self.get(date="2026-11-21").context["day_sessions"], [night])
        self.assertEqual(self.get(date="2026-11-22").context["day_sessions"], [dawn])

    def test_empty_day_shows_no_schedule_and_add_link_with_date(self):
        res = self.get(date="2026-11-20")
        self.assertContains(res, "일정 없음")
        self.assertIn("date=2026-11-20", res.context["add_url"])
        add = self.client.get(res.context["add_url"])
        self.assertEqual(add.context["form"]["date"].value(), datetime.date(2026, 11, 20))
        self.assertEqual(add.context["back_url"], reverse("lessons:schedule") + "?date=2026-11-20")

    def test_status_shapes_and_makeup_badge(self):
        cancelled = self.at_session(self.sky_pass, 19, 10, status=LessonSession.Status.CANCELLED)
        self.at_session(self.sky_pass, 19, 11, status=LessonSession.Status.COMPLETED)
        self.at_session(self.sky_pass, 19, 12, status=LessonSession.Status.ABSENT, absence_deducted=True)
        self.at_session(self.sky_pass, 19, 13, makeup_for=cancelled)
        res = self.get(date="2026-11-19")
        for text in ("is-cancelled", "휴강", "is-completed", "완료", "결석·차감", ">보강<"):
            self.assertContains(res, text)

    def test_week_view_sunday_to_saturday_with_empty_days(self):
        s = self.at_session(self.sky_pass, 19, 16)
        res = self.get(view="week", date="2026-11-19")
        week = res.context["week"]
        self.assertEqual([d["date"].day for d in week], [15, 16, 17, 18, 19, 20, 21])
        self.assertEqual(week[4]["sessions"], [s])
        self.assertContains(res, "일정 없음", count=6)

    def test_prev_next_links(self):
        res = self.get(date="2026-01-31")
        self.assertIn("date=2025-12-31", res.context["prev_url"])
        self.assertIn("date=2026-02-28", res.context["next_url"])
        res = self.get(view="week", date="2026-11-19")
        self.assertIn("date=2026-11-12", res.context["prev_url"])
        self.assertIn("date=2026-11-26", res.context["next_url"])
        self.assertIn("view=week", res.context["next_url"])

    def test_student_filter_and_bad_values(self):
        self.at_session(self.sky_pass, 19, 15)
        self.at_session(self.yun_pass, 19, 16)
        only_sky = self.get(date="2026-11-19", student=self.sky.pk)
        self.assertEqual([s.enrollment.student for s in only_sky.context["day_sessions"]], [self.sky])
        self.assertIn(f"student={self.sky.pk}", only_sky.context["next_url"])  # 이동해도 필터 유지
        for bad in ("abc", "999999"):
            self.assertEqual(len(self.get(date="2026-11-19", student=bad).context["day_sessions"]), 2)
        res = self.get(view="nonsense", date="nonsense")
        self.assertEqual((res.status_code, res.context["view"]), (200, "month"))

    def test_archived_students_and_other_teachers_are_hidden(self):
        self.at_session(self.sky_pass, 19, 15, status=LessonSession.Status.COMPLETED)
        self.sky.is_active = False
        self.sky.save()
        b = make_teacher("b@example.com", "B강사")
        b_student = Student.objects.create(teacher=b, name="B의 학생")
        self.at_session(make_pass(b_student, title="남의 레슨"), 19, 16)
        res = self.get(date="2026-11-19")
        self.assertEqual(res.context["day_sessions"], [])
        self.assertNotContains(res, "남의 레슨")
        filtered = self.get(date="2026-11-19", student=b_student.pk)  # 남의 수강생 필터는 무시
        self.assertIsNone(filtered.context["student"])
        self.assertNotIn(b_student, list(res.context["students"]))

    def test_query_count_does_not_grow_with_sessions(self):
        self.at_session(self.sky_pass, 19, 9)
        with CaptureQueriesContext(connection) as few:
            self.get(date="2026-11-19")
        for h in range(10, 18):
            self.at_session(self.yun_pass if h % 2 else self.sky_pass, 19, h)
        with CaptureQueriesContext(connection) as many:
            self.get(date="2026-11-19")
        self.assertEqual(len(many), len(few))

    def test_login_required(self):
        self.client.logout()
        res = self.get()
        self.assertEqual(res.status_code, 302)


class DashboardTodayTests(TestCase):
    def test_today_count_and_list(self):
        teacher = make_teacher("a@example.com", "A강사")
        student = Student.objects.create(teacher=teacher, name="김하늘")
        e = make_pass(student)
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.datetime.combine(today, datetime.time(15)), tz)
        LessonSession.objects.create(enrollment=e, start_at=start, end_at=start + datetime.timedelta(hours=1))
        done = start - datetime.timedelta(hours=3)
        LessonSession.objects.create(
            enrollment=e, start_at=done, end_at=done + datetime.timedelta(hours=1), status="completed"
        )
        tomorrow = start + datetime.timedelta(days=1)
        LessonSession.objects.create(enrollment=e, start_at=tomorrow, end_at=tomorrow + datetime.timedelta(hours=1))
        self.client.force_login(teacher)
        res = self.client.get(reverse("home"))
        self.assertEqual(len(res.context["today_sessions"]), 2)
        self.assertEqual(res.context["today_scheduled"], 1)
        self.assertContains(res, "남은 예정 1회")
        self.assertContains(res, "김하늘 · 피아노 개인 레슨")


# ---------------------------------------------------------------------------
# UC-07 진행·잔여 회차 확인 (수강생 목록 S08, 레슨권 카드 S10)
# ---------------------------------------------------------------------------


class RemainingStateTests(TestCase):
    """잔여 = 총 − 차감. 2회 이하 'low', 0 이하 'exhausted'(자동 종료 없음)."""

    def setUp(self):
        teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=teacher, name="박서연")

    def state_after(self, total, completed):
        e = make_pass(self.student, title=f"{total}회권-{completed}", total=total)
        for day in range(1, completed + 1):
            add_session(e, LessonSession.Status.COMPLETED, day=day)
        return usage(e)

    def test_boundaries(self):
        cases = [(8, 5, 3, ""), (8, 6, 2, "low"), (8, 7, 1, "low"), (8, 8, 0, "exhausted"), (2, 3, -1, "exhausted")]
        for total, completed, remaining, state in cases:
            with self.subTest(total=total, completed=completed):
                e = self.state_after(total, completed)
                self.assertEqual(e.remaining_count, remaining)
                self.assertEqual(e.remaining_state, state)
                self.assertEqual(e.status, Enrollment.Status.ACTIVE)  # 소진돼도 자동 종료하지 않음

    def test_overflow_count(self):
        self.assertEqual(self.state_after(2, 3).overflow_count, 1)
        self.assertEqual(self.state_after(8, 1).overflow_count, 0)


class StudentListRemainingTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.client.force_login(self.teacher)

    def student_with(self, name, total, completed, title="피아노"):
        s = Student.objects.create(teacher=self.teacher, name=name)
        e = make_pass(s, title=title, total=total)
        for day in range(1, completed + 1):
            add_session(e, LessonSession.Status.COMPLETED, day=day)
        return s, e

    def test_list_shows_remaining_per_active_pass_with_emphasis(self):
        self.student_with("김하늘", 8, 1)
        low, _ = self.student_with("박서연", 8, 6)
        self.student_with("최준호", 4, 4, title="기타")
        Student.objects.create(teacher=self.teacher, name="정민아")
        response = self.client.get(reverse("students:list"))
        self.assertContains(response, "잔여 7/8")
        self.assertContains(response, 'class="remaining is-low"')
        self.assertContains(response, "잔여 2/8")
        self.assertContains(response, "얼마 남지 않음")  # 색만이 아니라 글자로도
        self.assertContains(response, "소진</span>")
        self.assertContains(response, "0/4")
        self.assertContains(response, "레슨권 없음")

    def test_multiple_active_passes_each_on_a_line_and_ended_hidden(self):
        s, _ = self.student_with("김하늘", 8, 0)
        make_pass(s, title="작곡", total=4)
        make_pass(s, title="예전 레슨", total=4, status=Enrollment.Status.ENDED)
        page = self.client.get(reverse("students:list"))
        self.assertContains(page, '<li class="pass-line">', count=2)
        self.assertContains(page, "작곡")
        self.assertNotContains(page, "예전 레슨")

    def test_overflow_shown_on_list(self):
        self.student_with("최준호", 2, 3)
        self.assertContains(self.client.get(reverse("students:list")), "초과 1회")

    def test_query_count_does_not_grow_with_students(self):
        self.student_with("가", 8, 1)
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("students:list"))
        for i in range(5):
            s, e = self.student_with(f"학생{i}", 8, 2)
            make_pass(s, title="두 번째", total=4)
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("students:list"))
        self.assertEqual(len(many), len(few))

    def test_other_teachers_passes_are_not_shown(self):
        b = make_teacher("b@example.com", "B강사")
        other = Student.objects.create(teacher=b, name="B의 학생")
        make_pass(other, title="남의 레슨권")
        self.student_with("김하늘", 8, 0)
        self.assertNotContains(self.client.get(reverse("students:list")), "남의 레슨권")

    def test_detail_card_marks_low_remaining(self):
        s, _ = self.student_with("박서연", 8, 6)
        page = self.client.get(reverse("students:detail", args=[s.pk]))
        self.assertContains(page, "잔여 적음")
        self.assertContains(page, 'class="is-low"')


# ---------------------------------------------------------------------------
# UC-02 보관 마무리 (#9): 한 트랜잭션, 보관 해제의 의미
# ---------------------------------------------------------------------------


class ArchiveTransactionTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="박서연")
        e = make_pass(self.student)
        self.planned = [add_session(e, day=d) for d in (21, 22)]
        self.done = add_session(e, LessonSession.Status.COMPLETED, day=14)
        self.client.force_login(self.teacher)

    def test_archive_failure_rolls_back_session_deletion(self):
        """예정 회차를 지운 뒤 보관 저장이 실패하면 삭제도 되돌린다 (둘 다 되거나 둘 다 안 됨)."""
        from unittest import mock

        with (
            mock.patch.object(Student, "save", side_effect=RuntimeError("저장 실패")),
            self.assertRaises(RuntimeError),
            self.assertLogs("django.request", "ERROR"),  # 의도된 500 로그는 테스트 출력에 남기지 않음
        ):
            self.client.post(reverse("students:archive", args=[self.student.pk]))
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)
        self.assertEqual(LessonSession.objects.filter(status="scheduled").count(), 2)

    def test_restore_brings_back_active_status_but_not_deleted_sessions(self):
        """보관 해제는 '수강 중' 상태만 되돌린다. 보관 때 지운 예정 회차는 되살리지 않는다 (필요하면 다시 등록)."""
        self.client.post(reverse("students:archive", args=[self.student.pk]))
        self.client.post(reverse("students:restore", args=[self.student.pk]))
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)
        self.assertEqual(list(LessonSession.objects.all()), [self.done])
