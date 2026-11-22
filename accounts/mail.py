"""메일 백엔드 (UC-15, PR #68 리뷰 P1).

운영(DEBUG=False)에서 SMTP(EMAIL_HOST_USER)를 설정하지 않았을 때 쓰는 '발송 안 함' 백엔드.
콘솔 백엔드를 쓰면 비밀번호 재설정 링크(일회용 인증 정보)가 컨테이너 로그·stdout에 남으므로,
메일 내용·받는 사람·링크는 어디에도 쓰지 않고 '보내지 않았다'는 경고만 남긴다.
화면은 Django 기본 흐름 그대로라 가입 여부와 관계없이 같은 안내가 나온다. 비상시에는 운영자가 /admin/에서 재설정.
"""

import logging

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class DisabledEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        count = len(list(email_messages or []))
        if count:
            logger.warning(
                "메일 발송이 꺼져 있어 %d건을 보내지 않았습니다 (운영 .env에 EMAIL_HOST_USER·EMAIL_HOST_PASSWORD 필요)",
                count,
            )
        return 0
