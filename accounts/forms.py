from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from accounts.models import User


class SignupForm(UserCreationForm):
    """S02 회원가입: 이메일·이름·비밀번호·비밀번호 확인."""

    class Meta:
        model = User
        fields = ("email", "name")
        widgets = {
            "email": forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
        }

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"].strip())
        # 로그인이 대소문자를 구분하지 않으므로 중복 검사도 대소문자 무시 (DB 제약과 같은 기준)
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("이미 가입된 이메일입니다.", code="duplicate_email")
        return email


class LoginForm(AuthenticationForm):
    """S01 로그인. 가입 여부를 드러내지 않도록 실패 메시지는 하나로 통일한다."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "이메일 또는 비밀번호가 올바르지 않습니다.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].widget = forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True})
        self.fields["username"].label = "이메일"
