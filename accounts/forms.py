from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from accounts.models import DEMO_EMAIL as RESERVED_DEMO_EMAIL
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
        if email.lower() == RESERVED_DEMO_EMAIL:  # seed_demo 전용 (#24, PR #65 리뷰)
            raise forms.ValidationError("사용할 수 없는 이메일입니다.", code="reserved_email")
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

    def clean(self):
        """시도 횟수 제한 (#7): 막힌 상태면 비밀번호를 확인하지 않고 같은 안내를 보여 준다.

        실패하면 기록하고, 성공하면 그 이메일의 실패 기록을 지운다. 비밀번호가 맞아도 막힌 동안에는
        들어갈 수 없다(맞는 비밀번호를 찾았는지 알려 주지 않기 위해).
        """
        from accounts import throttle
        from accounts.models import LoginAttempt

        email = self.cleaned_data.get("username")
        kind = LoginAttempt.Kind.LOGIN
        # 비밀번호를 확인하기 전에 시도 한 칸을 예약한다. 확인·예약이 잠금 안에서 한 번에 일어나므로
        # 여러 worker에 동시에 들어온 요청도 한도를 넘어 비밀번호 확인까지 가지 못한다 (PR #70 리뷰 P2)
        attempt = throttle.reserve(kind, self.request, email)
        if attempt is None:
            raise forms.ValidationError(throttle.BLOCKED_MESSAGE, code="throttled")
        try:
            cleaned = super().clean()
        except forms.ValidationError as e:
            if e.code != "invalid_login":  # 실패가 아닌 다른 오류(비활성 계정 등)는 세지 않는다
                throttle.release(attempt)
            raise
        except Exception:
            throttle.release(attempt)
            raise
        throttle.release(attempt)  # 성공은 실패가 아니므로 반납
        throttle.clear_email(kind, email)
        return cleaned
