from django import forms

from accounts.permissions import TeacherScopedFormMixin
from inbox.models import Conversation, Message
from students.models import Student

BODY_MAX = Message._meta.get_field("body").max_length


def _clean_body(value):
    value = (value or "").strip()
    if not value:
        raise forms.ValidationError("내용을 입력하세요.")
    return value


class StudentMessageForm(TeacherScopedFormMixin, forms.Form):
    """S15 수강생 메시지 추가 (시연용). 수강생 선택지는 로그인한 강사의 수강 중인 수강생만."""

    student = forms.ModelChoiceField(label="수강생", queryset=Student.objects.active(), empty_label="수강생 선택")
    subject = forms.CharField(label="제목", max_length=Conversation._meta.get_field("subject").max_length, strip=True)
    body = forms.CharField(
        label="내용", max_length=BODY_MAX, widget=forms.Textarea(attrs={"rows": 5, "maxlength": BODY_MAX})
    )
    teacher_scoped_fields = {"student": "teacher"}

    def clean_body(self):
        return _clean_body(self.cleaned_data.get("body"))


class ReplyForm(forms.Form):
    body = forms.CharField(
        label="답장", max_length=BODY_MAX, widget=forms.Textarea(attrs={"rows": 2, "maxlength": BODY_MAX})
    )

    def clean_body(self):
        return _clean_body(self.cleaned_data.get("body"))
