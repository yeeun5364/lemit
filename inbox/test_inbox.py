"""UC-17 메시지함 테스트: 실제 분류 동선, 모델 불가 시 메시지 보존, 강사 A/B 격리 (#33 필수 테스트)."""

import re
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from inbox import classifier, services
from inbox.forms import StudentMessageForm
from inbox.models import Conversation, Message
from students.models import Student

User = get_user_model()
MAKEUP_TEXT = "선생님 수요일에 학교 행사가 있어서 못 갈 것 같아요. 금요일에 보강 가능할까요?"


def make_teacher(email, name):
    return User.objects.create_user(email=email, password="lesson-time-2026!", name=name)


class InboxTestBase(TestCase):
    def setUp(self):
        classifier.clear_cache()
        self.addCleanup(classifier.clear_cache)
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="김하늘")
        self.client.force_login(self.teacher)

    def post_student_message(self, body=MAKEUP_TEXT, subject="수요일 수업", student=None):
        return self.client.post(
            reverse("inbox:new"), {"student": (student or self.student).pk, "subject": subject, "body": body}
        )


class ClassificationFlowTests(InboxTestBase):
    def test_student_message_is_classified_and_shown(self):
        """필수 ①: 수강생 메시지 POST → 리다이렉트 → 대화·메시지함 화면에 실제 모델의 예측 배지가 보임."""
        res = self.post_student_message()
        conv = Conversation.objects.get()
        self.assertRedirects(res, reverse("inbox:detail", args=[conv.pk]))
        msg = conv.messages.get()
        self.assertEqual(msg.sender, Message.Sender.STUDENT)
        self.assertEqual(msg.predicted_label, "MAKEUP")  # 직접 학습한 모델(v2)의 실제 추론
        self.assertEqual(msg.model_version, classifier.get_model().version)
        self.assertIsNotNone(msg.classified_at)
        self.assertTrue(0 < msg.confidence <= 1)

        detail = self.client.get(res["Location"])
        self.assertContains(detail, "보강 요청")
        self.assertContains(detail, "AI 예측·참고용")
        self.assertContains(detail, f"{msg.confidence_percent}%")
        listing = self.client.get(reverse("inbox:list"))
        self.assertContains(listing, "김하늘 · 수요일 수업")
        self.assertContains(listing, "보강 요청")

    def test_low_confidence_is_marked(self):
        self.post_student_message(body="내일 수업 취소해 주세요")  # v2 모델 확신도 0.48
        msg = Message.objects.get()
        self.assertTrue(msg.is_low_confidence)
        self.assertContains(self.client.get(reverse("inbox:detail", args=[msg.conversation_id])), "확신 낮음")

    def test_refresh_after_save_does_not_duplicate(self):
        """PRG: 저장 후 이동한 화면을 새로고침(GET)해도 메시지가 다시 저장되지 않는다."""
        res = self.post_student_message()
        self.client.get(res["Location"])
        self.client.get(res["Location"])
        self.assertEqual(Message.objects.count(), 1)

    def test_same_student_and_subject_continues_conversation(self):
        self.post_student_message(body="보강 일정 잡아주세요")
        self.post_student_message(body="수업 시간을 4시에서 5시로 바꿔주세요")
        self.post_student_message(body="이번 달 수강료 입금했습니다", subject="수강료")
        self.assertEqual(Conversation.objects.count(), 2)
        conv = Conversation.objects.get(subject="수요일 수업")
        self.assertEqual(list(conv.messages.values_list("predicted_label", flat=True)), ["MAKEUP", "RESCHEDULE"])

    def test_teacher_reply_is_saved_but_not_classified(self):
        self.post_student_message()
        conv = Conversation.objects.get()
        res = self.client.post(reverse("inbox:reply", args=[conv.pk]), {"body": "네 금요일 4시 괜찮습니다"})
        self.assertRedirects(res, reverse("inbox:detail", args=[conv.pk]))
        reply = conv.messages.get(sender=Message.Sender.TEACHER)
        self.assertEqual((reply.predicted_label, reply.confidence, reply.model_version), ("", None, ""))
        conv.refresh_from_db()
        self.assertEqual(conv.last_message_at, reply.sent_at)

    def test_tabs_filter_by_latest_student_message_label(self):
        self.post_student_message(body="보강 일정 잡아주세요", subject="보강")
        self.post_student_message(body="이번 달 수강료 입금했습니다", subject="입금")
        res = self.client.get(reverse("inbox:list"), {"label": "MAKEUP"})
        self.assertContains(res, "김하늘 · 보강")
        self.assertNotContains(res, "김하늘 · 입금")
        res = self.client.get(reverse("inbox:list"), {"label": "GENERAL"})
        self.assertContains(res, "김하늘 · 입금")
        self.assertNotContains(res, "김하늘 · 보강")
        res = self.client.get(reverse("inbox:list"), {"label": "<script>"})  # 알 수 없는 값은 전체
        self.assertEqual(len(res.context["conversations"]), 2)

    def test_opening_conversation_marks_student_messages_read(self):
        self.post_student_message()
        msg = Message.objects.get()
        self.assertIsNone(msg.read_at)
        self.assertEqual(self.client.get(reverse("inbox:list")).context["inbox_unread"](), 1)
        self.client.get(reverse("inbox:detail", args=[msg.conversation_id]))
        msg.refresh_from_db()
        self.assertIsNotNone(msg.read_at)
        self.assertEqual(self.client.get(reverse("inbox:list")).context["inbox_unread"](), 0)

    def test_classification_does_not_change_student_or_other_data(self):
        """예측은 참고용: 분류 후 수강생 정보가 바뀌지 않는다. (회차 불변 확인은 UC-04 회차 모델이 생기면 추가)"""
        before = Student.objects.values().get(pk=self.student.pk)
        self.post_student_message()
        self.assertEqual(Student.objects.values().get(pk=self.student.pk), before)

    def test_body_is_html_escaped(self):
        self.post_student_message(body="<script>alert(1)</script> 보강 가능할까요")
        res = self.client.get(reverse("inbox:detail", args=[Conversation.objects.get().pk]))
        self.assertNotContains(res, "<script>alert(1)</script>")
        self.assertContains(res, "&lt;script&gt;")


