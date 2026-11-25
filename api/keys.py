"""API 키 원문 만들기·해시 (#102).

- 원문은 `lemit_` + 무작위 43자(32바이트, URL 안전 base64).
  앞의 표시 덕분에 코드·로그에 실수로 들어가도 검색으로 찾기 쉽다.
- DB에는 원문 대신 SHA-256 해시만 둔다. 비밀번호는 짧아 느린 해시(PBKDF2 등)가 필요하지만, 이 키는 256비트
  무작위 값이라 해시를 거꾸로 추측할 수 없어 빠른 해시로 충분하고, 매 요청 확인도 빠르다.
- 조회는 해시 값으로 한다. 공격자가 해시의 앞자리를 마음대로 고를 수 없으므로 DB 조회 시간으로 키를 알아낼 수 없다.
  찾은 뒤에도 hmac.compare_digest로 한 번 더 비교한다.
"""

import hashlib
import hmac
import secrets

PREFIX = "lemit_"
RANDOM_BYTES = 32
DISPLAY_LENGTH = 10  # 목록에 보여 줄 앞부분 길이 (예: lemit_Xk3f)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate() -> tuple[str, str, str]:
    """(원문, 표시용 앞부분, 해시)를 돌려준다. 원문은 화면에 한 번만 보여 주고 저장하지 않는다."""
    raw = PREFIX + secrets.token_urlsafe(RANDOM_BYTES)
    return raw, raw[:DISPLAY_LENGTH], hash_key(raw)


def looks_like_key(raw: str) -> bool:
    return raw.startswith(PREFIX) and 20 <= len(raw) <= 100 and raw.isascii() and raw.isprintable()


def same_hash(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
