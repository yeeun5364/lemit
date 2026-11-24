from django import forms

from inbox.forms import BODY_MAX, _clean_body


class ChatForm(forms.Form):
    """대화방 입력창 (#95). 제목 없이 내용만. 받는 강사는 폼이 아니라 연결된 수강생 기록으로 정한다."""

    body = forms.CharField(
        label="메시지",
        max_length=BODY_MAX,
        widget=forms.Textarea(
            attrs={
                "rows": 1,
                "maxlength": BODY_MAX,
                "placeholder": "메시지를 입력하세요",
                "class": "input",
                "data-chat-input": "",
            }
        ),
    )

    def clean_body(self):
        return _clean_body(self.cleaned_data.get("body"))