class ValidationTests(InboxTestBase):
    def test_empty_or_blank_body_is_rejected(self):
        for body in ("", "   \n "):
            with self.subTest(body=body):
                res = self.post_student_message(body=body)
                self.assertEqual(res.status_code, 200)
                self.assertFalse(Message.objects.exists())

    def test_too_long_body_is_rejected(self):
        self.assertEqual(self.post_student_message(body="가" * 1001).status_code, 200)
        self.assertFalse(Message.objects.exists())

    def test_empty_reply_is_rejected(self):
        self.post_student_message()
        conv = Conversation.objects.get()
        res = self.client.post(reverse("inbox:reply", args=[conv.pk]), {"body": "  "})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(conv.messages.count(), 1)

    def test_archived_student_is_not_selectable(self):
        archived = Student.objects.create(teacher=self.teacher, name="보관학생", is_active=False)
        self.post_student_message(student=archived)
        self.assertFalse(Message.objects.exists())

    def test_db_rejects_classified_teacher_message(self):
        conv = Conversation.objects.create(teacher=self.teacher, student=self.student, subject="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Message.objects.create(conversation=conv, sender="teacher", body="x", predicted_label="MAKEUP")


class ModelUnavailableTests(InboxTestBase):
    """필수 ②: 모델을 쓸 수 없어도 메시지는 저장되고 '분류 불가'로 표시, 메시지함은 정상."""

    def setUp(self):
        super().setUp()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.missing = tmp / Path(settings.MESSAGE_CLASSIFIER_PATH).name  # 존재하지 않는 경로

    def test_message_is_kept_and_marked_unclassified(self):
        with override_settings(MESSAGE_CLASSIFIER_PATH=self.missing), self.assertLogs("inbox.classifier", "ERROR"):
            res = self.post_student_message()
        conv = Conversation.objects.get()
        self.assertRedirects(res, reverse("inbox:detail", args=[conv.pk]), fetch_redirect_response=False)
        msg = conv.messages.get()
        self.assertEqual(msg.body, MAKEUP_TEXT)
        self.assertEqual((msg.predicted_label, msg.confidence, msg.model_version), ("", None, ""))
        with override_settings(MESSAGE_CLASSIFIER_PATH=self.missing):
            detail = self.client.get(reverse("inbox:detail", args=[conv.pk]))
            listing = self.client.get(reverse("inbox:list"))
        self.assertContains(detail, "분류 불가")
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "분류 불가")

    def test_reply_works_without_model(self):
        conv = Conversation.objects.create(teacher=self.teacher, student=self.student, subject="x")
        with override_settings(MESSAGE_CLASSIFIER_PATH=self.missing):
            res = self.client.post(reverse("inbox:reply", args=[conv.pk]), {"body": "답장"})
        self.assertRedirects(res, reverse("inbox:detail", args=[conv.pk]), fetch_redirect_response=False)


