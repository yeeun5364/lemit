"""API 오류 코드 목록 (#102). 응답 문구·고치는 방법·재시도 여부의 유일한 기준이다.

- code는 영문 고정값이다. AI 에이전트는 문장이 아니라 code로 재시도·사용자 확인 여부를 판단한다.
- message·hint는 한국어. 상황에 맞는 문장이 있으면 ApiError에서 바꿔 쓴다.
- 이 목록은 /api/docs/errors/ 화면에도 그대로 보여 준다.
- 알리지 않는 것: 다른 강사 데이터의 존재(404로 답함), 서버 내부(스택·SQL·경로), 요청 본문·개인정보, 키 값.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    message: str
    hint: str
    retryable: bool = False


KEY_PAGE = "LEMIT 웹 '설정 > API 키'"

ERRORS: dict[str, ErrorSpec] = {
    "validation_error": ErrorSpec(
        400, "입력값을 확인해 주세요.", "fields에 항목별로 무엇을 고치면 되는지 적혀 있습니다."
    ),
    "invalid_json": ErrorSpec(
        400,
        "요청 본문이 올바른 JSON이 아닙니다.",
        "따옴표·쉼표·괄호를 확인하고 Content-Type: application/json 헤더와 함께 보내 주세요.",
    ),
    "unsupported_media_type": ErrorSpec(
        415, "JSON 형식의 요청만 받을 수 있습니다.", "Content-Type: application/json 헤더를 보내 주세요."
    ),
    "not_acceptable": ErrorSpec(
        406, "JSON 형식으로만 응답할 수 있습니다.", "Accept 헤더를 application/json으로 보내거나 빼 주세요."
    ),
    "not_authenticated": ErrorSpec(
        401,
        "API 키가 없습니다.",
        f"Authorization: Bearer <키> 헤더를 보내 주세요. 키는 {KEY_PAGE}에서 발급합니다.",
    ),
    "invalid_api_key": ErrorSpec(
        401,
        "유효하지 않은 API 키입니다.",
        f"키 전체를 빠짐없이 복사했는지 확인해 주세요. 폐기된 키일 수 있으니 {KEY_PAGE}에서 상태를 확인하고, "
        "필요하면 새로 발급해 주세요.",
    ),
    "api_key_expired": ErrorSpec(
        401, "API 키가 만료되었습니다.", f"만료된 키는 연장할 수 없습니다. {KEY_PAGE}에서 새 키를 발급해 주세요."
    ),
    "insufficient_scope": ErrorSpec(
        403,
        "이 API 키에는 이 요청에 필요한 권한이 없습니다.",
        f"{KEY_PAGE}에서 필요한 범위를 고른 키를 새로 발급해 사용해 주세요.",
    ),
    "account_not_allowed": ErrorSpec(
        403, "이 계정은 API를 사용할 수 없습니다.", "API는 강사 계정만 사용할 수 있습니다."
    ),
    "permission_denied": ErrorSpec(
        403, "이 요청을 수행할 권한이 없습니다.", "API 키의 범위와 계정 상태를 확인해 주세요."
    ),
    "not_found": ErrorSpec(
        404,
        "요청한 주소나 대상을 찾을 수 없습니다.",
        "주소와 번호를 확인해 주세요. 내 데이터가 아니면 찾을 수 없는 것으로 응답합니다. "
        "API 목록은 /api/docs/ 에 있습니다.",
    ),
    "method_not_allowed": ErrorSpec(
        405, "이 주소는 이 HTTP 메서드를 받지 않습니다.", "응답의 Allow 헤더에 있는 메서드로 요청해 주세요."
    ),
    "conflict": ErrorSpec(
        409, "현재 상태와 충돌해 요청을 처리할 수 없습니다.", "대상의 현재 상태를 조회한 뒤 다시 판단해 주세요."
    ),
    "rate_limited": ErrorSpec(
        429,
        "요청이 너무 많습니다.",
        "retry_after 초가 지난 뒤 다시 시도해 주세요. 같은 요청을 짧은 간격으로 반복하지 않도록 해 주세요.",
        retryable=True,
    ),
    "server_error": ErrorSpec(
        500,
        "서버 오류가 발생했습니다.",
        "잠시 후 다시 시도해 보고, 계속되면 request_id를 LEMIT 운영자에게 알려 주세요.",
    ),
}
