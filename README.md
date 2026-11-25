# LEMIT (레밋)

[![CI](https://github.com/yeeun5364/lemit/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yeeun5364/lemit/actions/workflows/ci.yml)

> **Lesson + Meet** · 배움과 사람이 만나는 공간
> 수업의 모든 흐름을 하나로

**LEMIT은 프리랜서 강사가 유동적인 레슨 일정과 진행 회차를 쉽고 체계적으로 관리할 수 있도록 돕는 웹서비스입니다.**

레슨마다 시간과 장소가 다르고 휴강·보강이 자주 생기는 상황에서, 일정만 적는 일반 캘린더를 넘어 **수업 일정과 실제 진행 상태(예정/완료/휴강/결석, 보강 연결), 진행 횟수, 수강생 기록을 하나로 연결**하는 것을 목표로 합니다.

> **현재 상태:** 발표 MVP 핵심 기능을 구현하고 운영 서버에 배포하는 단계입니다 (2026-11-20). 실제 구현 여부는 아래 [구현 현황](#구현-현황)과 [devlog/](devlog/)를 기준으로 합니다.

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
| MVP 핵심 | 강사 회원가입·로그인 | ✅ | UC-01. 회원가입·로그인·로그아웃, 기본 로그인 필수, 강사별 데이터 접근 도우미. 테스트 통과, main 반영 (PR #36, 2026-11-19). 로그인 시도 횟수 제한(15분에 이메일 5번·IP 20번 실패, 비밀번호 재설정 요청도) 추가, main 반영 (PR #70, 2026-11-22) (#7) |
| MVP 핵심 | 수강생 등록·조회 | ✅ | UC-02. 등록·수정·목록(검색·보관 포함, 레슨권별 잔여 회차)·상세·보관/해제, 강사 A/B 격리 테스트. 보관은 남은 예정 회차 삭제와 한 트랜잭션(실패 시 둘 다 되돌림), 보관 해제는 지운 예정 회차를 되살리지 않음. main 반영 (PR #37·#59·#61, 2026-11-20) (#9) |
| MVP 핵심 | 레슨권 등록·종료 | ✅ | UC-03. 수강생 상세에서 레슨권 추가(레슨명·총 회차·시작일), 진행 중 여러 개, 소진 표시·새 레슨권 등록 때 종료 제안, 직접 종료. 잔여 회차는 회차 상태로 계산(저장 안 함). 테스트·브라우저 확인 통과, main 반영 (PR #55, 리뷰 반영 PR #57, 2026-11-20) (#10). 레슨권 추가 때 이전 레슨명 기본 선택 + '직접 입력'(#75, PR 검토 전) |
| MVP 핵심 | 회차(일정) 등록·수정·삭제 | ✅ | UC-04. 일정 추가(수강생 상세에서 레슨권 미리 선택, 종료 기본 +1시간), 회차 상세, `예정`만 수정·삭제, 겹침·잔여 초과는 저장 후 경고, 수강생 상세 회차 이력, 보관 시 남은 예정 회차 삭제. 수정은 같은 수강생의 레슨권으로만, 저장·삭제는 수강생 행 잠금 후 재확인. 테스트·브라우저 확인 통과, main 반영 (PR #59, 2026-11-20) (#11) |
| MVP 핵심 | 일정 조회 (월간·주간) | ✅ | UC-05. `/schedule/` 월간 달력(회차 있는 날 점, 날짜 선택 시 그날 회차 시간순)·주간(일~토), 이전·다음·오늘, 수강생 필터, 상태 모양·보강 표시, 회차 누르면 상세. 대시보드 '오늘 수업'·'오늘의 일정'을 실제 회차로, 사이드바 '수업 일정' 활성화. 테스트·브라우저 확인 통과, main 반영 (PR #61, 2026-11-20) (#12) |
| MVP 핵심 | 수업 완료 처리 | ✅ | UC-06. 회차 상세에서 완료 처리(1회 차감, "진행 N / 잔여 M" 알림, 잔여 초과 시 '회차 초과' 경고, 시작 전이면 확인창)·완료 취소(잔여 복구, 보관 수강생 불가). 두 번 눌러도 한 번만 차감. 테스트·브라우저 확인 통과, main 반영 (PR #60, 2026-11-20) (#13) |
| MVP 핵심 | 진행·잔여 회차 확인 | ✅ | UC-07. 수강생 상세 레슨권 카드(총·진행·잔여, 회차 이력)와 수강생 목록 '잔여 회차' 열(진행 중 레슨권별, 2회 이하 강조, 0 이하 '소진'·초과 회차). 테스트·브라우저 확인 통과, main 반영 (PR #62, 2026-11-21) (#14) |
| MVP 핵심 | 수강생 메시지 AI 분류 (메시지함) | ✅ | UC-17. 가상 수강생 메시지 등록 → 학습한 모델로 분류 → 메시지함·대화 화면 배지, 분류 탭, 답장, 모델 불가 시 '분류 불가'. 분류해도 회차·레슨권은 바뀌지 않음(테스트). main 반영 (PR #38), 운영 서버 스모크에서 모델 v2 추론 확인(2026-11-20). 데모 데이터(`seed_demo`, #24)에 분류 4종 메시지(실제 모델 분류, '확신 낮음' 예시 포함). 운영 서버에서 `seed_demo` 실행 후 화면 확인 완료(사용자, 2026-11-22) (#33) |
| MVP 핵심 | 수강생 화면: 내 수업·강사와 메시지 (발표용) | ✅ | UC-18. 수강생 기록에 연결된 수강생 계정(`Student.account`)으로 공통 로그인 → `/me/` 내 수업(레슨권 잔여, 다가오는·지난 회차의 날짜·시간·장소·상태), 메시지(담당 강사에게 보내면 기존 모델이 분류, 수강생에게는 분류 비공개), 강사 답장 확인·이어 보내기. 수강생 계정은 강사 화면에 접근 불가(서버 미들웨어, GET→`/me/`, POST→403), 다른 수강생 대화 404. `seed_demo`가 데모 수강생 계정을 '김하늘'에 연결. 테스트 통과, 로컬 임시 DB에서 수강생→강사(분류 배지)→수강생 답장 흐름 확인, main 반영(PR #91), `v0.1.4` 운영 배포 후 사용자가 수강생 화면 확인(2026-11-23). 내 수업 월간 달력(본인 회차만, 날짜를 누르면 그날 수업, #92). 메시지는 제목 없는 카톡형 대화방 하나 + 자동 갱신(#95, **PR 검토 전**). QR 연결은 #84 (#80) |
| 확장 | 일회용 QR로 수강생 계정 연결 | 🟡 | UC-19. 강사가 수강생 상세에서 10분짜리 일회용 QR 발급(취소·재발급) → 수강생이 휴대폰으로 스캔해 로그인·가입(가입 후 같은 화면으로 복귀) → 연결 요청 → 강사가 요청 계정(이름·이메일) 확인 후 승인·거절 → 연결되면 내 수업·메시지. 강사는 연결 해제 가능(즉시 접근 차단). 만료·취소·사용된 초대와 강사·관리자·이미 연결된 계정은 거부, 연결은 행 잠금 + `Student.account` UNIQUE로 한 번만. QR은 서버에서 SVG로 생성(`segno`). 테스트 통과, 로컬 임시 DB에서 신규 가입 → 요청 → 승인 → `/me/` 확인, **PR 검토 전**, 실제 휴대폰·운영 확인은 아직 (#84) |
| ML | 메시지 분류 모델 학습·검증, Django 추론 모듈, `/healthz/` | ✅ | v2 검증 macro F1 0.936. 학습·검증·저장, Django 추론 모듈, `/healthz/`의 모델 상태, CI 점수 재현 검증. main 반영 (PR #34·#35), 운영 서버에서 v2 추론 확인 (2026-11-20, #32) |
| MVP 후속 | 비밀번호 찾기·변경 | 🟡 | UC-15. 로그인 화면 '비밀번호를 잊으셨나요?' → 메일 재설정 링크(1회용, 1시간), 가입 여부를 드러내지 않음. 사이드바 '설정 > 비밀번호 변경'. 개발은 메일을 콘솔에, 운영은 `.env`의 Gmail 앱 비밀번호로 SMTP 발송(미설정이면 보내지 않고 링크도 로그에 남기지 않음). 테스트 통과, main 반영 (PR #26·#68, 2026-11-22). 로그인 ID = 가입 이메일 안내·입력한 이메일 확인·다시 입력 추가(#78, PR 검토 전). **남은 것: 운영 메일 발송은 서버 `.env` 설정 후 확인** (#25) |
| MVP 후속 | 라벨(색)로 수업 형태·사람 구분 | ✅ | UC-16. 상단 '라벨'(S12 `/labels/`)에서 이름 + 8색 추가·수정·삭제(삭제 시 레슨권은 라벨 없음, 기록 유지), 레슨권 등록 때 라벨 선택·카드에서 변경, 일정 막대·달력 점·대시보드에 라벨 색(없으면 회색), 일정 라벨 필터. 상태는 모양으로만. 데모 데이터에 라벨 3개. 테스트 통과, 로컬 브라우저 확인(데모 데이터, 데스크톱·모바일 폭), main 반영 (PR #28·#67, 2026-11-22) (#27). 운영에서 '라벨이 달력에 안 보임' 보고(라벨이 붙은 레슨권의 회차 0개, 렌더링 오류는 미재현)에 맞춰 라벨 목록·레슨권 저장·일정 화면에 달력 표시 상태 안내 추가(#77, PR 검토 전) |
| MVP 후속 | 휴강·보강 처리 | ✅ | UC-08·09. 회차 상세 '휴강 처리'(사유 선택, 차감 없음)·'휴강 취소'(보강이 없을 때), 보강 없는 휴강·미차감 결석에 '보강 필요' 배지와 수강생 상세 모아 보기, '보강 등록'(원래 회차와 연결, 한 회차에 하나, 차감 결석 불가, 보강 완료 시 1회 차감). 테스트 통과, 로컬 브라우저 확인, main 반영 (PR #22·#69, 2026-11-22) (#15·#16) |
| 확장 | 결석 처리 | ✅ | UC-10. 회차 상세 '결석 처리'(차감/미차감을 기본값 없이 반드시 선택, 사유 선택), 차감 결석은 1회 차감·보강 불가, 미차감 결석은 '보강 필요'로 보강 등록, '결석 취소'(보강이 없을 때, 차감 복구). 데모 데이터에 차감·미차감 결석 예시. 테스트 통과, 로컬 브라우저 확인, main 반영 (PR #72, 2026-11-22) (#17) |
| 확장 | 회차 일정 변경 | ✅ | UC-11. 예정 회차의 날짜·시간·장소를 '수정'으로 바꾸면 바꾸기 전·후 일정을 이력(`SessionChangeLog`)으로 남기고, 회차 상세 '일정 변경 이력'에 최신순으로 표시(장소만 바뀐 경우 구분). 레슨권만 옮긴 수정은 남기지 않음. 변경 전 값은 수강생 행 잠금 뒤 다시 읽은 값. 테스트 통과, 로컬 화면 확인(임시 DB, 데스크톱·390px 폭), main 반영 (PR #73, 2026-11-22) (#18) |
| 확장 | 반복 일정 | 🟡 | UC-12 일부(#76): 레슨권 등록 때 '회차도 함께 만들기'로 첫 수업 날짜·시간·반복 요일에 맞춰 총 회차만큼 예정 회차 생성(라벨 색 그대로 달력 표시, 겹치면 경고). 테스트 통과, 로컬 화면 확인(임시 DB), **PR 검토 전**. 미리 보기·반복 규칙 저장·일괄 수정은 아직 (#19) |
| 외부 API | 공통 기반: API 키·인증·오류 형식·요청 제한·문서 | 🟡 | #102. 사이드바 '설정 > API 키'에서 발급(비밀번호 재확인, 범위 읽기·일정 변경·메시지 발송, 만료 30·90·180일·1년)·같은 값으로 연장·폐기, 원문은 발급 직후 한 번만(DB엔 SHA-256 해시). `Authorization: Bearer` 키 인증만, 연결 확인 `GET /api/v1/me/`, 모든 오류를 공통 JSON(`code`·`message`·`hint`·`fields`·`retryable`·`request_id`)으로, 키당 분당 60회·인증 실패 IP당 15분 20회(DB 캐시로 worker 공유), 접근 로그에 본문·키 미기록, 문서 `/api/docs/`·오류 코드 `/api/docs/errors/`. 로컬 테스트 통과(2026-11-25). **남은 것: PR 리뷰·병합, 운영 배포 후 HTTPS 호출 확인** |
| 이후 | 장소 지도 연동 | ⬜ | 프로젝트 기간 이후. 그전엔 회차별 장소 메모(UC-04) |
| 확장 | 수업일지·진도 기록 | ⬜ | |
| UI | 디자인 시스템·공통 레이아웃 (Calm & Friendly) | ✅ | #45. 사이드바·접이식 메뉴, 대시보드(수강생·메시지·오늘 수업 실제 수치), 메시지함 PC 2단·모바일 분리, 수강생 표/카드, 수업 일정 달력. main 반영 (PR #47, 일정 화면 PR #61). 기준: [SCREENS.md §5](docs/SCREENS.md) |
| 인프라 | Django + PostgreSQL 연결 | ✅ | Django 5.2.19 + PostgreSQL 18, 커스텀 User(이메일 로그인), 테스트 6개 통과 (2026-11-19) |
| 인프라 | Docker 실행 환경 | ✅ | web(gunicorn)+db(+proxy) 컨테이너, 이미지 안 모델 검증·추론, 헬스체크·스모크 테스트. 운영 서버(Lightsail)에서 동작 중 (#5) |
| 인프라 | GitHub Actions (테스트) | ✅ | 린트·PostgreSQL 18 테스트·Docker 이미지(빌드·스모크) 3개 작업, main 병합 필수 검사 (#6) |
| 인프라 | AWS Lightsail 배포 | ✅ | **첫 수동 배포 성공 (2026-11-20, `c848f2c`)**: Amazon Linux 2023, db·web·proxy healthy, 스모크 6항목(AI 모델 v2 추론 포함)·공개 경로 확인 통과, 외부에서 5432·8000 차단 확인. 도메인 `lemit.eta.pyhub.kr`에 HTTPS 적용(Let's Encrypt 인증서, HTTP→HTTPS 308, 보안 쿠키, HSTS는 아직 0). 외부 `/healthz/` ok·모델 v2 확인 (2026-11-21). 브라우저 기능 확인·관리자 계정 생성은 사용자 진행 (#48, [docs/DEPLOY.md](docs/DEPLOY.md)) |
| 인프라 | GitHub Actions 자동 배포(CD) | 🟡 | **태그 파이프라인 (main 반영 PR #74, 2026-11-23, `v0.1.3`으로 운영 배포 확인):** `vX.Y.Z` 태그 push → Release tag(진입점)가 main 포함·CI 완료 확인 → Deploy 한 실행에서 `plan → test → train → parity → image → release → deploy`. 태그 커밋으로 학습한 모델을 넣어 시험한 이미지를 GHCR에 게시하고, Release에 기록한 digest를 서버가 받아 실행(서버 빌드 없음). 단계 판정·서버 `deploy-image`는 자동 테스트, 이미지 빌드·게시·digest 재수신·서버 이미지 배포·롤백은 로컬 Docker로 확인. `v0.1.3`(`4887c11`)으로 첫 운영 실행 성공(2026-11-23, Deploy run 45101961237): Release digest = 서버 실행 이미지, 이미지 안 모델 해시 = Release 기록, 공개 `/healthz/` 모델 `v2+v0.1.3`. 첫 시도는 GHCR 패키지 Actions 권한 미연결로 게시 실패 → 설정 후 재실행. **버전 태그 배포 (#71, 서버 빌드 방식):** main push는 CI만. 이 방식으로 `v0.1.2` 태그 자동 배포 성공(2026-11-23, Deploy run 45088524424). **Self-hosted Runner 방식 (#56):** CI(GitHub-hosted)가 성공한 main 커밋만(실제 CI 실행 ID와 작업 결과 확인). 검증은 GitHub-hosted 작업, 배포는 러너 전용 WSL 배포판(라벨 `lemit-deploy`)이 격리 확인 후 SSH 요청 한 줄만. 배포 전용 키는 저장소 밖 고정 경로 강제 명령(서버에서 main 포함 재확인). PR 트리거 금지 등 규칙을 CI에서 검사. 공개 저장소라 위험을 줄이는 구성이며 완전한 차단은 아님. 스크립트 테스트 통과, 러너·서버 강제 명령·GitHub 설정은 `v0.1.1`(SSH `check`)·`v0.1.2`(자동 배포) 실행으로 확인 ([docs/CD.md](docs/CD.md)) |
| 인프라 | 운영 로그·DB 백업 | 🟡 | 500 오류 traceback·AI 모델 경고를 표준 출력으로(`docker compose logs web`), `scripts/backup_db.sh`(목차 확인·보관 개수), `scripts/restore_check.sh`(실행별 임시 DB에 복원 후 건수 비교, 자기가 만든 DB만 삭제), systemd 매일 백업+복원 점검 타이머. 테스트·PC 연습·CI 시나리오 통과, main 반영 (PR #46). 운영 서버에서 수동 백업·복원 점검 통과, systemd 타이머 설치·1회 실행 통과(매일 03:30 KST, 2026-11-20) (#44) |

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

PR·main push는 CI만 실행하고 배포하지 않습니다. 배포는 별도 워크플로입니다 ([docs/CD.md](docs/CD.md)).

| 워크플로 | 내용 |
|---|---|
| Release tag (`release-tag.yml`) | 버전 태그(`vX.Y.Z`) push 시 태그·main 포함 확인, 그 커밋의 main push CI 결과를 기다림(GitHub-hosted, Secret 없음) |
| Deploy (`deploy.yml`) | Release tag가 성공하면 한 실행에서 `0. plan → 1. test → 2. train → 3. parity → 4. image → 5. release → 6. deploy`(`CD_AUTO_DEPLOY=true`일 때만): 태그 커밋 테스트·모델 학습·학습/서비스 환경 예측 비교·이미지 시험과 GHCR 게시·GitHub Release 기록 → production 승인 후 서버가 기록된 digest로 실행(롤백 뒤 보류 중이면 건너뜀). 수동 실행(check·deploy·rollback)은 plan·deploy만 |

## 시연 자료

> 발표(2026-11-24) 전까지 채웁니다. 실제로 동작을 확인한 내용만 올립니다.

- **배포 주소:** https://lemit.eta.pyhub.kr (2026-11-20 첫 배포, 도메인·HTTPS 적용. IP 주소 `https://15.135.58.179`로는 인증서가 없어 접속되지 않습니다. 실제 개인정보·다른 곳에서 쓰는 비밀번호를 입력하지 않습니다)
- **데모 계정:** 강사 `demo@example.com`, 수강생 `student@example.com`('김하늘' 기록에 연결, #80) (`seed_demo`가 만듦. 비밀번호는 저장소에 두지 않고 실행할 때 지정)
- **데모 데이터:** `python manage.py seed_demo --password '<시연용 비밀번호>'` (#24). 로컬 실행·테스트 확인, **운영 서버에서는 아직 실행하지 않음**
  - 운영 서버: `docker compose exec web python manage.py seed_demo --password '<시연용 비밀번호>' --student-password '<수강생 비밀번호>'`
  - 수강생 계정 비밀번호는 `--student-password` > `LEMIT_DEMO_STUDENT_PASSWORD` > (처음이면) 무작위 생성 후 한 번 출력. 다시 실행해도 같은 계정이 새 '김하늘' 기록에 다시 연결됩니다
  - 수강생 4명(미성년자 1명·보호자), 레슨권 4개(잔여 보통·2회 이하 강조·소진), 회차 20개(지난주~다음 주, 완료·예정·휴강, 오늘 수업 2개, 보강 연결 1개·보강 필요 휴강 1개), 대화 6개(휴강·보강·일정 변경·일반 분류, 안 읽음·답장, 확신 낮음 1개)
  - 날짜는 실행한 날 기준이라 **시연 당일 아침에 다시 실행**하면 '오늘 수업'이 맞춰집니다. 다시 실행하면 데모 계정 데이터만 지우고 새로 만듭니다(다른 계정은 그대로, 비밀번호를 주지 않으면 기존 비밀번호 유지)
  - 비밀번호를 주지 않고 처음 실행하면 무작위 비밀번호를 한 번만 출력합니다(`--password`로 지정한 값은 출력하지 않음). 가짜 이름과 `010-0000-XXXX` 번호만 씁니다
  - 명령이 만든 계정(그룹 `lemit-demo`)만 다룹니다. 같은 이메일의 다른 계정이 이미 있으면 데이터·비밀번호를 바꾸지 않고 멈춥니다. 이 이메일은 회원가입에서 받지 않습니다

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
| 대시보드 (PC) | [docs/images/ui/after-dashboard-pc.png](docs/images/ui/after-dashboard-pc.png) |
| 메시지함 2단 (PC) | [docs/images/ui/after-inbox-detail-pc.png](docs/images/ui/after-inbox-detail-pc.png) |
| 대시보드·메시지함 (모바일) | [대시보드](docs/images/ui/after-dashboard-mobile.png) · [목록](docs/images/ui/after-inbox-mobile.png) · [상세](docs/images/ui/after-inbox-detail-mobile.png) |
| 수강생 목록 | [docs/images/ui/after-students-pc.png](docs/images/ui/after-students-pc.png) |
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
├── scripts/               # 헬스체크·스모크 테스트, 배포(deploy.sh)·롤백(rollback.sh)
├── deploy/                # Caddyfile (TLS 리버스 프록시), systemd 백업 타이머. deploy/state/는 서버 배포 기록(커밋 안 함)
├── .env.example           # 환경변수 예시 (.env는 커밋 안 함)
├── config/                # Django 프로젝트 설정 (settings, urls)
├── accounts/              # 강사 계정: 이메일 로그인 커스텀 User
├── inbox/                 # 메시지함 + 메시지 분류 추론 서비스 (classifier.py)
├── api/                   # 외부 API (DRF): API 키 모델·인증·공통 오류 형식, 키 관리 화면 (#102)
├── ml/                    # 메시지 분류 모델: 데이터(data/), 학습(train.py), 검증(verify.py), 모델 파일(artifacts/)
├── docs/
│   ├── PROJECT_PLAN.md    # 프로젝트 기획 기준 문서 (원문)
│   ├── USE_CASES.md       # 기능별 유스케이스 (GitHub 이슈와 1:1 연결)
│   ├── ERD.md             # 데이터 모델·ERD
│   ├── SCREENS.md         # 화면 목록·흐름·와이어프레임
│   ├── DEPLOY.md          # 배포 절차서 (Lightsail, 롤백, 장애 대응)
│   ├── CD.md              # GitHub Actions 자동 배포 설정 (Self-hosted Runner, 보안 조건)
│   └── ML.md              # 메시지 분류 모델 설계·학습 결과·한계
└── devlog/                # 날짜별 진행 기록 (발표 자료 작성용)
    ├── README.md          # 작성 규칙
    ├── _TEMPLATE.md       # 기록 템플릿
    └── YYYY-MM-DD.md      # 날짜별 기록
```

- 전체 기획 내용: [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md)
- 유스케이스: [docs/USE_CASES.md](docs/USE_CASES.md) → 작업 관리는 GitHub Issues (마일스톤 "발표 MVP (2026-11-24)")
- 진행 기록: [devlog/](devlog/)
