"""UC-03 레슨권 등록·종료, 잔여 회차 계산(ERD 2-2), 강사 A/B 격리 테스트."""

import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase
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
        self.assertEqual(response.context["back_url"], reverse("students:list"))

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
