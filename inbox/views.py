"""UC-17 메시지함 (S13 목록, S14 대화 상세·답장, S15 수강생 메시지 추가).

모든 조회·POST는 로그인한 강사의 대화에서만 찾는다 (accounts.permissions). 다른 강사의 대화는 404.
"""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import get_owned_or_404
from inbox import services
from inbox.forms import ReplyForm, StudentMessageForm
from inbox.models import Conversation, Message
from inbox.selectors import conversations_for, with_display

TABS = [("", "전체")] + [(value, label) for value, label in Message.Label.choices]


def _current_label(request):
    label = request.GET.get("label", "")
    return label if label in Message.Label.values else ""


def _list_context(request, selected=None):
    """메시지함 목록(왼쪽 칸) 컨텍스트. PC에서는 목록과 상세를 한 화면에 2단으로 보여준다 (#45)."""
    label = _current_label(request)
    return {
        "conversations": with_display(conversations_for(request.user, label)),
        "tabs": TABS,
        "current_label": label,
        "selected": selected,
    }


def _detail_context(request, conversation, form):
    return {
        **_list_context(request, selected=conversation),
        "conversation": conversation,
        "messages_list": conversation.messages.all(),
        "form": form,
    }


def inbox_list(request):
    """S13 메시지함. 분류 탭은 대화에서 가장 최근 수강생 메시지의 AI 분류 기준."""
    return render(request, "inbox/inbox_list.html", _list_context(request))


def conversation_detail(request, pk):
    """S14 대화 상세. 열면 수강생 메시지를 읽음 처리한다."""
    conversation = get_owned_or_404(Conversation.objects.select_related("student"), request.user, pk=pk)
    services.mark_read(conversation)  # 목록을 만들기 전에 읽음 처리해야 왼쪽 목록의 '안 읽음' 표시가 맞다
    return render(request, "inbox/conversation_detail.html", _detail_context(request, conversation, ReplyForm()))


@require_POST
def reply(request, pk):
    conversation = get_owned_or_404(Conversation.objects.select_related("student"), request.user, pk=pk)
    form = ReplyForm(request.POST)
    if not form.is_valid():
        return render(
            request, "inbox/conversation_detail.html", _detail_context(request, conversation, form), status=400
        )
    services.add_teacher_reply(conversation=conversation, body=form.cleaned_data["body"])
    messages.success(request, "답장을 보냈습니다.")
    label = _current_label(request)  # 메시지함 분류 필터를 답장 후에도 유지 (검증된 값만)
    url = reverse("inbox:detail", args=[conversation.pk])
    return redirect(f"{url}?label={label}" if label else url)


def new_student_message(request):
    """S15 수강생 메시지 추가 (시연용 A안). 저장 → 분류 → 대화 상세로 리다이렉트(PRG)."""
    form = StudentMessageForm(request.POST or None, teacher=request.user, initial=_initial(request))
    if request.method == "POST" and form.is_valid():
        message = services.add_student_message(teacher=request.user, **form.cleaned_data)
        if message.is_classified:
            messages.success(
                request,
                f"수강생 메시지를 저장했습니다. AI 분류: {message.get_predicted_label_display()} "
                f"({message.confidence_percent}%)",
            )
        else:
            messages.warning(
                request, "수강생 메시지를 저장했습니다. 지금은 AI 분류를 사용할 수 없어 '분류 불가'로 표시합니다."
            )
        return redirect("inbox:detail", pk=message.conversation_id)
    return render(request, "inbox/student_message_form.html", {"form": form})


def _initial(request):
    """수강생 상세에서 들어오면 수강생을 미리 선택 (?student=<id>, 내 수강생일 때만)."""
    student_id = request.GET.get("student", "")
    return {"student": student_id} if student_id.isdigit() else {}
