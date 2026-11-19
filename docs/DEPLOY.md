# LEMIT 배포 절차서 (AWS Lightsail)

> **상태 (2026-11-19):** 절차와 스크립트는 **PC에서 연습 배포로 검증**했습니다. 검증한 항목은 첫 배포, 두 번째 배포, 수동 롤백, 모델 손상 시 자동 롤백, Caddy 프록시를 거친 웹 동선, 공개 경로 확인 실패 시 미확정, 운영 `.env` 사전 점검(14개 시나리오)입니다. 프록시 시나리오와 사전 점검 시나리오는 CI에서도 매번 돌립니다. **실제 Lightsail 서버 배포는 아직 하지 않았습니다** (#7).
> 실제 서버 `lemit-server`(Amazon Linux 2023)는 생성되었고, 서버 준비(§1)부터 사용자가 직접 진행합니다. §1의 설치 명령은 Ubuntu에서 Amazon Linux 2023 기준으로 바꿨습니다(2026-11-19).
> 기준 문서: [OPERATIONS.md](OPERATIONS.md) (운영 원칙), [ML.md](ML.md) §6 (모델 배포·롤백)

## 0. 구성 한눈에 보기

```text
인터넷 ── 80/443 ──> proxy (Caddy, TLS 종료) ── web:8000 ──> web (Django + gunicorn, 모델 포함 이미지)
                                                              └──> db (PostgreSQL 18, 볼륨 pgdata, 포트 비공개)
SSH 22 (내 IP만) ── 관리자
```

- 인터넷에 여는 포트는 **22(관리자 IP만), 80, 443**뿐입니다. 8000(web)과 5432(DB)는 열지 않습니다.
- 앱 이미지 태그 = 커밋 SHA입니다. 모델 파일이 이미지 안에 있어서 **앱과 모델이 함께 배포되고 함께 롤백**됩니다.

## 1. 서버 준비 (한 번만, 사용자가 직접) — Amazon Linux 2023

> 실제 서버: Lightsail `lemit-server`, **Amazon Linux 2023**, Sydney(`ap-southeast-2`), 2GB RAM·2 vCPU·60GB SSD, SSH 사용자 `ec2-user` (2026-11-19 생성).
> 아래 설치 명령은 `amazonlinux:2023` 컨테이너에서 실행해 확인했습니다. dnf로 docker 25.0·git 설치를 확인했고, 플러그인 설치 스크립트는 정상 설치 외에 변조 파일·체크섬 항목 누락·알 수 없는 아키텍처일 때 아무것도 설치하지 않는 것까지 확인했습니다. **실제 서버에서는 아직 실행하지 않았습니다.**

1. **사양:** 2GB 메모리면 충분합니다.
   - PC 연습에서 잰 사용량은 web 약 250MB(gunicorn worker 2개 + 모델), DB 약 40MB, 프록시 약 13MB입니다.
   - 서버에서 이미지를 빌드(패키지 설치)할 때는 메모리가 더 들고, 기본 상태에는 스왑이 없으므로 아래 4번에서 **스왑 2GB**를 만듭니다.
2. **고정 IP(Static IP):** Lightsail 콘솔 → 네트워킹 → 고정 IP 생성 → `lemit-server`에 연결합니다.
   - 연결하지 않으면 인스턴스를 중지했다 시작할 때 공인 IP가 바뀝니다. 그러면 `.env`의 `DJANGO_ALLOWED_HOSTS`, 시연 주소, SSH 접속 주소가 모두 틀어집니다.
   - 고정 IP를 연결하면 공인 IP가 새 고정 IP로 바뀝니다. 이후에는 **고정 IP를 기준으로** `.env`와 SSH 주소를 씁니다.
3. **Lightsail 네트워킹(방화벽)**, 인스턴스의 IPv4 방화벽 기준입니다.
   - SSH 22: **"IP 주소로 제한"에서 내 IP만 허용.** Lightsail 기본값은 모든 IP 허용입니다.
   - HTTP 80: 허용
   - HTTPS 443: 도메인을 쓸 때 추가
   - **그 외 포트(특히 5432, 8000)는 추가하지 않습니다.** IPv6 방화벽을 켰다면 같은 규칙을 적용합니다.
4. **스왑 2GB** (서버에서 실행). 다시 실행해도 안전합니다. 이미 있으면 만들지 않고, fstab에도 중복으로 넣지 않습니다.
   ```bash
   if [ ! -f /swapfile ]; then
     sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress && sudo chmod 600 /swapfile && sudo mkswap /swapfile
   fi
   swapon --show | grep -q '^/swapfile ' || sudo swapon /swapfile
   grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # 재부팅 후에도 유지
   free -h                                                    # Swap 2.0Gi 확인
   ```
5. **Docker, git 설치와 코드 받기** (Amazon Linux 2023 저장소):
   ```bash
   sudo dnf install -y docker git
   sudo systemctl enable --now docker
   sudo usermod -aG docker ec2-user   # 로그아웃 후 다시 접속해야 sudo 없이 docker 사용
   git clone https://github.com/yeeun5364/lemit.git && cd lemit
   ```
6. **Docker Compose·Buildx 플러그인 설치.** Amazon Linux 2023 저장소에 없어서 GitHub 릴리스에서 받습니다.
   ```bash
   sudo bash scripts/install_docker_plugins.sh
   docker compose version && docker buildx version
   ```
   - 버전을 고정합니다: Compose v5.6.1(2026-10-21), Buildx v0.38.1(2026-10-20). 나온 지 2주가 지난 버전 중에서 골랐습니다.
   - **두 파일의 sha256 체크섬이 모두 맞을 때만 설치합니다.**
     - 스크립트는 `set -euo pipefail`로 실행되어, 어느 단계든 실패하면 즉시 멈춥니다.
     - 체크섬이 틀리거나 항목이 없으면 아무것도 설치하지 않고 종료 코드 1로 끝납니다.
     - 지원하지 않는 CPU 아키텍처(x86_64·aarch64 외)도 바로 중단합니다(PR #41 리뷰).
   - 저장소의 docker 버전(25.0)과 Compose v5 조합은 실제 서버에서 처음 실행합니다. `docker compose up`이나 `build`에서 API 버전 오류가 나면 그 출력을 그대로 알려주세요.
7. **서버 점검:**
   ```bash
   bash scripts/server_check.sh   # 읽기 전용. OS·메모리·스왑·디스크·도구·docker 그룹·열린 포트·.env 확인
   ```
   - `[필수]` 항목이 0건이 될 때까지 위 단계를 보완합니다.
   - 5432·8000이 loopback(127.x, ::1)이 아닌 주소에 열려 있으면 `[필수]`입니다.
   - 포트 목록을 얻지 못하면 정상으로 보지 않고 `[주의]`로 알립니다(`sudo dnf install -y iproute`).
   - `.env`는 다음 단계에서 만들므로, 이 시점에 `.env`가 없다는 `[주의]`는 정상입니다.

## 2. 운영 `.env` 만들기 (서버에서 사용자가 직접)

**비밀값은 서버에서 만들고 서버에만 둡니다.** 채팅, 저장소, 이미지에 넣지 않습니다.

```bash
cp .env.example .env
python3 -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50))"   # 출력값을 .env에 붙여넣기
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))"   # 출력값을 .env에 붙여넣기
vi .env                     # nano를 쓰려면: sudo dnf install -y nano
chmod 600 .env
bash scripts/server_check.sh   # 운영 .env 사전 점검까지 다시 확인
```

| 변수 | 도메인 + HTTPS | IP로 HTTP 시연 (도메인 없음) |
|---|---|---|
| `DJANGO_DEBUG` | `False` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `lemit.example.com,localhost` | `고정IP,localhost` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://lemit.example.com` | `http://고정IP` |
| `SITE_ADDRESS` | `lemit.example.com` (Caddy가 인증서 자동 발급) | `:80` |
| `DJANGO_BEHIND_PROXY` | `True` | `True` |
| `DJANGO_SECURE_HTTPS` | `True` (HTTPS 강제, 보안 쿠키) | `False` |
| `DJANGO_HSTS_SECONDS` | 처음엔 `0`, 안정되면 `3600`부터 | `0` |
| `GUNICORN_WORKERS` | 1GB면 `1`, 2GB면 `2` | 같음 |

- `DJANGO_ALLOWED_HOSTS`에 `localhost`를 함께 넣습니다. 컨테이너 헬스체크는 이 목록의 첫 값을 Host로 쓰므로 **첫 값은 실제 도메인이나 IP**로 둡니다.
- `DATABASE_URL`은 `compose.yaml`이 `POSTGRES_*` 값으로 자동으로 만듭니다(호스트 `db`). `.env`의 값은 쓰이지 않습니다.
- **IP로 HTTP 시연할 때 주의:** 로그인 비밀번호가 암호화되지 않고 전송됩니다. 데모 계정에는 **시연 전용 비밀번호**만 쓰고, 실제 개인정보를 입력하지 않습니다(OPERATIONS §10). 실제 사용자를 받기 전에는 도메인 + HTTPS가 필수입니다.
- 도메인을 쓴다면 DNS A 레코드를 고정 IP로 먼저 연결해야 Caddy가 인증서를 받을 수 있습니다.

## 3. 배포

```bash
scripts/deploy.sh            # 기본: origin/main의 최신 커밋
scripts/deploy.sh <커밋SHA>   # 특정 커밋
```

스크립트가 하는 일은 다음과 같습니다. 순서는 OPERATIONS §5를 따릅니다.

1. **운영 `.env` 사전 점검** (`scripts/preflight.sh`, PR #40 리뷰). 값끼리 맞지 않으면 중단합니다.
   - 공통: `DJANGO_DEBUG=False`, 예시 값(`change-me`) 없음, 비밀키 50자 이상, DB 비밀번호는 영문·숫자·`-_`만
   - 도메인 모드(`SITE_ADDRESS`가 도메인): `DJANGO_BEHIND_PROXY=True`, `DJANGO_SECURE_HTTPS=True`, `DJANGO_ALLOWED_HOSTS` **첫 값 = 도메인**, `DJANGO_CSRF_TRUSTED_ORIGINS`에 `https://도메인`
   - IP 모드(`SITE_ADDRESS=:80`): `DJANGO_SECURE_HTTPS=False`(HTTPS가 없어서 켜면 리다이렉트가 반복됨), `DJANGO_ALLOWED_HOSTS` 첫 값은 서버 IP(`localhost` 아님)
2. **코드 받기:** `git fetch`와 `checkout`을 합니다. 서버 작업 폴더에 수정한 내용이 있으면 중단합니다.
3. **이미지 빌드:** 태그는 커밋 SHA 12자리입니다.
4. **`manage.py check --deploy`:** 운영 `.env`로 새 이미지에서 실행합니다. **모드별로 의도적으로 받아들이는 경고만 허용**하고, 그 밖의 경고나 오류가 나오면 중단합니다.
   - IP 모드: `W004`(HSTS), `W008`(SSL 리다이렉트), `W012`(세션 쿠키 secure), `W016`(CSRF 쿠키 secure). HTTP 시연이므로 HTTPS 관련 경고는 피할 수 없습니다.
   - 도메인 모드: `W004`(HSTS)만. 인증서가 안정된 뒤 `DJANGO_HSTS_SECONDS`로 켭니다.
5. **DB 기동과 마이그레이션:** 새 이미지로 한 번 실행합니다.
6. **교체:** web과 proxy를 교체하고, healthy가 될 때까지 기다립니다. proxy는 Caddy 관리 API로 동작 여부를 확인합니다.
7. **스모크 테스트:** `/healthz/` 정상, **모델 상태 `ok`**, `check`, `migrate --check`, **`ml.verify`**, **추론 1건**을 확인합니다.
8. **프록시를 거친 공개 경로 확인** (`scripts/check_public.sh`, PR #40 리뷰). 응답의 `status`와 `message_classifier.status`가 모두 `ok`여야 합니다.
   - IP 모드: Caddy(80)를 거쳐 `/healthz/`를 호출합니다.
   - 도메인 모드: `curl --resolve`로 `https://도메인/healthz/`를 호출해 **인증서까지 검증**합니다.
   - 인증서를 처음 발급받는 시간을 고려해 기본 10초 × 12회 재시도합니다.
9. **결과 처리:** 1~8을 모두 통과해야 `deploy/state/current_tag`를 바꾸고 `history.log`에 `OK`를 남깁니다.

**실패했을 때 처리와 복구 범위**

| 실패 지점 | 기록 | 처리 |
|---|---|---|
| 사전 점검, `check --deploy` | 사전 점검은 화면에만, `check --deploy`는 `FAILED_CHECK_DEPLOY` | 아무것도 바꾸지 않고 중단. `.env`를 고친 뒤 다시 실행 |
| web이 healthy가 안 됨, 스모크 실패 | `FAILED_ROLLED_BACK_TO_<직전 태그>` | **직전 성공 이미지로 web 자동 롤백**(앱+모델). 롤백 후에도 스모크로 확인 |
| 공개 경로 확인만 실패 | `FAILED_PUBLIC_CHECK` | web은 스모크를 통과했으므로 되돌리지 않음. **`current_tag`는 바꾸지 않음.** 프록시 로그·DNS·인증서·방화벽을 고친 뒤 다시 실행 |

- 이 스크립트는 **DB 스키마와 프록시 설정 자체는 되돌리지 않습니다.**
- 외부 인터넷에서 보이는 DNS와 방화벽은 서버 안에서 확인할 수 없으므로, 배포 후 PC 브라우저로 직접 확인합니다(§4).

**첫 배포 후 한 번만** 관리자 계정을 만듭니다.
```bash
docker compose exec web python manage.py createsuperuser
```

## 4. 배포 후 확인 (사람이 직접)

1. 서버 상태를 확인합니다.
   ```bash
   curl -s http://localhost:8000/healthz/ -H "Host: <ALLOWED_HOSTS 첫 값>"
   ```
   **HTTP 200만 보지 말고** `"status": "ok"`와 `"message_classifier": {"status": "ok", "version": "v2"}`까지 확인합니다.
2. 브라우저에서 `http(s)://도메인 또는 IP`로 접속해 다음 순서로 확인합니다.
   1. 회원가입·로그인
   2. 수강생 등록
   3. **메시지함 → [+ 수강생 메시지]** → "수요일에 못 가서 금요일에 보강 가능할까요?" 입력
   4. **분류 배지**(예: "보강 요청 · 63%")와 메시지함 '보강' 탭 확인
3. 외부에서 막혀 있어야 하는 포트를 확인합니다. **Windows PC의 PowerShell**에서 실행하며, 5432·8000은 `TcpTestSucceeded : False`, 80은 `True`여야 합니다.
   ```powershell
   Test-NetConnection 고정IP -Port 5432
   Test-NetConnection 고정IP -Port 8000
   Test-NetConnection 고정IP -Port 80
   ```
4. 컨테이너 상태와 메모리를 확인합니다.
   ```bash
   docker compose --profile proxy ps
   docker stats --no-stream
   ```
   메모리가 부족하면 `GUNICORN_WORKERS`를 줄이고 다시 배포합니다.
5. README "시연 자료"에 배포 주소를 적습니다.

## 5. 롤백

```bash
docker image ls lemit-web          # 서버에 남아 있는 이미지 태그
cat deploy/state/history.log       # 배포 기록 (시각, 태그, 결과)
scripts/rollback.sh <이미지 태그>  # 해당 태그로 web 교체 + 스모크 테스트
```

- 앱과 모델이 이미지 단위로 함께 돌아갑니다. 모델만 따로 바꾸거나 되돌리지 않습니다.
- **DB 스키마는 되돌리지 않습니다.** 되돌릴 이미지 이후에 마이그레이션이 있었다면 그 이미지가 현재 스키마와 맞는지 먼저 확인합니다. 파괴적 마이그레이션은 배포 전에 따로 계획합니다(OPERATIONS §5).
- 자동 롤백에는 한계가 있습니다. 실패한 새 이미지가 스모크 테스트 전 약 20~30초 동안 요청을 받을 수 있습니다. 시연 직전에는 배포하지 않습니다.

## 6. 하면 안 되는 것

- `docker compose down -v`: **DB 볼륨(모든 데이터)이 지워집니다.** CI의 `down -v`는 임시 환경 정리용입니다.
- `-f compose.dev.yaml` 지정: DEBUG, runserver, 소스 마운트, DB 포트 공개가 켜집니다.
- 서버에서 코드를 직접 고치고 배포: 배포 스크립트가 중단합니다. 수정은 PR을 거쳐 main에 넣은 뒤 배포합니다.
- `.env`를 커밋하거나 채팅에 붙여넣기, 볼륨 `caddy_data` 삭제(인증서 재발급 제한).

## 7. 장애 대응 (간단)

| 증상 | 확인 | 조치 |
|---|---|---|
| 사이트 접속 안 됨 | `docker compose --profile proxy ps`, `docker compose logs --tail 100 proxy web` | 프록시가 멈췄으면 `docker compose --profile proxy up -d --wait` |
| web unhealthy | `docker compose logs --tail 100 web`, `/healthz/`의 `db` 값 | DB 문제면 db 로그 확인. 새 배포 직후라면 `scripts/rollback.sh <직전 태그>` |
| 분류 배지가 "분류 불가" | `/healthz/`의 `message_classifier` | 이미지에 모델 파일이 없거나 손상됨 → 직전 이미지로 롤백 (서비스의 다른 기능은 정상) |
| 디스크 부족 | `df -h`, `docker system df` | 사용하지 않는 **이미지**만 정리합니다. 최근 두 개 태그는 롤백용으로 남깁니다. 볼륨은 지우지 않습니다 |

## 8. PC 연습 배포 기록 (2026-11-19)

PC 연습 조건은 다음과 같습니다.
- 임시 작업 폴더, 가짜 비밀값, Compose 프로젝트 `lemit-rehearsal`
- 포트: 프록시 8080, web 8100
- `DEPLOY_SKIP_GIT=1`: git을 건드리지 않고 현재 커밋으로 배포하는 연습용 설정

| 단계 | 결과 |
|---|---|
| 첫 배포 `dc4d008fd9d0` | 빌드 → 마이그레이션 → db·web·proxy healthy → 스모크 6항목 통과 |
| 프록시를 거친 웹 동선 | 회원가입 → 수강생 → 수강생 메시지 → **"보강 요청 · 63%"** 배지, 보안 헤더(`X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`) 적용, `Server` 헤더 숨김 |
| 포트 | db `5432/tcp`(호스트 미공개), web `127.0.0.1:8100`만, proxy 8080 |
| 메모리 | web 247MiB, db 41MiB, proxy 13MiB |
| 두 번째 배포 `16a15ac2c77e` | 통과, 기록 `OK` |
| 수동 롤백 → `dc4d008fd9d0` | 실행 이미지가 이전 태그로 바뀜, 스모크 통과, 기록 `MANUAL_ROLLBACK_OK` |
| 모델이 손상된 배포 `11b1e4feef2f` | 스모크가 모델 상태·`ml.verify`·추론 3항목을 실패로 잡음 → **직전 이미지로 자동 롤백**, 롤백 후 스모크 통과, 기록 `FAILED_ROLLED_BACK_TO_dc4d008fd9d0`, 종료 코드 1 |
| (PR #40 리뷰 반영 후) 정상 배포 `e4da19a00158` | 사전 점검 통과(IP 모드) → `check --deploy` 통과(허용 경고 W004·W008·W012·W016) → 스모크 → **Caddy를 거친 공개 경로 통과** → `OK`. proxy도 healthy |
| 공개 경로 확인 실패 `703819beaaa0` | 확인 포트를 일부러 틀리게 지정 → 2회 실패 → 종료 코드 1, 기록 `FAILED_PUBLIC_CHECK`, **`current_tag`는 `e4da19a00158` 그대로** |
| 프록시 중단 | `check_public.sh`가 실패. 프록시를 다시 켜면 통과 |
| 운영 `.env` 사전 점검 | `scripts/test_preflight.sh` 14개 시나리오(정상 2, 오류 12) 모두 기대대로 판정 |
