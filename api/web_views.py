"""웹 화면 '설정 > API 키' (#102): 발급·연장·폐기.

- 강사 본인의 키만 보이고 다룬다. 다른 강사의 키 번호를 넣으면 404 (accounts.permissions).
- 수강생 계정은 StudentPortalMiddleware가 이 화면을 막는다 (공통 화면 목록에 없음).
- 원문 키는 발급 응답에서 한 번만 보여 준다. 세션·DB에 남기지 않고, 브라우저가 저장하지 않게 no-store로 보낸다.
"""

import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import get_owned_or_404
from api import keys, scopes
from api.forms import ApiKeyCreateForm, ApiKeyExtendForm
from api.models import ApiKey

LIMIT_MESSAGE = (
    f"유효한 API 키는 {ApiKey.MAX_USABLE_PER_USER}개까지 만들 수 있어요. 쓰지 않는 키를 폐기한 뒤 다시 시도해 주세요."
)


def _usable_count(user):
    return ApiKey.objects.filter(user=user).usable().count()


def key_list(request):
    api_keys = list(ApiKey.objects.filter(user=request.user))
    usable = sum(k.is_usable for k in api_keys)
    return render(
        request,
        "api/key_list.html",
        {"api_keys": api_keys, "usable_count": usable, "limit": ApiKey.MAX_USABLE_PER_USER},
    )


@require_http_methods(["GET", "POST"])
def key_create(request):
    user = request.user
    form = ApiKeyCreateForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # 같은 강사의 동시 발급 요청이 개수 제한을 함께 넘지 않도록 강사 행을 잠근다
            get_user_model().objects.select_for_update().get(pk=user.pk)
            if _usable_count(user) >= ApiKey.MAX_USABLE_PER_USER:
                form.add_error(None, LIMIT_MESSAGE)
            else:
                raw, prefix, digest = keys.generate()
                days = form.cleaned_data["expires_in"]
                api_key = ApiKey.objects.create(
                    user=user,
                    name=form.cleaned_data["name"],
                    prefix=prefix,
                    key_hash=digest,
                    scopes=[code for code, _ in scopes.CHOICES if code in form.cleaned_data["scopes"]],
                    expires_at=timezone.now() + datetime.timedelta(days=days),
                )
                response = render(request, "api/key_created.html", {"api_key": api_key, "raw_key": raw})
                response["Cache-Control"] = "no-store"
                return response
    return render(
        request,
        "api/key_form.html",
        {
            "form": form,
            "limit_reached": _usable_count(user) >= ApiKey.MAX_USABLE_PER_USER,
            "limit_message": LIMIT_MESSAGE,
        },
    )


@require_http_methods(["GET", "POST"])
def key_extend(request, pk):
    api_key = get_owned_or_404(ApiKey, request.user, teacher_lookup="user", pk=pk)
    if not api_key.is_usable:
        messages.error(request, "만료되었거나 폐기된 키는 연장할 수 없어요. 새 키를 발급해 주세요.")
        return redirect("api_keys:list")
    form = ApiKeyExtendForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        api_key.expires_at = timezone.now() + datetime.timedelta(days=form.cleaned_data["expires_in"])
        api_key.save(update_fields=["expires_at"])
        expires_on = timezone.localtime(api_key.expires_at)
        messages.success(
            request, f"'{api_key.name}' 키를 {expires_on:%Y년 %m월 %d일}까지 연장했어요. 키 값은 그대로예요."
        )
        return redirect("api_keys:list")
    return render(request, "api/key_extend.html", {"api_key": api_key, "form": form})


@require_http_methods(["GET", "POST"])
def key_revoke(request, pk):
    api_key = get_owned_or_404(ApiKey, request.user, teacher_lookup="user", pk=pk)
    if request.method == "POST":
        if api_key.revoked_at is None:
            api_key.revoked_at = timezone.now()
            api_key.save(update_fields=["revoked_at"])
        messages.success(request, f"'{api_key.name}' 키를 폐기했어요. 이 키로 보내는 요청은 바로 거절됩니다.")
        return redirect("api_keys:list")
    return render(request, "api/key_revoke.html", {"api_key": api_key})
