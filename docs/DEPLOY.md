# LEMIT 배포 절차서 (AWS Lightsail)

> **상태 (2026-11-19):** 절차와 스크립트는 **PC에서 연습 배포로 검증**했습니다. 검증한 항목은 첫 배포, 두 번째 배포, 수동 롤백, 모델 손상 시 자동 롤백, Caddy 프록시를 거친 웹 동선입니다. **실제 Lightsail 서버 배포는 아직 하지 않았습니다** (#7).
> 기준 문서: [OPERATIONS.md](OPERATIONS.md) (운영 원칙), [ML.md](ML.md) §6 (모델 배포·롤백)

## 0. 구성 한눈에 보기

```text
인터넷 ── 80/443 ──> proxy (Caddy, TLS 종료) ── web:8000 ──> web (Django + gunicorn, 모델 포함 이미지)
                                                              └──> db (PostgreSQL 18, 볼륨 pgdata, 포트 비공개)
SSH 22 (내 IP만) ── 관리자
```

- 인터넷에 여는 포트는 **22(관리자 IP만), 80, 443**뿐입니다. 8000(web)과 5432(DB)는 열지 않습니다.
- 앱 이미지 태그 = 커밋 SHA입니다. 모델 파일이 이미지 안에 있어서 **앱과 모델이 함께 배포되고 함께 롤백**됩니다.

## 1. 서버 준비 (한 번만, 사용자가 직접)

1. **Lightsail 인스턴스:** Linux/Unix → **Ubuntu 24.04 LTS**.
   - 권장 사양은 **2GB 메모리** 이상입니다(최소 1GB).
   - PC 연습에서 잰 사용량은 web 약 250MB(gunicorn worker 2개 + 모델), DB 약 40MB, 프록시 약 13MB였습니다.
   - 2GB를 권장하는 이유는 서버에서 이미지를 빌드(패키지 설치)할 때 메모리가 더 들기 때문입니다.
2. **고정 IP(Static IP)** 를 만들어 인스턴스에 연결합니다.
3. **Lightsail 네트워킹(방화벽):**
   - SSH 22: "내 IP로 제한"
   - HTTP 80, HTTPS 443: 허용
   - **그 외 포트(특히 5432, 8000)는 추가하지 않습니다.**
4. SSH 키로 접속합니다. Lightsail 콘솔에서 받은 키 또는 내 공개키를 씁니다. 비밀번호 로그인은 쓰지 않습니다.
5. Docker를 설치합니다. 공식 문서의 Ubuntu 저장소 방식을 따릅니다.
   ```bash
   sudo apt-get update && sudo apt-get install -y ca-certificates curl git
   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
   sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo usermod -aG docker $USER   # 다시 접속하면 sudo 없이 docker 사용
   ```
6. 코드를 받습니다.
   ```bash
   git clone https://github.com/yeeun5364/lemit.git && cd lemit
   ```

## 2. 운영 `.env` 만들기 (서버에서 사용자가 직접)

**비밀값은 서버에서 만들고 서버에만 둡니다.** 채팅, 저장소, 이미지에 넣지 않습니다.

```bash
cp .env.example .env
python3 -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50))"   # 출력값을 .env에 붙여넣기
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))"   # 출력값을 .env에 붙여넣기
nano .env
chmod 600 .env
```

| 변수 | 도메인 + HTTPS | IP로 HTTP 시연 (도메인 없음) |
|---|---|---|
| `DJANGO_DEBUG` | `False` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `lemit.example.com,localhost` | `서버IP,localhost` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://lemit.example.com` | `http://서버IP` |
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

1. **사전 점검:** `.env`가 있는지, `DJANGO_DEBUG=True`나 예시 값(`change-me`)이 남아 있지 않은지 확인합니다. 하나라도 걸리면 중단합니다.
2. **코드 받기:** `git fetch`와 `checkout`을 합니다. 서버 작업 폴더에 수정한 내용이 있으면 중단합니다.
3. **이미지 빌드:** 태그는 커밋 SHA 12자리입니다.
4. **DB 기동과 마이그레이션:** 새 이미지로 한 번 실행합니다.
5. **교체:** web과 proxy를 교체하고, healthy가 될 때까지 기다립니다.
6. **스모크 테스트:** `/healthz/` 정상, **모델 상태 `ok`**, `check`, `migrate --check`, **`ml.verify`**, **추론 1건**을 확인합니다.
7. **결과 처리:** 성공하면 `deploy/state/current_tag`와 `history.log`에 기록합니다. 실패하면 **직전 성공 이미지로 자동 롤백**한 뒤 롤백 결과도 스모크 테스트로 확인합니다.

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
3. 외부에서 막혀 있어야 하는 포트를 확인합니다(PC에서 실행). 둘 다 연결되지 않아야 합니다.
   ```bash
   nc -vz 서버IP 5432
   nc -vz 서버IP 8000
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
