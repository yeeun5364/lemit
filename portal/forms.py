from django import forms

from inbox.forms import BODY_MAX, _clean_body
from inbox.models import Conversation

SUBJECT_MAX = Conversation._meta.get_field("subject").max_length


class NewMessageForm(forms.Form):
    """수강생이 담당 강사에게 보내는 새 메시지 (#80). 받는 강사는 폼이 아니라 연결된 수강생 기록으로 정한다."""

    subject = forms.CharField(
        label="제목",
        max_length=SUBJECT_MAX,
        strip=True,
        widget=forms.TextInput(attrs={"placeholder": "예: 다음 주 수업 일정"}),
    )
    body = forms.CharField(
        label="내용", max_length=BODY_MAX, widget=forms.Textarea(attrs={"rows": 4, "maxlength": BODY_MAX})
    )

    def clean_body(self):
        return _clean_body(self.cleaned_data.get("body"))


class FollowUpForm(forms.Form):
    """같은 대화에 이어 보내기."""

    body = forms.CharField(
        label="메시지", max_length=BODY_MAX, widget=forms.Textarea(attrs={"rows": 2, "maxlength": BODY_MAX})
    )

    def clean_body(self):
        return _clean_body(self.cleaned_data.get("body"))
