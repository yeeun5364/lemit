# LEMIT 외부 API 사용 안내

강사 본인의 AI 에이전트·스크립트가 LEMIT 데이터를 읽고 쓸 수 있는 HTTP API입니다. 설계 결정과 이유는 [PROJECT_PLAN.md](PROJECT_PLAN.md)와 이슈 #102(공통 규칙)·#110(로드맵)에 있습니다.

> 이 문서는 사용법(호출 방법·예시·오류 대응)의 기준입니다. 엔드포인트마다 요청·응답 필드는 `/api/docs/`(Swagger, 코드에서 자동 생성)가 기준이고, 오류 코드 설명은 `/api/docs/errors/`가 기준입니다(`api/errors.py` 하나에서 응답과 문서를 함께 만듭니다).

## 1. 엔드포인트 현황

| 메서드·경로 | 기능 | 필요 범위 | 상태 | 이슈 |
|---|---|---|---|---|
| `GET /api/v1/me/` | 연결 확인 (키 주인·범위·만료일) | 없음 (유효한 키면 됨) | 🟡 main 병합(PR #113), 운영 확인 전 | #102 |
| `POST /api/v1/messages/classify/` | 메시지 한 건 분류 | `read` | 🟡 로컬 테스트 통과, PR 리뷰 중 | #101 |
| 수강생·일정·잔여 회차 조회 | | `read` | ⬜ | #103~#105 |
| 일정 변경 | | `sessions:write` | ⬜ | #106 |
| 메시지 발송 | | `messages:send` | ⬜ | #107 |

문서 (키 없이 공개): `/api/docs/`(Swagger UI), `/api/schema/`(OpenAPI), `/api/docs/errors/`(오류 코드).

## 2. API 키 발급

1. LEMIT 웹에 강사 계정으로 로그인 → 사이드바 **설정 > API 키** → **새 키 발급**
2. 이름, 범위, 유효 기간(30·90·180일·1년)을 고르고 현재 비밀번호를 입력합니다. 비밀번호를 15분에 5번 틀리면 발급·연장이 잠시 막힙니다 (두 화면이 횟수를 함께 씀).
   - `read` (읽기): 조회·분류
   - `sessions:write` (일정 변경)
   - `messages:send` (메시지 발송): 수강생에게 전달되어 되돌릴 수 없으므로 따로 고릅니다.
3. 발급 직후 화면에 **키 원문(`lemit_…`)이 한 번만** 나옵니다. 바로 복사해 안전한 곳(환경 변수, 비밀 저장소)에 두세요. LEMIT은 해시만 저장하므로 다시 보여 줄 수 없습니다.
4. 만료 14일 전부터 목록과 대시보드에 '곧 만료'가 표시됩니다. 만료 전에는 **연장**(키 값 그대로), 만료 후에는 새로 발급합니다. 유출이 의심되면 **폐기**하면 즉시 거절됩니다.

키는 강사당 사용 가능한 것만 5개까지 둘 수 있습니다. 수강생 계정은 발급할 수 없습니다.

## 3. 호출 방법

- 주소: `https://lemit.eta.pyhub.kr/api/v1/…` (로컬은 `http://localhost:8000/api/v1/…`)
- 인증: `Authorization: Bearer <키>` 헤더만 받습니다. 쿼리 문자열(`?key=`)과 웹 로그인 세션은 인증으로 쓰지 않습니다.
- 본문: `Content-Type: application/json`, UTF-8
- 시간: `+09:00`이 붙은 ISO 8601
- 모든 응답에 `X-Request-ID` 헤더가 있습니다. 문의할 때 이 값을 알려 주세요.

아래 예시는 키를 환경 변수에 두었다고 가정합니다.

```bash
export LEMIT_API_KEY="lemit_..."
export LEMIT="https://lemit.eta.pyhub.kr"
```

### 연결 확인 `GET /api/v1/me/`

```bash
curl -s "$LEMIT/api/v1/me/" -H "Authorization: Bearer $LEMIT_API_KEY"
```

```json
{
  "teacher": {"name": "김강사"},
  "api_key": {
    "name": "에이전트",
    "prefix": "lemit_Xk3f",
    "scopes": ["read"],
    "scopes_display": ["읽기"],
    "created_at": "2026-11-25T13:00:00+09:00",
    "expires_at": "2027-02-23T13:00:00+09:00",
    "expires_soon": false
  }
}
```

### 메시지 분류 `POST /api/v1/messages/classify/` (#101)

웹 메시지함과 **같은 모델·같은 기준**(`inbox/classifier.py`)으로 수강생 메시지 한 건을 분류합니다. 결과는 참고용이고, 메시지·회차·레슨권을 저장하거나 바꾸지 않습니다.

```bash
curl -s -X POST "$LEMIT/api/v1/messages/classify/" \
  -H "Authorization: Bearer $LEMIT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "다음 주 화요일 수업 목요일로 옮길 수 있을까요?"}'
```

```json
{
  "label": "RESCHEDULE",
  "label_display": "일정 변경",
  "confidence": 0.812,
  "is_low_confidence": false,
  "scores": {"RESCHEDULE": 0.812, "MAKEUP": 0.104, "CANCELLATION": 0.061, "GENERAL": 0.023},
  "truncated": false,
  "model_version": "v2"
}
```

(수치는 예시입니다. 실제 값은 모델이 계산합니다.)

| 필드 | 뜻 |
|---|---|
| `label` | 가장 확률이 높은 분류: `CANCELLATION`(휴강 요청)·`MAKEUP`(보강 요청)·`RESCHEDULE`(일정 변경)·`GENERAL`(일반 메시지) |
| `confidence` | `label`의 확률 (0~1, 소수 셋째 자리) |
| `is_low_confidence` | `confidence`가 0.5 미만이면 `true`. 이때는 강사에게 확인하는 것을 권합니다 |
| `scores` | 4개 분류 각각의 확률, 높은 순. 1·2위가 비슷하면 애매한 메시지입니다 |
| `truncated` | 500자를 넘어 앞 500자만 분류했으면 `true` |
| `model_version` | 분류에 쓴 모델 버전. `/healthz/`의 `message_classifier.version`과 같습니다 |

입력 규칙:
- `text`는 문자열, 앞뒤 공백을 뺀 길이 1~1000자 (메시지 최대 길이와 같음)
- 한 요청에 한 건만 분류합니다

## 4. 오류 응답

모든 오류는 같은 형식입니다. **분기는 `message` 문장이 아니라 `code`로 하세요.** `message`·`hint`는 사람이 읽는 한국어 문장이라 바뀔 수 있습니다.

```json
{
  "error": {
    "code": "validation_error",
    "message": "입력값 1개를 확인해 주세요.",
    "hint": "fields에 항목별로 무엇을 고치면 되는지 적혀 있습니다.",
    "fields": [{"field": "text", "code": "max_length", "message": "현재 1234자입니다. 1000자 이하로 줄여 주세요."}],
    "retryable": false,
    "docs": "https://lemit.eta.pyhub.kr/api/docs/errors/#validation_error",
    "request_id": "req_3f9a1c2b7d4e"
  }
}
```

자주 만나는 경우와 대응:

| 상황 | 상태·code | 대응 |
|---|---|---|
| 헤더 없음 | 401 `not_authenticated` | `Authorization: Bearer <키>` 추가 |
| 키 오타·폐기된 키 | 401 `invalid_api_key` | 키를 다시 확인하거나 새로 발급 (폐기 여부는 알려 주지 않음) |
| 만료된 키 | 401 `api_key_expired` | 메시지에 만료 날짜가 있음. 새로 발급 |
| 범위 부족 | 403 `insufficient_scope` | 메시지에 필요한 범위와 키의 범위가 있음. 그 범위를 고른 키로 발급 |
| 다른 강사의 데이터·없는 번호 | 404 `not_found` | 존재 여부를 드러내지 않으려고 403 대신 404로 답함 |
| 입력값 오류 | 400 `validation_error` | `fields`를 보고 고침 |
| JSON 문법 오류 | 400 `invalid_json` | 따옴표·쉼표·괄호 확인 |
| 요청이 너무 많음 | 429 `rate_limited` | `retry_after`초(또는 `Retry-After` 헤더) 뒤 재시도 |
| 분류 모델 사용 불가 | 503 `model_unavailable` | 잠시 뒤 재시도 (`retryable: true`) |
| 서버 오류 | 500 `server_error` | `request_id`를 운영자에게 전달 |

`retryable`이 `true`인 오류만 자동 재시도하세요. 나머지는 같은 요청을 반복해도 결과가 같습니다.

## 5. 요청 제한

- 키마다 분당 60회
- 같은 IP에서 키 인증 실패가 15분에 20번을 넘으면, 그 IP의 키 인증을 잠시 막습니다 (올바른 키도 거절)

## 6. 보안 원칙

- 키 원문은 저장하지 않습니다 (SHA-256 해시만). 서버 접근 로그에도 요청 본문·키·`Authorization` 헤더·쿼리 문자열을 남기지 않습니다.
- 브라우저에서 직접 호출하는 용도가 아니므로 CORS 헤더를 보내지 않습니다. 키를 웹 페이지·앱 코드·저장소에 넣지 마세요.
- 외부 서비스가 강사 승인을 받아 접근하는 방식(OAuth)과 MCP 연동은 #109에서 다룹니다.
