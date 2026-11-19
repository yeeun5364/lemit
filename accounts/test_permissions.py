"""강사별 데이터 접근 도우미(accounts/permissions.py) 테스트.

아직 강사 소유 모델(Student 등)이 없어서, User를 외래키(user)로 가진 Django 기본 모델 LogEntry를
"강사 소유 데이터"로 보고 동작을 확인한다. UC-02 이후에는 각 앱이 실제 모델로 강사 A/B 격리 테스트를 둔다.
"""

from django import forms
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.views.generic import DetailView

from accounts.permissions import TeacherScopedFormMixin, TeacherScopedQuerysetMixin, get_owned_or_404, scope_to_teacher

User = get_user_model()


class OwnDetailView(TeacherScopedQuerysetMixin, DetailView):
    model = LogEntry
    teacher_lookup = "user"


class OwnChoiceForm(TeacherScopedFormMixin, forms.Form):
    target = forms.ModelChoiceField(queryset=LogEntry.objects.all())
    teacher_scoped_fields = {"target": "user"}


class TeacherScopeHelperTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = User.objects.create_user(email="a@example.com", password="x-pass-2026!", name="A")
        cls.b = User.objects.create_user(email="b@example.com", password="x-pass-2026!", name="B")
        cls.a_obj = LogEntry.objects.create(user=cls.a, action_flag=ADDITION, object_repr="A의 데이터")
        cls.b_obj = LogEntry.objects.create(user=cls.b, action_flag=ADDITION, object_repr="B의 데이터")

    def test_scope_to_teacher(self):
        self.assertQuerySetEqual(scope_to_teacher(LogEntry.objects.all(), self.a, "user"), [self.a_obj])

    def test_get_owned_or_404(self):
        self.assertEqual(get_owned_or_404(LogEntry, self.a, "user", pk=self.a_obj.pk), self.a_obj)
        with self.assertRaises(Http404):  # 다른 강사의 객체는 403이 아니라 404 (존재 여부 비공개)
            get_owned_or_404(LogEntry, self.a, "user", pk=self.b_obj.pk)

    def test_class_based_view_returns_404_for_other_teacher(self):
        request = RequestFactory().get("/")
        request.user = self.a
        res = OwnDetailView.as_view()(request, pk=self.a_obj.pk)  # 렌더링 전 TemplateResponse
        self.assertEqual(res.context_data["object"], self.a_obj)
        with self.assertRaises(Http404):
            OwnDetailView.as_view()(request, pk=self.b_obj.pk)

    def test_form_rejects_other_teachers_choice(self):
        """조작된 POST로 다른 강사의 id를 보내도 폼 오류가 된다."""
        self.assertTrue(OwnChoiceForm({"target": self.a_obj.pk}, teacher=self.a).is_valid())
        form = OwnChoiceForm({"target": self.b_obj.pk}, teacher=self.a)
        self.assertFalse(form.is_valid())
        self.assertIn("target", form.errors)

    def test_form_requires_teacher(self):
        with self.assertRaises(TypeError):
            OwnChoiceForm({"target": self.a_obj.pk})
