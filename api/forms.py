"""API 키 발급·연장 폼 (#102).

발급·연장은 비밀번호를 다시 확인한다 (로그인된 PC를 잠깐 쓴 사람이 키를 만들어 가져가지 못하게).
재확인 실패는 로그인 제한(accounts/throttle.py)과 같은 방식으로 세고, 발급·연장 두 화면이 횟수를 함께 쓴다
(종류 api_key, 15분에 계정당 5번·IP당 20번, #102 리뷰 P1). 막힌 동안에는 맞는 비밀번호도 받지 않는다.
"""

from django import forms

from accounts import throttle
from accounts.models import LoginAttempt
from api import scopes
from api.models import ApiKey


class PasswordConfirmForm(forms.Form):
    password = forms.CharField(
        label="현재 비밀번호",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="본인 확인을 위해 로그인 비밀번호를 다시 입력해 주세요.",
        error_messages={"required": "현재 비밀번호를 입력하세요."},
    )

    BLOCKED_MESSAGE = "비밀번호 확인 시도가 너무 많습니다. 15분 뒤에 다시 시도하세요."
    KIND = LoginAttempt.Kind.API_KEY

    def __init__(self, *args, user, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.request = request

    def clean_password(self):
        password = self.cleaned_data["password"]
        # 확인 전에 한 칸을 예약해, 동시에 보낸 요청도 한도를 넘어 비밀번호 확인까지 가지 못하게 한다
        attempt = throttle.reserve(self.KIND, self.request, self.user.email, pending=True)
        if attempt is None:
            raise forms.ValidationError(self.BLOCKED_MESSAGE, code="throttled")
        try:
            ok = self.user.check_password(password)
        except Exception:
            throttle.release(attempt)
            raise
        if not ok:
            throttle.confirm(attempt)
            raise forms.ValidationError("비밀번호가 맞지 않습니다.", code="wrong_password")
        throttle.release(attempt)
        throttle.clear_email(self.KIND, self.user.email)
        return password


def expiry_field(label):
    return forms.TypedChoiceField(
        label=label,
        choices=ApiKey.EXPIRY_CHOICES,
        coerce=int,
        initial=ApiKey.DEFAULT_EXPIRY_DAYS,
        widget=forms.RadioSelect,
        error_messages={"required": "기간을 고르세요.", "invalid_choice": "기간을 다시 고르세요."},
    )


class ApiKeyCreateForm(PasswordConfirmForm):
    field_order = ["name", "scopes", "expires_in", "password"]

    name = forms.CharField(
        label="키 이름",
        max_length=ApiKey._meta.get_field("name").max_length,
        help_text="어디에 쓰는 키인지 적어 두면 나중에 구분하기 쉬워요. 예: Claude 에이전트, 테스트용",
        error_messages={"required": "키 이름을 입력하세요."},
    )
    scopes = forms.MultipleChoiceField(
        label="범위",
        choices=scopes.CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="필요한 것만 고르세요. 메시지 발송은 수강생에게 실제로 전달되어 되돌릴 수 없어요.",
        error_messages={"required": "범위를 하나 이상 고르세요."},
    )
    expires_in = expiry_field("유효 기간")


class ApiKeyExtendForm(PasswordConfirmForm):
    field_order = ["expires_in", "password"]

    expires_in = expiry_field("오늘부터 연장할 기간")
