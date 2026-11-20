# GitHub Actions 자동 배포 (CD) 설정 안내 — Self-hosted Runner

> **상태 (2026-11-20):** CD를 **Self-hosted Runner**(개인 PC의 러너 전용 WSL2 배포판) 방식으로 바꿨습니다(#56). 워크플로 규칙 검사, 서버 강제 명령의 입력·main 포함 검증, 설치 스크립트, 러너 격리 확인 단계는 **자동 테스트·컨테이너에서 검증**했습니다. **러너 PC 격리, 서버 강제 명령 설치, GitHub 설정, 실제 배포 시험은 아직 안 했습니다.** 이 문서의 "직접" 단계는 사용자가 합니다.
> 전제: 서버 준비와 첫 수동 배포를 먼저 끝냅니다([DEPLOY.md](DEPLOY.md) §1~§4).
> 이전 방식(GitHub-hosted 러너 + OIDC + 배포 중 SSH 임시 개방, #42)은 설정하기 전에 바뀌어 실제로 쓰인 적이 없습니다. §9에 기록만 남깁니다.

> ⚠️ **공개 저장소의 Self-hosted Runner는 위험을 "줄이는" 구성이지 "완전히 막는" 구성이 아닙니다.** 남는 위험은 §8에 있습니다.
> ⚠️ **§4의 검증을 모두 마치기 전에는 배포 키 Secret을 등록하지 않고, 자동 배포(`CD_AUTO_DEPLOY`)를 켜지 않습니다.**

## 0. 전체 흐름

```text
main에 병합 → CI(린트·테스트·Docker, GitHub-hosted 러너)가 모두 성공
   └→ Deploy 워크플로 (자동 배포를 켰을 때. 처음엔 수동 버튼으로만)
        ① plan   (GitHub-hosted): 요청 검증. 그 커밋의 실제 CI 실행(ci.yml, main push)을 찾아 3개 작업 성공 확인,
                                  main에 포함된 커밋인지 확인
        ② deploy (내 PC의 러너 전용 WSL 배포판, 라벨 lemit-deploy):
             - 러너 격리 확인(root·sudo·docker 그룹·Windows 드라이브·Windows 프로그램 실행이 있으면 중단)
             - 저장소 코드를 받지 않음. (자동 배포면) 아직 main 최신인지 확인
             - 호스트 키 확인 후 배포 전용 키로 SSH 요청 한 줄: deploy <커밋>
        ③ 서버: 이 키로는 /usr/local/bin/lemit-ci-deploy(root 소유 고정 경로)만 실행
             → 커밋이 origin/main에 포함됐는지 다시 확인 → 저장소의 deploy.sh <커밋> (rollback은 rollback.sh)
```

- **판단은 GitHub 컴퓨터에서, 내 PC에서는 최소한만.** 러너 작업은 `run` 단계 몇 개뿐이고 외부 액션·저장소 코드를 실행하지 않습니다.
- **SSH 방화벽을 열고 닫지 않습니다.** 러너 PC의 공인 IP가 이미 22번에 허용된 관리자 IP이므로 러너에 AWS 권한이 필요 없습니다.
- **배포 키는 배포 명령만 실행합니다.** 셸·포트 포워딩·`.env`·DB 접근이 없습니다. 강제 명령은 저장소 밖 root 소유 경로라, 예전 커밋을 배포해도 강제 명령이 예전 버전으로 바뀌지 않습니다.

## 1. 공개 저장소에서 Self-hosted Runner: 위험과 대응

GitHub는 공개 저장소에 Self-hosted Runner를 권장하지 않습니다. 포크 PR은 **자기가 고친 워크플로 파일**로 실행되므로, PR의 워크플로에 `runs-on: [self-hosted, lemit-deploy]`를 적으면 승인되는 순간 내 러너에서 돕니다. 개인 계정 저장소에서는 러너를 특정 워크플로 전용으로 묶는 기능(러너 그룹, 조직 기능)을 쓸 수 없습니다.

| 위험 | 대응 | 한계 |
|---|---|---|
| 포크 PR이 내 러너에서 코드 실행 | 외부 기여자의 모든 PR은 승인해야 실행(§1-1). 승인 전 `.github/` 변경 확인 | **승인을 잘못 누르면 실행됨** |
| 저장소 안에서 실수로 PR·push 작업이 러너를 씀 | `scripts/check_workflow_policy.py`(CI): self-hosted는 `deploy.yml`의 작업 하나만, 다른 워크플로는 GitHub-hosted 라벨 문자열만, 트리거는 `workflow_run`(CI·completed·main)·`workflow_dispatch`만, 검증 작업의 저장소·이벤트·ref 조건, production·작업 단위 concurrency·액션 미사용 | 조건의 **존재**를 보며 논리 전체를 증명하지는 않음. 포크 PR에는 효과 없음 |
| 러너 작업이 개인 PC에 접근 | 러너 전용 WSL 배포판 + sudo 없는 전용 사용자 + Windows 드라이브·프로그램 실행 끄기(§2). 배포 작업이 매번 확인하고 아니면 중단 | 확인 단계는 배포 작업에만 있음. 승인된 악성 PR 작업은 이 확인을 거치지 않으므로 **격리 자체**가 방어선 |
| 배포 키 유출 | production Environment Secret(main 브랜치만), 키는 한 단계 안에서만 파일로 만들고 삭제, 강제 명령, 서버에서 main 포함 재확인 | 키를 쓰는 동안 같은 러너에 숨어 있던 프로그램은 읽을 수 있음 |
| 예전 커밋 배포로 강제 명령이 약해짐 | 강제 명령을 저장소 밖 root 소유 고정 경로에 설치(§3-2), 저장소 안에서 실행되면 거부 | 갱신은 관리자가 직접(자동 아님) |
| 테스트 안 된 코드 배포 | 실제 CI 실행 ID와 그 작업 결과 확인 + main 포함(plan), 서버도 main 포함 확인 | main에 들어간 커밋은 배포될 수 있음(main 보호 규칙에 의존) |
| 운영 `.env`·DB | 러너는 셸·DB 접근 없음. `deploy.sh`·`rollback.sh`는 `.env`를 읽기만 하고 `down -v`를 쓰지 않음 | 배포 시 마이그레이션은 실행되고, 이미지 롤백은 DB 스키마를 되돌리지 않음(DEPLOY.md) |

### 1-1. 포크 PR 승인 정책 (직접, 가장 먼저)
저장소 → **Settings → Actions → General → Approval for running fork pull request workflows from contributors**
- **"Require approval for all external contributors"** 를 고릅니다. 지금 값(`first_time_contributors`)은 처음 기여하는 사람만 승인을 요구합니다.
- 외부 PR의 "Approve and run"을 누르기 전에 **`.github/` 아래 변경이 있는지** 반드시 확인합니다. 있으면 승인하지 않습니다.
- GitHub 계정 2단계 인증을 켭니다(계정이 탈취되면 워크플로를 바꿔 러너를 쓸 수 있음).

## 2. 러너 실행 환경 (WSL, 직접)

개인 개발 환경과 **분리된** 러너 전용 배포판을 씁니다. 지금 개발에 쓰는 배포판에 설치한 러너는 제거합니다.

1. **러너 전용 배포판을 만듭니다**(PowerShell). 이름은 예시입니다.
   ```powershell
   wsl --install -d Ubuntu-24.04 --name lemit-runner
   ```
   사용 중인 WSL 버전에서 `--name`이 안 되면, 새 배포판을 내보내기·가져오기(`wsl --export`/`wsl --import`)로 다른 이름의 복사본을 만듭니다.
2. **sudo 권한이 없는 전용 사용자**로 러너를 설치·등록합니다. 기존 러너(`lemit-ubuntu-runner`)는 개발 배포판에서 `./config.sh remove`로 등록 해제합니다.
   ```bash
   sudo adduser --disabled-password --gecos "" ghrunner
   id ghrunner          # groups에 sudo·docker가 없어야 함
   ```
3. **Windows 드라이브·프로그램 접근을 끕니다.** 러너 배포판의 `/etc/wsl.conf`:
   ```ini
   [boot]
   systemd=true

   [automount]
   enabled=false

   [interop]
   enabled=false
   appendWindowsPath=false
   ```
   PowerShell에서 `wsl --terminate lemit-runner` 후 다시 엽니다. 이 설정을 바꿀 때만 잠깐 관리자 사용자를 쓰고, 러너는 `ghrunner`로만 돌립니다.
4. **라벨 `lemit-deploy`**: 등록할 때 `./config.sh --url https://github.com/yeeun5364/lemit --token <등록 토큰> --labels lemit-deploy`, 또는 Settings → Actions → Runners에서 라벨 추가.
5. **서비스 실행:** 러너 폴더에서 `sudo ./svc.sh install ghrunner && sudo ./svc.sh start`
   - WSL 배포판은 Windows를 재부팅한 뒤 **배포판이 시작되어야** 러너가 다시 온라인이 됩니다. 재부팅 후 GitHub Settings → Actions → Runners에서 상태를 확인하고, 오프라인이면 배포판을 엽니다(`wsl -d lemit-runner`). 자동 시작 방법(작업 스케줄러 등)은 아직 정하지 않았습니다.
6. **도구:** `ssh`, `ssh-keygen`, `git` (배포 작업이 처음에 확인).
7. **공인 IP:** 러너 배포판에서 `curl -fsS https://checkip.amazonaws.com` 결과가 Lightsail SSH 22번에 허용된 관리자 IP와 같아야 합니다(관리자 IP는 이 공개 문서가 아니라 개인 메모에). 다르면 배포가 "SSH 연결 실패"로 끝납니다. 22번을 넓게 열지 않습니다.

## 3. 서버 호스트 키·배포 전용 SSH 키 (PC + 서버, 직접)

### 3-1. 호스트 키 (가짜 서버 차단)
1. **서버에서** 지문 확인: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
2. **PC에서** 호스트 키 한 줄을 받아 지문 비교:
   ```powershell
   ssh-keyscan -t ed25519 15.135.58.179 2>$null | Tee-Object -Variable hk
   $hk | ssh-keygen -lf -
   ```
3. 두 지문이 **같을 때만** 그 한 줄을 §5의 `LIGHTSAIL_KNOWN_HOSTS`에 씁니다(공개 정보라 Variable).

### 3-2. 강제 명령 설치 + 배포 전용 키
1. **서버에서 강제 명령을 고정 경로에 설치합니다.** 검토한 main 버전에서 실행합니다.
   ```bash
   cd ~/lemit && git fetch origin && git checkout --detach origin/main
   sudo bash scripts/install_ci_deploy.sh      # → /usr/local/bin/lemit-ci-deploy (root 소유, 755)
   ```
   - `scripts/ci_deploy.sh`를 바꾼 PR이 병합되면 **같은 방법으로 다시 설치**합니다. 배포가 자동으로 바꾸지 않습니다. Deploy `check` 결과의 "강제 명령" 줄이 "저장소 버전과 다름"이면 갱신이 필요한지 확인합니다.
2. **PC에서 키를 만듭니다**(암호 없이 엔터 두 번): `ssh-keygen -t ed25519 -f $HOME\.ssh\lemit-github-deploy -C lemit-github-deploy`
3. **서버 `~/.ssh/authorized_keys`에 강제 명령과 함께 한 줄로 등록합니다.** 예전 줄(`command="/home/ec2-user/lemit/scripts/ci_deploy.sh"...`)이 있으면 지웁니다.
   ```bash
   echo 'command="/usr/local/bin/lemit-ci-deploy",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 AAAA...여기에공개키... lemit-github-deploy' >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```

## 4. Secret 연결 전 검증 (직접, 모두 통과해야 다음 단계)

**아래를 모두 확인하기 전에는 §5의 Secret을 등록하지 않고 자동 배포를 켜지 않습니다.** 결과는 개인 메모에 날짜와 함께 남깁니다(IP 등은 공개 문서에 쓰지 않음).

**러너 격리** (러너 배포판에서 `ghrunner`로)

| 확인 | 기대 결과 |
|---|---|
| `id` | uid가 0이 아니고 groups에 sudo·wheel·admin·docker 없음 |
| `sudo -n true` | 실패 |
| `ls /mnt/c` | 없음 |
| `powershell.exe` 또는 `cmd.exe` | 실행 안 됨(명령 없음) |
| `cat /proc/sys/fs/binfmt_misc/WSLInterop` | 파일 없음 또는 `enabled`가 아님 |
| 개발 배포판에서 `wsl -l -v` | 러너가 개발 배포판이 아닌 `lemit-runner`에 있음 |

**SSH 최소 권한** (배포 키로, 러너 배포판 또는 PC에서. `KEY=lemit-github-deploy 키 경로`)

| 명령 | 기대 결과 |
|---|---|
| `ssh -i $KEY ec2-user@서버 status` | 상태 출력, 끝에 "강제 명령: 저장소 버전과 같음" |
| `ssh -i $KEY ec2-user@서버 ls` | "허용하지 않는 요청" (종료 2) |
| `ssh -i $KEY ec2-user@서버 'deploy 0123456789abcdef0123456789abcdef01234567'` | "main에 포함되지 않은 커밋" |
| `ssh -i $KEY -t ec2-user@서버` | 셸이 열리지 않음(빈 요청 거부) |
| `ssh -i $KEY -N -L 15432:localhost:5432 ec2-user@서버` 실행 중 다른 창에서 `127.0.0.1:15432`로 접속 시도 | 포워딩 거부(ssh 창에 `administratively prohibited`) |
| 서버에서 `ls -l /usr/local/bin/lemit-ci-deploy` | `root root`, `-rwxr-xr-x` |
| 서버에서 `grep lemit-github-deploy ~/.ssh/authorized_keys` | `command="/usr/local/bin/lemit-ci-deploy"`와 `no-port-forwarding,...,no-pty,no-user-rc`가 있는 한 줄만 |

## 5. GitHub 설정 (웹, 직접, §4 통과 후)

### 5-1. Environment `production`
저장소 → **Settings → Environments → New environment** → `production`
- **Deployment branches and tags:** "Selected branches and tags" → **`main`만**.
- **Required reviewers (권장):** 본인 계정. 배포마다 승인을 눌러야 러너 작업이 시작되어, PC를 켰을 때 예상하지 못한 배포가 바로 실행되는 것도 막습니다.

### 5-2. Environment 안에 Variables·Secret

| 종류 | 이름 | 값 |
|---|---|---|
| Variable | `LIGHTSAIL_HOST` | `15.135.58.179` (§3-1의 known_hosts 줄과 같은 이름) |
| Variable | `LIGHTSAIL_USER` | `ec2-user` |
| Variable | `LIGHTSAIL_KNOWN_HOSTS` | §3-1에서 확인한 한 줄 |
| **Secret** | `LIGHTSAIL_SSH_KEY` | §3-2의 배포 전용 **개인 키** 전체 |

- 개인 키는 채팅·PR·이슈·파일에 붙여넣지 않고, **러너 배포판에 파일로 두지 않습니다.** 등록 후 PC의 개인 키 파일은 지워도 됩니다.
- 앱 비밀값(Django 비밀키, DB 비밀번호)은 GitHub에 넣지 않습니다(서버 `.env`에만).

### 5-3. 자동 배포 스위치 (§6 시험 후)
Settings → Secrets and variables → Actions → **Variables** → `CD_AUTO_DEPLOY` = `true`. 없으면 수동 버튼만 동작합니다.

## 6. 첫 시험 (직접, 순서대로)

1. **check:** Actions → Deploy → Run workflow(브랜치 main) → `check`
   - plan 성공 → deploy 작업이 `lemit-runner`의 러너에서 시작 → "러너 격리 확인" 통과 → 서버 `status`
2. **deploy:** `deploy`, sha 비움 → plan "CI 실행 …: 3개 작업 모두 성공" → 서버 `deploy.sh` → `배포 완료` → 서비스 주소 확인
3. **같은 버전 다시 deploy:** `OK`로 끝나는지
4. **rollback:** `check`의 이미지 목록에서 이전 태그로 `rollback`
5. **러너가 꺼져 있을 때:** `sudo ./svc.sh stop` 후 `check` → "Waiting for a runner"로 대기 → 서비스 시작 후 이어서 실행되는지
6. **Windows 재부팅 후** 러너가 온라인으로 돌아오는지(§2-5)
7. 모두 성공하면 §5-3 자동 배포를 켭니다.

| 증상 | 원인 후보 |
|---|---|
| "러너가 격리되지 않았습니다: …" | §2의 해당 항목. 고친 뒤 §4를 다시 확인 |
| "production Environment 설정이 빠졌습니다" | §5-2 이름 오타, Environment가 아닌 곳에 등록 |
| deploy 작업이 계속 "Waiting for a runner" | 러너 오프라인(배포판 미시작 포함), 라벨 `lemit-deploy` 누락 |
| "성공한 CI 실행을 찾지 못했습니다" | 그 커밋의 main push CI가 없거나 실패 |
| "SSH 연결 실패" | 러너 공인 IP(§2-7), authorized_keys(§3-2), Secret 내용 |
| `Host key verification failed` | 서버 호스트 키 변경 또는 known_hosts 오류 → **바로 맞추지 말고 원인부터 확인** |
| "main에 포함되지 않은 커밋입니다" | main 밖의 커밋 요청. 서버 `git fetch`가 안 됐으면 "git fetch 실패" |
| "강제 명령은 저장소 밖의 고정 경로에 설치해야 합니다" | authorized_keys가 예전 경로를 가리킴 → §3-2 |
| "다른 배포·롤백이 진행 중" | 서버에서 수동 배포가 돌고 있음 (서버 잠금) |

## 7. 러너 PC가 꺼져 있을 때

| 상황 | 동작 |
|---|---|
| PC가 꺼진 동안 main에 병합 | plan은 GitHub 컴퓨터에서 끝나고, deploy 작업은 맞는 러너를 기다립니다. GitHub 문서 기준 Self-hosted 러너를 기다리는 작업은 **24시간** 뒤 실패합니다. 실패 알림은 GitHub 알림 설정에 따르며 이 워크플로가 보장하지 않습니다. |
| 그사이 배포가 여러 번 | deploy 작업의 동시 실행 그룹(`deploy-production`)에서 진행 중인 것은 취소하지 않고, **기다리는 것은 나중에 들어온 하나만** 남습니다(검증을 통과한 작업만 그룹에 들어감). 읽기 전용 `check`는 별도 그룹이라 대기 중인 배포를 밀어내지 않습니다. |
| PC를 켰는데 남은 자동 배포가 오래된 커밋 | 실행 직전에 main 최신 커밋과 비교해, 더 새 커밋이 있으면 **건너뜁니다**(요약에 '건너뜀'). 새 커밋의 CI가 성공하면 그 배포가 실행됩니다. 새 커밋의 CI가 실패했다면 아무것도 배포되지 않으니 필요하면 수동 `deploy`. |
| 24시간이 지나 실패 | 서버는 바뀌지 않았습니다. PC를 켠 뒤 수동 `deploy`(sha 비움). |
| 배포 도중 PC가 꺼지거나 네트워크가 끊김 | SSH가 끊기면 서버의 `deploy.sh`가 중간에 멈출 수 있습니다. **이 경우의 동작은 시험하지 않았습니다.** 관리자 SSH나 `check`로 상태를 보고, 이상하면 `rollback` 또는 서버에서 `deploy.sh`를 다시 실행합니다(DEPLOY.md). |
| 예상하지 못한 시점의 배포가 싫다면 | §5-1 Required reviewers |

## 8. 남는 위험 (인정하고 운영)

- **외부 PR 승인 실수:** 승인된 포크 PR의 작업은 내 러너에서 실행됩니다. 배포 작업의 격리 확인 단계를 거치지 않으므로, 피해 범위는 §2의 격리 상태에 달려 있습니다.
- **계정 탈취:** 저장소 쓰기 권한이 있으면 워크플로를 바꿔 러너를 쓸 수 있습니다(2단계 인증).
- **러너가 작업마다 새로 만들어지지 않습니다(ephemeral 아님).** 악성 작업이 남긴 프로그램이 다음 배포 때 키를 읽을 수 있습니다. 의심되면 러너 배포판을 지우고 다시 만든 뒤 배포 키를 교체합니다(§3-2).
- **최신 확인과 서버 배포 사이의 짧은 틈:** 러너가 "main 최신"을 확인한 직후 새 커밋이 병합되면, 이전 커밋이 먼저 배포되고 이어서 새 커밋 배포가 실행됩니다.
- **정책 검사의 범위:** 조건 문자열이 있는지 확인하며, 조건식 전체의 논리를 증명하지는 않습니다. 워크플로 변경 PR은 사람이 함께 봅니다.
- **강제 명령 갱신은 수동:** 갱신을 잊으면 서버는 예전(이미 검토한) 강제 명령을 계속 씁니다.
- 관리자 IP(집 인터넷)가 바뀌면 배포가 실패합니다(안전한 쪽 실패).
- 배포 중 약 20~30초 동안 실패한 새 버전이 응답할 수 있습니다(DEPLOY.md §3, 자동 롤백 전).

## 9. 이전 방식 기록 (#42, 사용하지 않음)

GitHub-hosted 러너가 OIDC로 AWS 역할을 받아, 배포하는 동안만 SSH 22번에 러너 IP를 추가하고 끝나면 빼는 방식이었습니다. production Environment와 AWS 설정을 하기 전에 Self-hosted 방식으로 바뀌어 실제 배포에 쓰이지 않았습니다.
- AWS에 OIDC 공급자, 역할 `lemit-github-deploy`, 서버 태그 `lemit-deploy=allowed`를 만들었다면 이제 필요 없습니다.
- `scripts/lightsail_firewall.py`(22번 규칙에서 IP 하나만 추가·제거, 테스트 포함)는 남겨 둡니다. 관리자 PC에 AWS CLI가 있으면 쓸 수 있습니다: `python3 scripts/lightsail_firewall.py close --instance lemit-server --cidr <IP>/32`
