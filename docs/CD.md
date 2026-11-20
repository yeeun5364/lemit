# GitHub Actions 자동 배포 (CD) 설정 안내 — Self-hosted Runner

> **상태 (2026-11-20):** CD를 **Self-hosted Runner**(개인 PC의 Ubuntu WSL2) 방식으로 바꿨습니다(#56). 워크플로 규칙 검사, 서버 명령 입력·main 포함 검증, 배포 잠금은 **자동 테스트로 검증**했습니다. **러너 PC 격리, GitHub 설정, 실제 배포 시험은 아직 안 했습니다.** §2~§4는 사용자가 직접 하는 단계입니다.
> 전제: 서버 준비와 첫 수동 배포를 먼저 끝냅니다([DEPLOY.md](DEPLOY.md) §1~§4). 서버에 `/home/ec2-user/lemit` 폴더와 운영 `.env`가 있어야 합니다.
> 이전 방식(GitHub-hosted 러너 + OIDC + 배포 중 SSH 임시 개방, #42)은 설정하기 전에 바뀌어 실제로 쓰인 적이 없습니다. §8에 기록만 남깁니다.

## 0. 전체 흐름

```text
main에 병합 → CI(린트·테스트·Docker, GitHub-hosted 러너)가 모두 성공
   └→ Deploy 워크플로 (자동 배포를 켰을 때. 처음엔 수동 버튼으로만)
        ① plan   (GitHub-hosted): 요청 검증, 이 커밋이 main에 있고 CI 3개가 성공했는지 다시 확인
        ② deploy (내 PC의 WSL 러너, 라벨 lemit-deploy):
             - 저장소 코드를 받지 않음. 설정 확인 → (자동 배포면) 아직 main 최신인지 확인
             - 서버 신원 확인(호스트 키) 후 배포 전용 키로 SSH 요청 한 줄: deploy <커밋>
        ③ 서버: 이 키로는 scripts/ci_deploy.sh만 실행
             → 커밋이 origin/main에 포함됐는지 다시 확인 → deploy.sh <커밋> (rollback은 rollback.sh)
```

**왜 이렇게 나누나요?**
- **판단은 GitHub 컴퓨터에서, 내 PC에서는 최소한만.** 공개 저장소의 코드나 외부 액션이 내 PC에서 실행되지 않도록, 러너 작업은 `run` 단계 몇 개(설정 확인, SSH)만 있습니다.
- **SSH 방화벽을 열고 닫지 않습니다.** 러너 PC의 공인 IP가 이미 Lightsail 22번에 허용된 관리자 IP이므로, AWS 권한(OIDC 역할·키)이 필요 없습니다.
- **배포 전용 열쇠는 배포 명령만 실행합니다.** 유출되더라도 서버에서 셸을 열거나 `.env`·DB에 접근할 수 없습니다(`deploy <main의 커밋>`, `rollback <태그>`, `status`만).
- **서버 신원 확인:** 미리 저장한 서버 호스트 키와 다르면 접속하지 않습니다(`StrictHostKeyChecking=yes`).

**필요한 것 체크리스트**

| 어디에 | 무엇을 | 단계 |
|---|---|---|
| GitHub | 포크 PR 실행 승인: **모든 외부 기여자** | §1-2 (가장 먼저) |
| 러너 PC (WSL) | 전용 사용자·배포판, Windows 접근 차단, 서비스 실행, 라벨 `lemit-deploy`, 공인 IP 확인 | §2 |
| PC + 서버 | 서버 호스트 키 확인, 배포 전용 SSH 키를 강제 명령으로 등록 | §3 |
| GitHub | Environment `production`(main만) + Variables 3개 + Secret 1개 | §4 |
| GitHub | 첫 시험: check → deploy → rollback, 그다음 자동 배포 켜기 | §5 |

## 1. 보안 판단: 공개 저장소에서 Self-hosted Runner

GitHub는 공개 저장소에 Self-hosted Runner를 **권장하지 않습니다.** 누구나 포크해서 PR을 보낼 수 있고, PR은 **자기가 고친 워크플로 파일**로 실행되기 때문입니다. PR의 `ci.yml`에 `runs-on: self-hosted`를 적으면 내 PC에서 그 코드가 돕니다. 개인 계정 저장소에서는 "이 러너는 이 워크플로만" 같은 제한(러너 그룹)을 쓸 수 없습니다(조직 기능).

### 1-1. 위험과 대응

| 위험 | 대응 | 누가 |
|---|---|---|
| 포크 PR이 내 PC에서 코드 실행 | 외부 기여자의 모든 PR은 승인해야 워크플로가 실행됨(§1-2). 승인 전에 `.github/` 변경을 확인 | 사용자 설정 |
| 저장소 안에서 실수로 PR·push 작업이 러너를 씀 | `scripts/check_workflow_policy.py`가 CI에서 검사: self-hosted는 `deploy.yml`만, 트리거는 `workflow_run`(CI, main)·`workflow_dispatch`만, 러너 작업은 `environment: production`·`needs`·저장소 조건·액션 미사용 | 코드 |
| 러너 작업이 Windows 파일·프로그램에 접근 | WSL 기본 설정은 `/mnt/c`로 Windows 파일을 읽고 `powershell.exe`를 실행할 수 있음 → 러너 전용 배포판에서 automount·interop 끄기, sudo 없는 전용 사용자(§2) | 사용자 설정 |
| 배포 키 유출 | production Environment Secret(배포 브랜치 main만) → PR 작업은 받을 수 없음. 키를 `~/.ssh`가 아닌 작업 임시 폴더에 두고 끝나면 삭제. 강제 명령으로 기능 제한, 서버에서 main 포함 여부 재확인 | 코드 + 사용자 설정 |
| 이전 작업이 러너에 남긴 파일 | 러너 작업은 저장소를 받지 않고, 키 폴더를 매번 새로 만들고 지움. ssh는 `-F /dev/null`로 러너의 SSH 설정 파일을 읽지 않음 | 코드 |
| 운영 `.env`·DB | 러너는 셸·DB 접근이 없음(강제 명령). `deploy.sh`·`rollback.sh`는 `.env`를 읽기만 하고 `down -v`를 쓰지 않음 | 기존 코드 |
| 테스트 안 된 코드 배포 | plan 작업(GitHub-hosted)이 main 포함 + CI 3개 성공을 확인, 서버도 main 포함을 다시 확인 | 코드 |

### 1-2. 포크 PR 승인 설정 (직접, 가장 먼저)

저장소 → **Settings → Actions → General → Approval for running fork pull request workflows from contributors**
- **"Require approval for all external contributors"** 를 고릅니다.
- 지금 값(`first_time_contributors`)은 처음 기여하는 사람만 승인을 요구합니다. PR이 한 번 병합된 사람은 승인 없이 워크플로를 실행할 수 있어, Self-hosted 러너에는 부족합니다.
- 외부 PR의 "Approve and run" 버튼을 누르기 전에 **`.github/workflows/` 변경이 있는지** 꼭 확인합니다.

**판단:** §1-2와 §2의 러너 격리를 모두 적용하면, 혼자 운영하는 수업 프로젝트 수준에서는 위험을 통제할 수 있다고 봅니다. 둘 중 하나라도 빠지면 권하지 않습니다. 남는 위험은 §7에 적었습니다.

## 2. 러너 PC 준비 (WSL, 직접)

1. **러너 전용 WSL 배포판을 권장합니다.** 아래 3번 설정은 배포판 전체에 적용되어 같은 배포판의 개발 작업에도 영향을 주기 때문입니다.
2. **sudo 권한이 없는 전용 사용자로 러너를 실행합니다.**
   ```bash
   sudo adduser --disabled-password --gecos "" ghrunner
   id ghrunner          # groups에 sudo가 없어야 함
   ```
   러너를 이미 다른 사용자로 설치했다면, `ghrunner`로 다시 설치·등록합니다(`./config.sh remove` 후 `ghrunner`로 `./config.sh`).
3. **Windows 파일·프로그램 접근을 끕니다.** `/etc/wsl.conf`:
   ```ini
   [boot]
   systemd=true

   [automount]
   enabled=false

   [interop]
   enabled=false
   appendWindowsPath=false
   ```
   PowerShell에서 `wsl --terminate <배포판 이름>` 후 다시 열고 확인합니다: `ls /mnt/c`(없어야 함), `powershell.exe`(실행 안 되어야 함).
4. **라벨 `lemit-deploy`를 붙입니다.** 저장소 → Settings → Actions → **Runners** → `lemit-ubuntu-runner` → 라벨 추가. 또는 등록할 때 `./config.sh ... --labels lemit-deploy`.
   - 배포 작업은 `self-hosted, Linux, X64, lemit-deploy` 라벨을 모두 가진 러너에서만 실행됩니다.
5. **서비스로 실행합니다**(PC를 켜면 자동 시작): 러너 폴더에서 `sudo ./svc.sh install ghrunner && sudo ./svc.sh start`, 상태는 `sudo ./svc.sh status`.
6. **도구를 확인합니다:** `ssh`, `ssh-keygen`, `git`(배포 작업이 처음에 확인합니다).
7. **공인 IP를 확인합니다.** WSL에서 `curl -fsS https://checkip.amazonaws.com` 결과가 Lightsail SSH 22번에 허용된 관리자 IP와 같아야 합니다(관리자 IP는 이 공개 문서가 아니라 개인 메모에).
   - 다르거나 집 인터넷 IP가 자주 바뀌면 배포가 "SSH 연결 실패"로 끝납니다. 이때 22번을 넓게 여는 대신 Lightsail 콘솔에서 새 IP로 바꿉니다.

## 3. 서버 호스트 키·배포 전용 SSH 키 (PC + 서버, 직접)

### 3-1. 호스트 키 (가짜 서버 차단)
1. **서버(SSH 접속한 창)에서** 지문을 봅니다: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
2. **PC에서** 호스트 키 한 줄을 받아 지문을 비교합니다.
   ```powershell
   ssh-keyscan -t ed25519 15.135.58.179 2>$null | Tee-Object -Variable hk
   $hk | ssh-keygen -lf -
   ```
3. 두 지문이 **같을 때만**, `ssh-keyscan`이 출력한 한 줄을 §4의 `LIGHTSAIL_KNOWN_HOSTS`에 넣습니다(공개 정보라 Variable).

### 3-2. 배포 전용 SSH 키
관리자 키(`lemit-key.pem`)는 서버 전체를 다룹니다. 러너에는 **배포만 할 수 있는 새 키**를 줍니다.
1. **PC에서 키를 만듭니다.** 자동으로 써야 해서 암호는 비웁니다(엔터 두 번).
   ```powershell
   ssh-keygen -t ed25519 -f $HOME\.ssh\lemit-github-deploy -C lemit-github-deploy
   ```
2. **서버에 강제 명령과 함께 등록합니다.** `ssh-ed25519 AAAA...` 부분을 공개 키(`.pub` 내용)로 바꿉니다.
   ```bash
   echo 'command="/home/ec2-user/lemit/scripts/ci_deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA...여기에공개키... lemit-github-deploy' >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```
3. **PC에서 시험합니다.** 첫 줄은 상태가 나오고, 둘째 줄은 거부되어야 합니다.
   ```powershell
   ssh -i $HOME\.ssh\lemit-github-deploy ec2-user@15.135.58.179 status
   ssh -i $HOME\.ssh\lemit-github-deploy ec2-user@15.135.58.179 ls       # "허용하지 않는 요청" → 정상
   ```
4. **개인 키 내용**을 §4의 Secret `LIGHTSAIL_SSH_KEY`에 붙여넣습니다(`Get-Content $HOME\.ssh\lemit-github-deploy -Raw | Set-Clipboard`).
   - 채팅·PR·이슈·파일에 붙여넣지 않습니다. **러너 PC에 파일로 두지도 않습니다**(러너의 모든 작업이 읽을 수 있음). 등록 후 PC의 개인 키 파일은 지워도 됩니다.

## 4. GitHub 설정 (웹, 직접)

### 4-1. Environment `production`
저장소 → **Settings → Environments → New environment** → 이름 `production`
- **Deployment branches and tags:** "Selected branches and tags" → **`main`만**. 그래야 다른 브랜치·PR의 작업이 배포 키를 받을 수 없습니다.
- **Required reviewers (선택, 권장):** 본인 계정. 배포마다 "승인"을 눌러야 러너 작업이 시작됩니다. PC를 켰을 때 예상하지 못한 배포가 바로 실행되는 것도 막습니다.

### 4-2. Environment 안에 Variables·Secret

| 종류 | 이름 | 값 |
|---|---|---|
| Variable | `LIGHTSAIL_HOST` | `15.135.58.179` (고정 IP. 도메인을 쓰려면 §3-1의 known_hosts 줄도 같은 이름으로) |
| Variable | `LIGHTSAIL_USER` | `ec2-user` |
| Variable | `LIGHTSAIL_KNOWN_HOSTS` | §3-1에서 확인한 한 줄 |
| **Secret** | `LIGHTSAIL_SSH_KEY` | §3-2의 배포 전용 **개인 키** 전체 |

- 앱 비밀값(Django 비밀키, DB 비밀번호)은 **GitHub에 넣지 않습니다.** 서버의 `.env`에만 있습니다.
- 이전 방식의 `AWS_REGION`, `AWS_ROLE_ARN`, `LIGHTSAIL_INSTANCE_NAME`은 필요 없습니다.

### 4-3. 자동 배포 스위치 (나중에)
저장소 → Settings → Secrets and variables → Actions → **Variables** → `CD_AUTO_DEPLOY` = `true`
- **§5의 수동 시험이 모두 성공한 뒤에** 만듭니다. 없으면 수동 버튼만 동작합니다.

## 5. 첫 시험 (직접, 순서대로)

1. **check** (배포 안 함): Actions → **Deploy** → **Run workflow**(브랜치 main) → action `check`
   - plan 작업 성공 → deploy 작업이 **내 러너**에서 시작(로그 상단 Runner name) → 서버 `status` 출력
2. **deploy:** action `deploy`, sha는 비워 둠(main 최신)
   - plan "CI 3개 작업 모두 성공" → 서버 `deploy.sh` 출력 → `배포 완료` → 서비스 주소에서 동작 확인
3. **같은 버전 다시 deploy:** 안전하게 `OK`로 끝나는지 확인합니다.
4. **rollback:** `check` 결과의 이미지 목록에서 이전 태그를 골라 action `rollback`, rollback_tag에 입력
5. **러너가 꺼져 있을 때:** 러너 서비스를 멈추고(`sudo ./svc.sh stop`) `check`를 실행 → deploy 작업이 "Waiting for a runner"로 대기하는지 확인 → 서비스를 켜면 이어서 실행되는지 확인
6. 모두 성공하면 §4-3 `CD_AUTO_DEPLOY=true`

| 증상 | 원인 후보 |
|---|---|
| "production Environment 설정이 빠졌습니다" | §4-2 이름 오타, Environment가 아닌 곳에 등록, main이 아닌 브랜치에서 실행 |
| deploy 작업이 계속 "Waiting for a runner" | 러너 꺼짐·오프라인, 라벨 `lemit-deploy` 누락(§2-4) |
| "SSH 연결 실패" | 러너 PC 공인 IP가 22번 허용 IP와 다름(§2-7), 배포 키 등록(§3-2), Secret 내용 |
| `Host key verification failed` | 서버 호스트 키가 바뀌었거나 `LIGHTSAIL_KNOWN_HOSTS`가 틀림 → **바로 맞추지 말고 원인부터 확인** |
| `[ci_deploy] 허용하지 않는 요청입니다: main에 포함되지 않은 커밋입니다` | main 밖의 커밋을 요청함. 서버 `git fetch`가 안 됐으면 "git fetch 실패" |
| "다른 배포·롤백이 진행 중" | 서버에서 수동 배포가 돌고 있음 (서버 잠금) |

## 6. 러너 PC가 꺼져 있을 때

| 상황 | 동작 |
|---|---|
| PC가 꺼진 동안 main에 병합 | plan은 GitHub 컴퓨터에서 끝나고, deploy 작업은 러너를 기다립니다. **최대 24시간** 기다린 뒤 실패합니다(GitHub 기본). 실패 알림 메일이 옵니다. |
| 그사이 병합이 여러 번 | 동시 실행 그룹(`deploy-production`)에서 **기다리는 배포는 가장 최근 것 하나만** 남고 이전 대기분은 취소됩니다. |
| PC를 켰는데 대기 중인 자동 배포가 오래된 커밋 | 실행 직전에 main 최신 커밋과 비교해, 더 새 커밋이 있으면 **건너뜁니다**(성공, 요약에 '건너뜀'). 새 커밋의 CI가 성공하면 그 배포가 실행됩니다. 새 커밋의 CI가 실패했다면 아무것도 배포되지 않으니, 필요하면 수동 deploy로 원하는 커밋을 배포합니다. |
| 24시간이 지나 실패 | 서버는 바뀌지 않았습니다. PC를 켠 뒤 수동 `deploy`(sha 비움)를 실행합니다. |
| 배포 도중 PC가 꺼지거나 네트워크가 끊김 | SSH가 끊기면 서버의 `deploy.sh`가 중간에 멈출 수 있습니다. 이 경우의 동작은 **아직 시험하지 않았습니다.** 관리자 SSH나 수동 `check`로 `status`(현재 태그·컨테이너·공개 경로)를 확인하고, 이상하면 `rollback` 또는 서버에서 `deploy.sh`를 다시 실행합니다(DEPLOY.md). |
| 예상하지 못한 시점의 배포가 싫다면 | §4-1 Required reviewers를 켜서 승인 후에만 실행되게 합니다. |

## 7. 안전장치 요약과 남는 위험

| 위험 | 장치 |
|---|---|
| 테스트 안 된 코드 배포 | main 포함 + CI 3개 성공(plan 작업), 서버에서 main 포함 재확인(`ci_deploy.sh`) |
| PR 코드가 내 PC에서 실행 | PR·push 트리거 없음, 저장소·브랜치 조건, CI 규칙 검사, 외부 기여자 승인(§1-2) |
| 러너 작업의 PC 접근 | 전용 사용자·배포판, automount·interop 끔(§2) |
| 배포 키 유출 | Environment Secret(main만), 작업 임시 폴더에만 두고 삭제, 강제 명령(셸·포트 포워딩 불가) |
| 가짜 서버 접속 | 저장한 호스트 키로만 접속, 러너의 SSH 설정 파일 무시(`-F /dev/null`) |
| 동시 배포 | 워크플로 동시 실행 1개 + 서버 `flock` 잠금 |
| 공개 로그 노출 | 키·`.env`·관리자 IP를 출력하지 않음 |

**남는 위험**
- **GitHub 계정이 탈취되면** 워크플로를 바꿔 러너에서 코드를 실행할 수 있습니다. 계정 2단계 인증을 켜 둡니다. 피해 범위는 §2의 격리가 줄입니다.
- **외부 PR 승인을 잘못 누르면** 그 PR의 워크플로가 러너에서 돌 수 있습니다. 승인 전에 `.github/` 변경을 확인합니다.
- 러너는 작업마다 새로 만들어지지 않습니다(ephemeral 아님). 악성 작업이 한 번 실행되면 이후 작업에 영향을 줄 수 있으므로, 의심되면 러너 배포판을 지우고 다시 만듭니다.
- 관리자 IP(집 인터넷)가 바뀌면 배포가 실패합니다(안전한 쪽 실패).
- 배포 중 약 20~30초 동안 실패한 새 버전이 응답할 수 있습니다(DEPLOY.md §3, 자동 롤백 전).

## 8. 이전 방식 기록 (#42, 사용하지 않음)

GitHub-hosted 러너가 OIDC로 AWS 역할을 받아, 배포하는 동안만 SSH 22번에 러너 IP를 추가하고 끝나면 빼는 방식이었습니다. production Environment와 AWS 설정을 하기 전에 Self-hosted 방식으로 바뀌어 실제 배포에 쓰이지 않았습니다.
- AWS에 OIDC 공급자, 역할 `lemit-github-deploy`, 서버 태그 `lemit-deploy=allowed`를 만들었다면 이제 필요 없으므로 지워도 됩니다.
- `scripts/lightsail_firewall.py`(방화벽 규칙에서 IP 하나만 추가·제거, 테스트 포함)는 남겨 둡니다. 관리자 PC에 AWS CLI가 있으면 22번 규칙의 IP를 바꿀 때 쓸 수 있습니다: `python3 scripts/lightsail_firewall.py close --instance lemit-server --cidr <IP>/32`
