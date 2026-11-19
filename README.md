# LEMIT (레밋)

[![CI](https://github.com/yeeun5364/lemit/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yeeun5364/lemit/actions/workflows/ci.yml)

> **Lesson + Meet** · 배움과 사람이 만나는 공간
> 수업의 모든 흐름을 하나로

**LEMIT은 프리랜서 강사가 유동적인 레슨 일정과 진행 회차를 쉽고 체계적으로 관리할 수 있도록 돕는 웹서비스입니다.**

레슨마다 시간과 장소가 다르고 휴강·보강이 자주 생기는 상황에서, 일정만 적는 일반 캘린더를 넘어 **수업 일정과 실제 진행 상태(예정/완료/휴강/결석, 보강 연결), 진행 횟수, 수강생 기록을 하나로 연결**하는 것을 목표로 합니다.

> **현재 상태:** 기획 및 초기 설계 단계입니다. 실제 구현 여부는 아래 [구현 현황](#구현-현황)과 [devlog/](devlog/)를 기준으로 합니다.

> 📄 **상세 기획(문제 정의, 대상 사용자, 기능 우선순위, 기술 선택 이유, 아키텍처, 데이터 모델, 로드맵, 미확정 사항)은 [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md)에서만 관리합니다.** 이 README는 요약·현황·실행 방법만 다룹니다.

---

## 한눈에 보기

| 항목 | 내용 |
|---|---|
| 대상 사용자 | 개인 레슨을 운영하는 프리랜서 강사 |
| 핵심 문제 | 유동적인 일정·휴강·보강과 수업 회차 관리 |
| 발표용 MVP | 수강생 등록 → 일정 등록·조회 → 수강생 메시지 AI 분류 확인 → 완료 처리 → 진행·잔여 회차 확인 (발표: 2026-11-24) |
| AI 기능 | 직접 학습시킨 모델(TF-IDF + Logistic Regression)이 수강생 메시지를 휴강 요청·보강 요청·일정 변경·일반 메시지로 분류. 예측은 참고용이며 일정을 자동으로 바꾸지 않음 ([docs/ML.md](docs/ML.md)) |
| 기술 스택 (설계안) | Python 3.14 · Django 5.2 LTS · PostgreSQL 18 · scikit-learn · Docker · GitHub Actions · AWS Lightsail |
| 상세 | [기획 문서](docs/PROJECT_PLAN.md) — 기능 우선순위 §5, MVP 범위 §6, 기술 선택 이유 §7, 데이터 모델 §8, 로드맵 §9 |

## 구현 현황

> 실제로 동작을 확인한 기능만 ✅로 표시합니다. 갱신 시 [devlog/](devlog/)에도 기록합니다.
> 범례: ⬜ 미착수 · 🟡 진행 중 · ✅ 완료(검증됨)
> 구분: **MVP 핵심**(발표 시연 흐름, 먼저 구현) → **MVP 후속**(핵심 완료 후, 발표 전 여유가 있으면) → 확장 → 이후. 세부 우선순위는 GitHub 라벨 기준.

| 구분 | 항목 | 상태 | 비고 |
|---|---|---|---|
| MVP 핵심 | 강사 회원가입·로그인 | ✅ | UC-01. 회원가입·로그인·로그아웃, 기본 로그인 필수, 강사별 데이터 접근 도우미. 테스트 통과, main 반영 (PR #36, 2026-11-19) |
| MVP 핵심 | 수강생 등록·조회 | 🟡 | UC-02. 등록·수정·목록(검색·보관 포함)·상세·보관/해제, 강사 A/B 격리 테스트 통과, PR 검토 중 (#9). 잔여 회차 표시는 UC-03 이후 |
| MVP 핵심 | 레슨 일정 등록·조회 (월간·주간) | ⬜ | UC-03~05 |
| MVP 핵심 | 수업 완료 처리 | ⬜ | UC-06 |
| MVP 핵심 | 진행·잔여 회차 확인 | ⬜ | UC-07 |
| MVP 핵심 | 수강생 메시지 AI 분류 (메시지함) | 🟡 | UC-17. 가상 수강생 메시지 등록 → 학습한 모델로 분류 → 메시지함·대화 화면 배지, 분류 탭, 답장, 모델 불가 시 '분류 불가'. 테스트·개발 서버 확인, PR 검토 중 (#33). AWS 배포 후 확인은 #7 |
| ML | 메시지 분류 모델 학습·검증, Django 추론 모듈, `/healthz/` | 🟡 | v2 검증 macro F1 0.936. 학습·Django 모듈 연동까지 구현·테스트 통과, main 반영 완료 (PR #35, #32). **웹 화면에서 쓰는 기능(UC-17)과 AWS 배포 후 추론은 미완료** |
| MVP 후속 | 비밀번호 찾기·변경 | ⬜ | 메일 재설정 링크 (UC-15) |
| MVP 후속 | 라벨(색)로 수업 형태·사람 구분 | ⬜ | 라벨 없으면 회색, 상태는 모양으로 표시 (UC-16) |
| MVP 후속 | 휴강·보강 처리 | ⬜ | 발표 시연 2차 목표 (UC-08·09) |
| 확장 | 반복 일정 | ⬜ | |
| 이후 | 장소 지도 연동 | ⬜ | 프로젝트 기간 이후. 그전엔 회차별 장소 메모(UC-04) |
| 확장 | 수업일지·진도 기록 | ⬜ | |
| 인프라 | Django + PostgreSQL 연결 | ✅ | Django 5.2.19 + PostgreSQL 18, 커스텀 User(이메일 로그인), 테스트 6개 통과 (2026-11-19) |
| 인프라 | Docker 실행 환경 | 🟡 | web(gunicorn)+db 컨테이너, 이미지 안 모델 검증·추론, 헬스체크·스모크 테스트. PC에서 운영·개발 구성 모두 확인, PR 검토 중 (#5) |
| 인프라 | GitHub Actions (테스트) | 🟡 | 린트·PostgreSQL 18 테스트 워크플로 작성 (#6). 이미지 검증은 #5 이후 |
| 인프라 | AWS Lightsail 배포 | ⬜ | |

## 회차 상태와 차감 규칙

LEMIT이 일반 캘린더와 다른 핵심은 **각 수업 회차가 상태를 가지고, 그 상태에 따라 레슨권 잔여 횟수가 계산된다**는 점입니다.

> 세부 규칙은 2026-11-19에 확정했습니다(#2). 변경 시 이 표와 [docs/ERD.md](docs/ERD.md), [devlog/](devlog/)를 함께 갱신합니다.

| 회차 상태 | 의미 | 레슨권 차감 | 후속 처리 |
|---|---|---|---|
| 예정 | 등록되었지만 아직 진행 전인 수업 | ✗ | 수업 후 완료 처리 |
| 완료 | 실제로 진행된 수업 | ✓ | 수업일지 작성(확장). 일지가 없으면 완료 취소 가능 |
| 휴강 | 강사 사유 등으로 열리지 않은 수업 | ✗ | 보강 회차 등록 (보강 전까지 "보강 필요" 표시) |
| 결석 | 학생 사유로 불참 (당일 취소 포함) | 결석 처리 때 강사가 매번 선택 | 기본값 없음. **차감한 결석은 보강 불가** |

회차 상태는 위 **4가지뿐**입니다(`scheduled` / `completed` / `cancelled` / `absent`). **보강은 상태가 아닙니다.** 보강 회차는 원래 회차(휴강 또는 미차감 결석)에 연결된 일반 회차로, 똑같이 `예정` → `완료`로 진행되고 완료되면 1회 차감됩니다. 화면에서는 상태 배지 옆에 `보강` 표시를 붙여 구분합니다. ([ERD 2-1](docs/ERD.md))

**잔여 회차 계산**

```text
잔여 회차 = 레슨권 총 회차 − 차감된 회차 수
차감된 회차 = 완료 회차 수 (보강 회차의 완료 포함) + 차감한 결석 수
```

- 보강 회차도 진행되면 `완료` 상태가 되므로 **완료 회차 수에 한 번만** 들어갑니다. 보강을 따로 더하지 않습니다.
- **중복 차감 방지:** 이미 차감한 결석에는 보강을 등록할 수 없습니다. 보강은 휴강 또는 미차감 결석에만 연결됩니다.

**상태 변경 흐름**

```text
예정 ──수업 진행──▶ 완료
  │
  ├──강사 사유──▶ 휴강 ──보강 일정 등록──▶ 보강 회차: 예정 ──진행──▶ 완료(차감)
  │
  └──학생 사유──▶ 결석 ──┬─ 차감 → 종료 (보강 불가)
                        └─ 미차감 → 보강 일정 등록 가능
```

**확정한 세부 규칙 (2026-11-19, #2)**
- **결석 차감:** 기본값 없음. 결석 처리할 때 강사가 차감/미차감을 반드시 고릅니다.
- **완료 취소:** 잘못 누른 완료는 `예정`으로 되돌릴 수 있습니다. 단, 수업일지를 쓴 회차는 되돌릴 수 없습니다.
- **레슨권 소진:** 잔여 0이 되어도 자동 종료하지 않고 '소진'으로 표시합니다. 종료는 강사가 직접 하고, 새 레슨권을 추가하면 소진된 이전 레슨권의 종료를 제안합니다. 한 수강생이 진행 중인 레슨권을 여러 개 가질 수 있습니다.
- **보강 필요 표시:** 보강이 아직 없는 휴강·미차감 결석 회차에는 "보강 필요" 배지를 붙입니다.
- **차감한 결석:** 보강할 수 없습니다. (중복 차감 방지)
- **잔여 초과 일정:** 저장 후 기준으로 이 레슨권의 `예정` 회차 수(지금 저장하는 회차 포함, 수정이면 한 번만 셈)가 잔여 회차를 **초과(>)**하면 경고만 하고 저장은 허용합니다. 예: 잔여 3회에 예정 3회는 경고 없음, 예정이 4회가 되는 순간 경고.
- **시작 전 완료:** 허용하되 확인을 받습니다.

## 실행 방법

> 실행 방법은 두 가지입니다. **A. Docker만으로 실행**(web + db 컨테이너, #5)과 **B. PC 가상환경에서 Django 실행 + Docker PostgreSQL**(개발·테스트용).

### 필요한 환경

| 구분 | 버전 | 비고 |
|---|---|---|
| Python | **3.14** | Docker 이미지 `python:3.14-slim`. PC 가상환경(B 방법)으로 실행·테스트할 때만 PC에 설치 필요 |
| Django | **5.2 LTS** (5.2.19 고정) | 보안 지원 2028년 4월까지. Python 3.14는 5.2.8부터 지원 |
| PostgreSQL | **18** | Docker 이미지 `postgres:18` (2026-11 기준 최신 18.7) |
| DB 드라이버 | psycopg 3 (`psycopg[binary]` 3.3.9) | |
| Docker | Engine 29 이상, Compose v2 이상 | 개발 PC 확인 버전: Docker 29.9.1, Compose v5.6.0 |
| Git | 2.x | |

- **A. Docker만으로 실행:** Docker만 있으면 됩니다. PostgreSQL과 Python을 PC에 설치하지 않습니다.
- **B. PC 가상환경 + Docker PostgreSQL:** 테스트(`manage.py test`)·코드 검사·모델 재학습은 이 방법으로 합니다. Python 3.14와 Docker가 필요합니다.
- PC에 Python 3.15가 기본으로 잡혀 있으면 `py -3.14 -m venv .venv`처럼 버전을 지정해 가상환경을 만듭니다.
- 패키지 버전은 `requirements.txt`에 고정되어 있습니다.

### 환경변수 설정

`.env.example`을 `.env`로 복사한 뒤 값을 채웁니다.

| 셸 | 명령 |
|---|---|
| macOS/Linux, Git Bash | `cp .env.example .env` |
| Windows PowerShell | `Copy-Item .env.example .env` |
| Windows CMD | `copy .env.example .env` |

| 변수 | 설명 |
|---|---|
| `DJANGO_SECRET_KEY` | Django 시크릿 키 |
| `DJANGO_DEBUG` | 개발 시 `True`, 배포 시 `False` |
| `DATABASE_URL` | PostgreSQL 접속 정보. **실행 위치에 따라 호스트가 다름** (아래 표) |
| `EMAIL_HOST_USER` | 비밀번호 재설정 메일을 보낼 Gmail 주소 (배포 시) |
| `EMAIL_HOST_PASSWORD` | 위 Gmail 계정의 **앱 비밀번호** (실제 Gmail 비밀번호 아님) |

**`DATABASE_URL`의 호스트**

| Django를 어디서 실행하나 | 호스트 | 예 |
|---|---|---|
| PC 가상환경 (B 방법) | `127.0.0.1` | `postgres://lemit:비밀번호@127.0.0.1:5432/lemit` |
| Docker `web` 컨테이너 (A 방법) | `db` (Compose 서비스 이름) | `compose.yaml`이 `POSTGRES_*` 값으로 자동으로 만듦. `.env`에 따로 쓰지 않음 |

- 컨테이너 안에서 `127.0.0.1`은 자기 자신이라 PostgreSQL에 닿지 않습니다. 그래서 `compose.yaml`이 `web`의 `DATABASE_URL`을 `db` 호스트로 덮어씁니다. `POSTGRES_PASSWORD`는 URL에 그대로 들어가므로 영문·숫자·`-`·`_`만 씁니다(`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
- DB 포트는 개발용 `compose.dev.yaml`을 덧붙였을 때만 PC 안(`127.0.0.1:5432`)에 엽니다. **운영 구성(`compose.yaml`만 사용)은 DB 포트를 열지 않습니다** (#5, #7).
- 개발 환경에서는 메일을 실제로 보내지 않고 터미널에 출력합니다. `EMAIL_*` 값이 없어도 됩니다.
- 메일 계정을 바꿀 때는 `.env`의 `EMAIL_*` 값만 바꾸고 재시작하면 됩니다. 코드는 수정하지 않습니다.

`.env` 파일과 비밀번호·키 값은 **절대 커밋하지 않습니다.** (`.gitignore`에 포함)

### A. Docker만으로 실행 (#5)

```bash
# .env 준비 (위 "환경변수 설정")

# 개발 PC: 개발 설정(compose.dev.yaml)을 직접 덧붙임 → runserver, 코드 마운트, DB 포트 127.0.0.1:5432
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py migrate
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py createsuperuser
docker compose -f compose.yaml -f compose.dev.yaml exec web python scripts/smoke_test.py

# 운영 서버: compose.yaml만 (개발 파일을 지정하지 않음) → gunicorn, DB 포트 비공개
docker compose up -d --wait
docker compose exec web python manage.py migrate
docker compose exec web python scripts/smoke_test.py   # healthz·모델 상태·check·migrate --check·ml.verify·추론 1건
```

| 파일 | 용도 |
|---|---|
| `compose.yaml` | 공통·운영 기준. web(gunicorn) + db(PostgreSQL 18, 볼륨 `pgdata`). DB 포트 비공개, web은 서버 안 `127.0.0.1:8000`에만 공개, 헬스체크·재시작 정책·로그 크기 제한 |
| `compose.dev.yaml` | 개발 PC 전용. **자동으로 읽히지 않으며** `-f compose.yaml -f compose.dev.yaml`로 지정할 때만 적용. DB 포트 `127.0.0.1:5432` 공개, 코드 마운트, `runserver`, `DEBUG=True` |
| `Dockerfile` | `python:3.14-slim`, 의존성 레이어 분리, `collectstatic`, root가 아닌 사용자, 학습된 모델 파일(`ml/artifacts/`) 포함 |

- **운영 서버:** `docker compose up -d --wait`(= `compose.yaml`만). 개발 파일은 자동으로 섞이지 않습니다(PR #39 리뷰). `IMAGE_TAG`(커밋 SHA)로 이미지를 구분하고 이전 태그로 롤백합니다 (#7).
- web 포트를 바꾸려면 `.env`에 `WEB_PORT=8001`, gunicorn worker 수는 `GUNICORN_WORKERS`로 조정합니다.
- 컨테이너를 교체·재시작해도 데이터는 볼륨 `pgdata`에 남습니다. `docker compose down -v`는 **DB 데이터를 지우므로** 운영에서 쓰지 않습니다. CI의 `down -v`는 임시 환경 정리용입니다.

### B. PC 가상환경에서 실행 (개발·테스트)

```bash
# 1) 가상환경 만들기 (Python 3.14)
py -3.14 -m venv .venv                  # macOS/Linux: python3.14 -m venv .venv

# 2) 가상환경 켜기: 셸마다 명령이 다름
.\.venv\Scripts\Activate.ps1           # Windows PowerShell
.venv\Scripts\activate.bat             # Windows CMD
source .venv/Scripts/activate           # Windows Git Bash
source .venv/bin/activate               # macOS/Linux

# 3) 패키지 설치
pip install -r requirements.txt

# 4) 환경변수: .env.example을 .env로 복사(위 "환경변수 설정" 표)한 뒤
#    DJANGO_SECRET_KEY, POSTGRES_PASSWORD, DATABASE_URL의 비밀번호를 채움

# 5) PostgreSQL 18만 실행 (healthy가 될 때까지 대기)
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait db   # DB 포트를 PC에 열려면 개발 파일이 필요

# 6) DB 테이블 생성, 관리자 계정 생성, 서버 실행
python manage.py migrate
python manage.py createsuperuser        # 이메일·이름·비밀번호 입력
python manage.py runserver
```

접속: http://localhost:8000/ → 로그인 화면. 회원가입: http://localhost:8000/accounts/signup/ · 관리자: http://localhost:8000/admin/

- `DATABASE_URL`의 호스트는 `localhost` 대신 **`127.0.0.1`**을 씁니다. Windows에서 `localhost`가 IPv6(`::1`)로 먼저 연결을 시도해 DB 연결이 멈추는 문제가 있었습니다.
- `.env` 값에는 `$` 문자를 쓰지 않습니다. Docker Compose가 변수로 해석합니다.

### 테스트와 코드 검사

```bash
python manage.py test                   # 테스트 (PostgreSQL에 임시 DB test_lemit을 만들고 끝나면 지움)

pip install -r requirements-dev.txt     # 개발 도구(ruff) 설치
ruff check .                            # 린트
ruff format .                           # 포맷 자동 정리 (CI는 --check로 검사만)

python -m ml.verify                     # 저장된 메시지 분류 모델 검증 (파일·버전·데이터 일치, 점수 재현, 성능 기준)
python -m ml.train --version v3         # 모델 다시 학습 (데이터를 바꿨을 때만. 절차는 docs/ML.md §7)
```

### CI (GitHub Actions)

`main`에 push하거나 PR을 올리면 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)이 자동으로 실행됩니다.

| 작업 | 내용 |
|---|---|
| 린트 (ruff) | `ruff check`, `ruff format --check` |
| 테스트 (PostgreSQL 18) | PostgreSQL 18 서비스 컨테이너를 띄우고 `manage.py check`, 마이그레이션 누락 확인(`makemigrations --check`), ML 모델 검증(`python -m ml.verify`), `manage.py test` (추론·모델 파일 손상 테스트 포함) |

| Docker 이미지 (빌드·스모크) | `compose.yaml`로 이미지를 빌드하고 `up -d --wait`(db·web healthy 대기) → 마이그레이션 → `scripts/smoke_test.py` (#5) |

## 시연 자료

> 발표(2026-11-24) 전까지 채웁니다. 실제로 동작을 확인한 내용만 올립니다.

- **배포 주소:** (배포 후 추가)
- **데모 계정:** (데모용 가짜 데이터만 사용)
- **데모 데이터:** 관리 명령 `python manage.py seed_demo`로 넣을 예정 (아직 구현 전)

### 시연 시나리오

1. 강사 계정으로 로그인
2. 수강생 등록 (예: 피아노 레슨, 레슨권 8회)
3. 주간 레슨 일정 등록
4. 일정 목록·캘린더 조회
5. 수강생 메시지 추가 → 메시지함에서 AI 분류 배지(예: 보강 요청) 확인 → 분류 탭으로 걸러 보기
6. 수업 완료 처리 → 진행 1회 / 잔여 7회 확인
7. (2차 목표) 휴강 처리 → 보강 일정 등록 → 잔여 회차 유지 확인

### 화면 스크린샷

| 화면 | 이미지 |
|---|---|
| 수강생 목록 | (추가 예정) |
| 일정 조회 | (추가 예정) |
| 회차 완료·잔여 횟수 | (추가 예정) |

스크린샷은 `docs/images/`에 저장합니다.

## 저장소 구성

```text
lemit/
├── README.md              # 프로젝트 소개 (이 문서)
├── CLAUDE.md              # Claude Code 작업 지침 (devlog 자동 기록 규칙)
├── manage.py
├── requirements.txt       # 패키지 버전 고정
├── requirements-dev.txt   # 개발 도구 (ruff)
├── pyproject.toml         # ruff 설정
├── .github/workflows/     # CI (GitHub Actions)
├── Dockerfile             # web 이미지 (gunicorn, 모델 파일 포함)
├── compose.yaml           # web + db 컨테이너 (공통·운영 기준)
├── compose.dev.yaml       # 개발 PC 전용 설정 (-f로 직접 지정할 때만)
├── scripts/               # 컨테이너 헬스체크·스모크 테스트
├── .env.example           # 환경변수 예시 (.env는 커밋 안 함)
├── config/                # Django 프로젝트 설정 (settings, urls)
├── accounts/              # 강사 계정: 이메일 로그인 커스텀 User
├── inbox/                 # 메시지함 + 메시지 분류 추론 서비스 (classifier.py)
├── ml/                    # 메시지 분류 모델: 데이터(data/), 학습(train.py), 검증(verify.py), 모델 파일(artifacts/)
├── docs/
│   ├── PROJECT_PLAN.md    # 프로젝트 기획 기준 문서 (원문)
│   ├── USE_CASES.md       # 기능별 유스케이스 (GitHub 이슈와 1:1 연결)
│   ├── ERD.md             # 데이터 모델·ERD
│   ├── SCREENS.md         # 화면 목록·흐름·와이어프레임
│   └── ML.md              # 메시지 분류 모델 설계·학습 결과·한계
└── devlog/                # 날짜별 진행 기록 (발표 자료 작성용)
    ├── README.md          # 작성 규칙
    ├── _TEMPLATE.md       # 기록 템플릿
    └── YYYY-MM-DD.md      # 날짜별 기록
```

- 전체 기획 내용: [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md)
- 유스케이스: [docs/USE_CASES.md](docs/USE_CASES.md) → 작업 관리는 GitHub Issues (마일스톤 "발표 MVP (2026-11-24)")
- 진행 기록: [devlog/](devlog/)