class TeacherIsolationTests(TestCase):
    """필수 ③: 강사 A/B 격리 (실제 Conversation·Message·Student 모델과 URL)."""

    def setUp(self):
        self.a = make_teacher("a@example.com", "A강사")
        self.b = make_teacher("b@example.com", "B강사")
        self.a_student = Student.objects.create(teacher=self.a, name="에이학생")
        self.b_student = Student.objects.create(teacher=self.b, name="비학생")
        self.b_msg = services.add_student_message(
            teacher=self.b, student=self.b_student, subject="비의대화", body="보강 일정 잡아주세요"
        )
        self.b_conv = self.b_msg.conversation
        self.client.force_login(self.a)

    def test_list_and_tabs_show_only_own_conversations(self):
        for params in ({}, {"label": "MAKEUP"}):
            with self.subTest(params=params):
                res = self.client.get(reverse("inbox:list"), params)
                self.assertNotContains(res, "비의대화")
                self.assertNotContains(res, "비학생")

    def test_unread_count_counts_only_own_messages(self):
        self.assertEqual(self.client.get(reverse("inbox:list")).context["inbox_unread"](), 0)

    def test_other_teachers_conversation_is_404_and_unchanged(self):
        self.assertEqual(self.client.get(reverse("inbox:detail", args=[self.b_conv.pk])).status_code, 404)
        res = self.client.post(reverse("inbox:reply", args=[self.b_conv.pk]), {"body": "끼어들기"})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.b_conv.messages.count(), 1)
        self.b_msg.refresh_from_db()
        self.assertIsNone(self.b_msg.read_at)  # A가 열어도 B의 메시지가 읽음 처리되지 않음

    def test_cannot_create_message_for_other_teachers_student(self):
        """조작된 POST로 B의 수강생 id를 보내도 폼 오류, 대화·메시지가 생기지 않는다."""
        res = self.client.post(
            reverse("inbox:new"), {"student": self.b_student.pk, "subject": "조작", "body": "보강 가능할까요"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("student", res.context["form"].errors)
        self.assertFalse(Conversation.objects.filter(subject="조작").exists())

    def test_form_only_offers_own_active_students(self):
        form = StudentMessageForm(teacher=self.a)
        self.assertQuerySetEqual(form.fields["student"].queryset, [self.a_student])

    def test_service_and_model_recheck_ownership(self):
        """뷰를 거치지 않아도 서비스·모델 clean()이 소유권을 다시 확인한다."""
        with self.assertRaises(PermissionError):
            services.add_student_message(teacher=self.a, student=self.b_student, subject="x", body="보강")
        conv = Conversation(teacher=self.a, student=self.b_student, subject="x")
        with self.assertRaises(Exception) as ctx:
            conv.full_clean()
        self.assertIn("student", ctx.exception.message_dict)

    def test_missing_and_other_ids_look_the_same(self):
        other = self.client.get(reverse("inbox:detail", args=[self.b_conv.pk]))
        missing = self.client.get(reverse("inbox:detail", args=[999999]))
        self.assertEqual(other.status_code, missing.status_code)


class LoginAndCsrfTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher("a@example.com", "A강사")
        self.student = Student.objects.create(teacher=self.teacher, name="김하늘")
        self.conv = Conversation.objects.create(teacher=self.teacher, student=self.student, subject="x")

    def test_all_inbox_urls_require_login(self):
        login = reverse("login")
        for method, url in (
            ("get", reverse("inbox:list")),
            ("get", reverse("inbox:new")),
            ("post", reverse("inbox:new")),
            ("get", reverse("inbox:detail", args=[self.conv.pk])),
            ("post", reverse("inbox:reply", args=[self.conv.pk])),
        ):
            with self.subTest(method=method, url=url):
                res = getattr(self.client, method)(url)
                self.assertEqual(res.status_code, 302)
                self.assertTrue(res["Location"].startswith(login))
        self.assertFalse(Message.objects.exists())

    def test_reply_requires_post(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse("inbox:reply", args=[self.conv.pk])).status_code, 405)

    def test_csrf_enforced_for_new_message_and_reply(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        data = {"student": self.student.pk, "subject": "y", "body": "보강 일정 잡아주세요"}
        self.assertEqual(client.post(reverse("inbox:new"), data).status_code, 403)
        self.assertEqual(client.post(reverse("inbox:reply", args=[self.conv.pk]), {"body": "a"}).status_code, 403)
        self.assertFalse(Message.objects.exists())
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"', client.get(reverse("inbox:new")).content.decode()
        )
        res = client.post(reverse("inbox:new"), {**data, "csrfmiddlewaretoken": token.group(1)})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Message.objects.count(), 1)
