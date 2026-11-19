"""UC-17 메시지함 (S13 목록, S14 대화 상세·답장, S15 수강생 메시지 추가).

모든 조회·POST는 로그인한 강사의 대화에서만 찾는다 (accounts.permissions). 다른 강사의 대화는 404.
"""

from django.contrib import messages
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import get_owned_or_404, scope_to_teacher
from inbox import services
from inbox.forms import ReplyForm, StudentMessageForm
from inbox.models import Conversation, Message

TABS = [("", "전체")] + [(value, label) for value, label in Message.Label.choices]


def _conversations_for(teacher):
    latest_student_msg = Message.objects.filter(conversation=OuterRef("pk"), sender=Message.Sender.STUDENT).order_by(
        "-sent_at", "-id"
    )
    return (
        scope_to_teacher(Conversation.objects.select_related("student"), teacher)
        .annotate(
            latest_label=Subquery(latest_student_msg.values("predicted_label")[:1]),
            latest_confidence=Subquery(latest_student_msg.values("confidence")[:1]),
            latest_body=Subquery(latest_student_msg.values("body")[:1]),
            unread=Count("messages", filter=Q(messages__sender=Message.Sender.STUDENT, messages__read_at__isnull=True)),
        )
        .order_by("-last_message_at", "-id")
    )


def inbox_list(request):
    """S13 메시지함. 분류 탭은 대화에서 가장 최근 수강생 메시지의 AI 분류 기준."""
    label = request.GET.get("label", "")
    if label not in Message.Label.values:
        label = ""
    conversations = _conversations_for(request.user)
    if label:
        conversations = conversations.filter(latest_label=label)
    conversations = list(conversations)
    displays = dict(Message.Label.choices)
    for c in conversations:
        c.latest_display = displays.get(c.latest_label, "")
    return render(
        request, "inbox/inbox_list.html", {"conversations": conversations, "tabs": TABS, "current_label": label}
    )


def conversation_detail(request, pk):
    """S14 대화 상세. 열면 수강생 메시지를 읽음 처리한다."""
    conversation = get_owned_or_404(Conversation.objects.select_related("student"), request.user, pk=pk)
    services.mark_read(conversation)
    return render(
        request,
        "inbox/conversation_detail.html",
        {"conversation": conversation, "messages_list": conversation.messages.all(), "form": ReplyForm()},
    )


@require_POST
def reply(request, pk):
    conversation = get_owned_or_404(Conversation.objects.select_related("student"), request.user, pk=pk)
    form = ReplyForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "inbox/conversation_detail.html",
            {"conversation": conversation, "messages_list": conversation.messages.all(), "form": form},
            status=400,
        )
    services.add_teacher_reply(conversation=conversation, body=form.cleaned_data["body"])
    messages.success(request, "답장을 보냈습니다.")
    return redirect("inbox:detail", pk=conversation.pk)


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
