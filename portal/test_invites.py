"""#84 일회용 QR로 수강생 초대·연결: 발급·재발급·취소, 스캔 → 로그인/가입 → 연결 요청, 강사 승인·거절, 연결 해제."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from portal.models import StudentInvite
from students.models import Student

User = get_user_model()
PASSWORD = "Lemit-pass-9876"


class InviteTestCase(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(email="teacher@example.com", password=PASSWORD, name="박강사")
        self.other_teacher = User.objects.create_user(email="other@example.com", password=PASSWORD, name="최강사")
        self.student = Student.objects.create(teacher=self.teacher, name="김하늘")
        Student.objects.create(teacher=self.other_teacher, name="최강사의 학생")
        self.account = User.objects.create_user(email="haneul@example.com", password=PASSWORD, name="김하늘")
        self.detail = reverse("students:detail", args=[self.student.pk])

    def issue(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("students:invite_issue", args=[self.student.pk]))
        return StudentInvite.objects.filter(student=self.student).latest("id")

    def join_url(self, invite):
        return reverse("portal:join", args=[invite.token])

    def request_as(self, user, invite):
        self.client.force_login(user)
        return self.client.post(self.join_url(invite), follow=True)

    def approve(self, invite):
        self.client.force_login(self.teacher)
        url = reverse("students:invite_approve", args=[self.student.pk, invite.pk])
        return self.client.post(url, follow=True)


class IssueTests(InviteTestCase):
    def test_teacher_issues_short_lived_qr_shown_on_student_detail(self):
        invite = self.issue()
        self.assertEqual(invite.status, StudentInvite.Status.ISSUED)
        left = invite.expires_at - timezone.now()
        self.assertTrue(datetime.timedelta(minutes=9) < left <= datetime.timedelta(minutes=10))
        html = self.client.get(self.detail).content.decode()
        self.assertIn("<svg", html)
        self.assertIn(self.join_url(invite), html)
        self.assertIn("스캔 대기", html)

    def test_reissue_cancels_previous_invite(self):
        first = self.issue()
        second = self.issue()
        first.refresh_from_db()
        self.assertEqual(first.status, StudentInvite.Status.CANCELLED)
        self.assertEqual(second.status, StudentInvite.Status.ISSUED)

    def test_cannot_issue_for_linked_or_archived_student(self):
        Student.objects.filter(pk=self.student.pk).update(account=self.account)
        self.client.force_login(self.teacher)
        r = self.client.post(reverse("students:invite_issue", args=[self.student.pk]), follow=True)
        self.assertContains(r, "이미 수강생 계정이 연결돼 있습니다")
        Student.objects.filter(pk=self.student.pk).update(account=None, is_active=False)
        r = self.client.post(reverse("students:invite_issue", args=[self.student.pk]), follow=True)
        self.assertContains(r, "보관된 수강생에게는 초대를 만들 수 없습니다")
        self.assertFalse(StudentInvite.objects.exists())

    def test_teacher_can_cancel_open_invite(self):
        invite = self.issue()
        self.client.post(reverse("students:invite_cancel", args=[self.student.pk, invite.pk]))
        invite.refresh_from_db()
        self.assertEqual(invite.status, StudentInvite.Status.CANCELLED)
        self.client.force_login(self.account)
        self.assertContains(self.client.get(self.join_url(invite)), "만료됐거나 이미 사용된 초대입니다")


class JoinFlowTests(InviteTestCase):
    def test_anonymous_scan_offers_login_and_signup_that_come_back(self):
        invite = self.issue()
        self.client.logout()
        r = self.client.get(self.join_url(invite))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "박강사")
        self.assertContains(r, f"{reverse('login')}?next=/me/join/")
        self.assertContains(r, f"{reverse('signup')}?next=/me/join/")
        r = self.client.post(self.join_url(invite))
        self.assertRedirects(r, f"{reverse('login')}?next={self.join_url(invite)}", fetch_redirect_response=False)
        self.assertEqual(StudentInvite.objects.get().status, StudentInvite.Status.ISSUED)

    def test_new_signup_returns_to_invite_and_requests(self):
        invite = self.issue()
        self.client.logout()
        data = {"email": "new@example.com", "name": "새 수강생", "password1": PASSWORD, "password2": PASSWORD}
        r = self.client.post(
            f"{reverse('signup')}?next={self.join_url(invite)}", {**data, "next": self.join_url(invite)}
        )
        self.assertRedirects(r, self.join_url(invite))
        self.client.post(self.join_url(invite))
        invite.refresh_from_db()
        self.assertEqual(
            (invite.status, invite.requested_by.email), (StudentInvite.Status.REQUESTED, "new@example.com")
        )

    def test_signup_ignores_outside_next(self):
        data = {"email": "x@example.com", "name": "x", "password1": PASSWORD, "password2": PASSWORD}
        r = self.client.post(reverse("signup"), {**data, "next": "https://evil.example.com/"})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_request_then_approve_gives_access_only_after_approval(self):
        invite = self.issue()
        r = self.request_as(self.account, invite)
        self.assertContains(r, "연결을 요청했어요")
        self.assertEqual(self.client.get(reverse("portal:lessons")).status_code, 404)  # 승인 전에는 수강 기록 없음
        self.client.force_login(self.teacher)
        html = self.client.get(self.detail).content.decode()
        self.assertIn("haneul@example.com", html)
        self.assertIn("승인 대기", html)
        r = self.approve(invite)
        self.assertContains(r, "haneul@example.com 계정을")
        self.student.refresh_from_db()
        self.assertEqual(self.student.account, self.account)
        invite.refresh_from_db()
        self.assertEqual(invite.status, StudentInvite.Status.APPROVED)
        self.client.force_login(self.account)
        self.assertEqual(self.client.get(reverse("portal:lessons")).status_code, 200)
        self.assertRedirects(self.client.get(self.join_url(invite)), reverse("portal:lessons"))

    def test_teacher_rejects_request(self):
        invite = self.issue()
        self.request_as(self.account, invite)
        self.client.force_login(self.teacher)
        self.client.post(reverse("students:invite_cancel", args=[self.student.pk, invite.pk]))
        invite.refresh_from_db()
        self.assertEqual(invite.status, StudentInvite.Status.CANCELLED)
        self.assertIsNone(Student.objects.get(pk=self.student.pk).account)
        self.assertContains(self.approve(invite), "승인할 요청이 없습니다")


class RefusalTests(InviteTestCase):
    def test_expired_invite_is_refused(self):
        invite = self.issue()
        StudentInvite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
        r = self.request_as(self.account, invite)
        self.assertContains(r, "만료됐거나 이미 사용된 초대입니다")
        self.assertEqual(StudentInvite.objects.get().status, StudentInvite.Status.ISSUED)

    def test_used_invite_cannot_be_reused_by_another_account(self):
        invite = self.issue()
        self.request_as(self.account, invite)
        self.approve(invite)
        other = User.objects.create_user(email="other.student@example.com", password=PASSWORD, name="다른 학생")
        self.assertContains(self.request_as(other, invite), "만료됐거나 이미 사용된 초대입니다")
        self.assertEqual(Student.objects.get(pk=self.student.pk).account, self.account)

    def test_second_requester_cannot_take_over_pending_request(self):
        invite = self.issue()
        self.request_as(self.account, invite)
        other = User.objects.create_user(email="other.student@example.com", password=PASSWORD, name="다른 학생")
        self.assertContains(self.request_as(other, invite), "만료됐거나 이미 사용된 초대입니다")
        self.assertEqual(StudentInvite.objects.get().requested_by, self.account)

    def test_teacher_admin_or_already_linked_accounts_are_refused(self):
        admin = User.objects.create_superuser(email="admin@example.com", password=PASSWORD, name="관리자")
        linked = User.objects.create_user(email="linked@example.com", password=PASSWORD, name="이미 연결")
        Student.objects.create(teacher=self.teacher, name="다른 수강생", account=linked)
        invite = self.issue()
        for user, reason in (
            (self.other_teacher, "강사 계정은 수강생 계정으로 연결할 수 없습니다"),
            (admin, "관리자 계정은"),
            (linked, "이미 다른 수강생 기록에 연결돼 있습니다"),
        ):
            self.assertContains(self.request_as(user, invite), reason, msg_prefix=user.email)
        self.assertEqual(StudentInvite.objects.get().status, StudentInvite.Status.ISSUED)

    def test_double_approve_links_once(self):
        invite = self.issue()
        self.request_as(self.account, invite)
        self.approve(invite)
        r = self.approve(invite)
        self.assertContains(r, "승인할 요청이 없습니다")
        self.assertEqual(Student.objects.filter(account=self.account).count(), 1)

    def test_unknown_token_is_404(self):
        self.client.force_login(self.account)
        self.assertEqual(self.client.get(reverse("portal:join", args=["nope"])).status_code, 404)


class PermissionTests(InviteTestCase):
    def test_other_teacher_cannot_touch_invites(self):
        invite = self.issue()
        self.request_as(self.account, invite)
        self.client.force_login(self.other_teacher)
        urls = (
            reverse("students:invite_issue", args=[self.student.pk]),
            reverse("students:invite_approve", args=[self.student.pk, invite.pk]),
            reverse("students:invite_cancel", args=[self.student.pk, invite.pk]),
            reverse("students:unlink", args=[self.student.pk]),
        )
        for url in urls:
            self.assertEqual(self.client.post(url).status_code, 404, url)
        invite.refresh_from_db()
        self.assertEqual(invite.status, StudentInvite.Status.REQUESTED)
        self.assertIsNone(Student.objects.get(pk=self.student.pk).account)

    def test_student_account_cannot_approve_itself(self):
        invite = self.issue()
        self.request_as(self.account, invite)
        Student.objects.filter(pk=self.student.pk).update(account=None)
        peer = Student.objects.create(teacher=self.teacher, name="연결된 학생")
        linked = User.objects.create_user(email="s2@example.com", password=PASSWORD, name="학생2")
        Student.objects.filter(pk=peer.pk).update(account=linked)
        self.client.force_login(linked)  # 수강생 계정: 강사 화면 POST는 403
        url = reverse("students:invite_approve", args=[self.student.pk, invite.pk])
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertIsNone(Student.objects.get(pk=self.student.pk).account)

    def test_unlink_blocks_access_immediately(self):
        invite = self.issue()
        self.request_as(self.account, invite)
        self.approve(invite)
        self.client.force_login(self.teacher)
        r = self.client.post(reverse("students:unlink", args=[self.student.pk]), follow=True)
        self.assertContains(r, "수강생 계정 연결을 해제했습니다")
        self.assertIsNone(Student.objects.get(pk=self.student.pk).account)
        self.client.force_login(self.account)
        self.assertEqual(self.client.get(reverse("portal:lessons")).status_code, 404)
        self.assertEqual(self.client.get(reverse("portal:messages")).status_code, 404)
