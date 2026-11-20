# GitHub Actions 자동 배포 (CD) 설정 안내

> **상태 (2026-11-19):** 워크플로(`.github/workflows/deploy.yml`)와 서버 스크립트를 만들었습니다. 방화벽 계산, 서버 명령 입력 검증, 배포 잠금은 **자동 테스트로 검증**했습니다. **실제 AWS·GitHub·서버 설정은 아직 안 했습니다.** 아래 §1~§4는 사용자가 직접 하는 단계입니다. 관련 이슈: #42
> 전제: 서버 준비와 첫 수동 배포를 먼저 끝냅니다([DEPLOY.md](DEPLOY.md) §1~§4). 서버에 `/home/ec2-user/lemit` 폴더와 운영 `.env`가 있어야 합니다.

## 0. 전체 흐름 (한 번 읽어 보기)

```text
main에 병합 → CI(린트·테스트·Docker)가 모두 성공
   └→ Deploy 워크플로 시작 (자동 배포를 켰을 때. 처음엔 수동 버튼으로만)
        1. 이 커밋이 main에 있고 CI 3개가 성공했는지 다시 확인
        2. AWS 로그인: OIDC 1회용 출입증 (오래 쓰는 AWS 키를 GitHub에 두지 않음)
        3. Lightsail 방화벽 22번 규칙에 "이 GitHub 컴퓨터 IP 하나"를 추가
        4. 서버 신원 확인(호스트 키) 후 배포 전용 키로 SSH 접속
           → 서버는 이 키로는 scripts/ci_deploy.sh만 실행 → deploy.sh <커밋>
        5. 성공·실패와 관계없이 22번 규칙에서 그 IP만 다시 뺌 (관리자 IP·다른 규칙은 그대로)
```

**왜 이렇게 하나요?**
- **평소에는 SSH 22번을 관리자 IP만 허용합니다.** 그러면 GitHub 컴퓨터(IP가 매번 바뀜)는 들어올 수 없습니다. 그래서 배포하는 몇 분 동안만 그 IP 하나를 열어 줍니다.
- **AWS 키를 GitHub에 저장하지 않습니다.** OIDC는 "이 저장소의 `production` 환경에서 돈 워크플로"에만 AWS가 잠깐 쓸 출입증을 내주는 방식이라, 유출될 장기 키가 없습니다.
- **배포 전용 열쇠는 배포 명령만 실행합니다.** 혹시 유출되더라도 서버에서 셸을 열 수 없습니다(`deploy <커밋>`, `rollback <태그>`, `status`만 허용).
- **서버 신원 확인:** 처음 보는 서버라도 일단 접속하는 방식(`StrictHostKeyChecking=no`)은 쓰지 않습니다. 미리 저장한 서버 호스트 키와 다르면 접속을 거부합니다.

**필요한 것 체크리스트**

| 어디에 | 무엇을 | 단계 |
|---|---|---|
| Lightsail + AWS IAM | 서버 태그 `lemit-deploy=allowed`, OIDC 공급자, 역할 `lemit-github-deploy`(권한: 방화벽 읽기 + 태그 붙은 서버의 방화벽 쓰기) | §1 |
| PC | 서버 호스트 키 지문 확인 | §2 |
| PC + 서버 | 배포 전용 SSH 키 만들기, 서버 `authorized_keys`에 강제 명령으로 등록 | §3 |
| GitHub | Environment `production` + Variables 6개 + Secret 1개 | §4 |
| GitHub | 첫 시험: check → deploy → rollback, 그다음 자동 배포 켜기 | §5 |

## 1. AWS IAM 설정 (AWS 콘솔, 직접)

Lightsail도 같은 AWS 계정이라 **IAM 콘솔**(콘솔 상단 검색창에 IAM)에서 합니다.

### 1-1. OIDC 공급자 추가 (계정에 한 번만)
IAM → **자격 증명 공급자(Identity providers)** → **공급자 추가**
- 공급자 유형: **OpenID Connect**
- 공급자 URL: `https://token.actions.githubusercontent.com`
- 대상(Audience): `sts.amazonaws.com`

### 1-2. 서버에 태그 달기 (Lightsail 콘솔)
Lightsail → `lemit-server` → **태그** → 키-값 태그 추가: 키 `lemit-deploy`, 값 `allowed`
- 아래 정책은 **이 태그가 붙은 인스턴스의 방화벽만** 바꿀 수 있게 합니다(PR #43 리뷰 반영). AWS 문서상 `PutInstancePublicPorts`는 태그 기반 권한 제어를 지원합니다.

### 1-3. 권한 정책 만들기
IAM → **정책** → **정책 생성** → JSON 탭에 아래를 붙여넣고, 이름을 `lemit-lightsail-ssh-window`로 정합니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadFirewall",
      "Effect": "Allow",
      "Action": "lightsail:GetInstancePortStates",
      "Resource": "*",
      "Condition": {"StringEquals": {"aws:RequestedRegion": "ap-southeast-2"}}
    },
    {
      "Sid": "WriteFirewallOnlyOnTaggedInstance",
      "Effect": "Allow",
      "Action": "lightsail:PutInstancePublicPorts",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "ap-southeast-2",
          "aws:ResourceTag/lemit-deploy": "allowed"
        }
      }
    }
  ]
}
```

- 권한은 **방화벽 읽기**(읽기 전용, 태그 조건은 문서에 지원이 명시되지 않아 리전으로만 제한)와 **태그 붙은 인스턴스의 방화벽 쓰기**뿐입니다. 이 역할로는 인스턴스를 켜고 끄거나 지울 수 없습니다.
- 첫 시험에서 `AccessDenied ... PutInstancePublicPorts`가 나오면 1-2의 태그(키·값 철자)를 확인하세요. 그래도 안 되면 그 오류 내용을 알려주세요. 태그 조건 대신 리전 조건만 쓰는 방식으로 바꿀지 함께 정합니다.
- **주의:** 방화벽 쓰기 권한이 있으므로, 이 역할을 쓰는 워크플로가 악용되면 그 인스턴스의 다른 포트도 열 수 있습니다. 그래서 아래 신뢰 정책과 §4 Environment 승인으로 **이 저장소의 production 배포만** 이 역할을 쓰게 묶습니다.

### 1-4. 역할 만들기
IAM → **역할** → **역할 생성**
1. 신뢰할 수 있는 엔터티: **웹 자격 증명(Web identity)** → 공급자 `token.actions.githubusercontent.com`, Audience `sts.amazonaws.com`.
   GitHub 조직·저장소를 묻는 칸이 나오면 `yeeun5364`, `lemit`을 입력합니다.
2. 권한: `lemit-lightsail-ssh-window` 선택
3. 이름: `lemit-github-deploy`
4. 만든 뒤 역할의 **신뢰 관계 → 신뢰 정책 편집**에서 `Condition`을 아래처럼 **production 환경으로 좁힙니다.** `<계정ID>`는 콘솔 오른쪽 위 계정 메뉴의 12자리 숫자입니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Federated": "arn:aws:iam::<계정ID>:oidc-provider/token.actions.githubusercontent.com"},
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:yeeun5364/lemit:environment:production"
        }
      }
    }
  ]
}
```

5. 역할 화면 위쪽의 **ARN**(`arn:aws:iam::<계정ID>:role/lemit-github-deploy`)을 복사해 둡니다. §4의 `AWS_ROLE_ARN`에 넣습니다.

## 2. 서버 호스트 키 확인 (가짜 서버 차단용)

1. **서버(SSH 접속한 창)에서** 지문을 봅니다.
   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```
   `256 SHA256:xxxx... (ED25519)` 형태로 나옵니다. 처음 SSH 접속할 때 확인했던 지문과 같아야 합니다.
2. **PC의 PowerShell에서** 호스트 키 한 줄을 받아 지문을 비교합니다.
   ```powershell
   ssh-keyscan -t ed25519 15.135.58.179 2>$null | Tee-Object -Variable hk
   $hk | ssh-keygen -lf -
   ```
3. 두 지문이 **같을 때만**, `ssh-keyscan`이 출력한 한 줄(`15.135.58.179 ssh-ed25519 AAAA...`)을 §4의 `LIGHTSAIL_KNOWN_HOSTS`에 넣습니다. 이 값은 공개 정보라 Variable로 둡니다.

## 3. 배포 전용 SSH 키 (PC + 서버, 직접)

지금 쓰는 `lemit-key.pem`은 서버 전체를 다룰 수 있는 **관리자 키**입니다. GitHub에는 **배포만 할 수 있는 새 키**를 따로 만들어 줍니다.

1. **PC(PowerShell)에서 키를 만듭니다.** GitHub가 자동으로 써야 해서 암호는 비웁니다. 암호를 물으면 **아무것도 입력하지 않고 엔터를 두 번** 누릅니다.
   ```powershell
   ssh-keygen -t ed25519 -f $HOME\.ssh\lemit-github-deploy -C lemit-github-deploy
   Get-Content $HOME\.ssh\lemit-github-deploy.pub   # 공개 키 한 줄 (ssh-ed25519 AAAA... lemit-github-deploy)
   ```
2. **서버에서 공개 키를 "강제 명령"과 함께 등록합니다.** 아래 한 줄의 `ssh-ed25519 AAAA...` 부분을 1번에서 나온 공개 키로 바꿔 실행합니다.
   ```bash
   echo 'command="/home/ec2-user/lemit/scripts/ci_deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA...여기에공개키... lemit-github-deploy' >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```
   이 키로 접속하면 서버는 무엇을 요청받든 `scripts/ci_deploy.sh`만 실행합니다.
3. **PC에서 시험합니다.** 첫 줄은 상태가 출력되어야 하고, 둘째 줄은 거부되어야 합니다.
   ```powershell
   ssh -i $HOME\.ssh\lemit-github-deploy ec2-user@15.135.58.179 status   # 상태 출력
   ssh -i $HOME\.ssh\lemit-github-deploy ec2-user@15.135.58.179 ls       # "허용하지 않는 요청" → 정상
   ```
   PC는 관리자 IP라 방화벽을 통과합니다. GitHub는 배포할 때 워크플로가 잠깐 열어 줍니다.
4. **개인 키 내용**을 §4의 Secret `LIGHTSAIL_SSH_KEY`에 붙여넣습니다.
   ```powershell
   Get-Content $HOME\.ssh\lemit-github-deploy -Raw | Set-Clipboard   # 화면에 띄우지 않고 클립보드로
   ```
   - **이 내용을 채팅·PR·이슈·파일에 붙여넣지 않습니다.** GitHub Secret 입력칸에만 붙여넣습니다.
   - 등록 후 PC의 개인 키 파일은 지워도 됩니다. 다시 필요하면 새로 만들어 등록합니다.

## 4. GitHub 설정 (웹, 직접)

### 4-1. Environment 만들기
저장소 → **Settings → Environments → New environment** → 이름 `production`
- **Required reviewers:** 본인 계정을 체크합니다(권장). 배포마다 "승인" 버튼을 눌러야 진행됩니다. 수업 복습 중에는 실수 방지에 좋습니다.
- **Deployment branches and tags:** "Selected branches and tags" → `main`만 추가합니다.

### 4-2. Environment 안에 Variables·Secret 등록
같은 `production` 화면 아래쪽에 등록합니다. 저장소 전체 설정이 아니라 **Environment에** 넣어야 production 배포 작업만 읽을 수 있습니다.

| 종류 | 이름 | 값 |
|---|---|---|
| Variable | `AWS_REGION` | `ap-southeast-2` |
| Variable | `AWS_ROLE_ARN` | §1-4에서 복사한 역할 ARN |
| Variable | `LIGHTSAIL_INSTANCE_NAME` | `lemit-server` |
| Variable | `LIGHTSAIL_HOST` | `15.135.58.179` |
| Variable | `LIGHTSAIL_USER` | `ec2-user` |
| Variable | `LIGHTSAIL_KNOWN_HOSTS` | §2에서 확인한 한 줄 |
| **Secret** | `LIGHTSAIL_SSH_KEY` | §3의 배포 전용 **개인 키** 전체(`-----BEGIN ... END ...-----` 포함) |

- 앱 비밀값(Django 비밀키, DB 비밀번호)은 **GitHub에 넣지 않습니다.** 서버의 `.env`에만 있습니다.

### 4-3. 자동 배포 스위치 (나중에)
저장소 → **Settings → Secrets and variables → Actions → Variables 탭 → New repository variable**
- 이름 `CD_AUTO_DEPLOY`, 값 `true`
- **§5의 수동 시험이 모두 성공한 뒤에** 만듭니다. 없거나 `true`가 아니면 자동 배포는 하지 않고 수동 버튼만 동작합니다.

## 5. 첫 시험 (직접, 순서대로)

**준비**
- Lightsail 방화벽의 SSH 22번이 **관리자 IP로만 제한**되어 있어야 이 방식의 의미가 있습니다([DEPLOY.md](DEPLOY.md) §1 3번). 바꾸기 전에 SSH 창 하나를 열어 둔 상태에서 진행합니다.
- **기준 방화벽 규칙을 적어 둡니다.** 시험 단계마다 이 표와 콘솔 화면을 비교합니다. 이 문서는 공개 저장소에 있으니 **관리자 IP는 이 문서가 아니라 개인 메모에** 적습니다.

  | 규칙 | IPv4 | IPv6 | 비고 |
  |---|---|---|---|
  | SSH 22 | 관리자 IP만 (+ 브라우저 SSH 허용 체크 여부) | 제한 또는 삭제 | 배포 중에만 러너 IP 1개가 잠깐 추가됨 |
  | HTTP 80 | 모든 IP | 모든 IP 또는 없음 | |
  | HTTPS 443 | 도메인 연결 후 | 도메인 연결 후 | |

- **시험하는 동안 콘솔에서 방화벽을 바꾸지 않습니다.** 스크립트는 그사이 변경을 감지하면 덮어쓰지 않고 다시 시도하거나 실패로 멈춥니다. 다만 Lightsail API에는 "내가 읽은 뒤 아무도 안 바꿨을 때만 쓰기" 기능이 없어 아주 짧은 틈은 남습니다.

1. **check** (연결만 확인, 배포 안 함): Actions → **Deploy** → **Run workflow** → action `check`
   - 로그 확인 순서: 설정값 확인 → AWS 인증 → 러너 IP 추가 → 서버 `status` 출력(현재 태그·기록·컨테이너·공개 경로) → **러너 IP 제거**
   - 끝난 뒤 Lightsail 콘솔에서 22번 규칙이 **원래대로**(관리자 IP만)인지 기준 표와 비교합니다. 80·IPv6 규칙도 그대로인지 봅니다.
2. **deploy:** action `deploy`, sha는 비워 둠(main 최신)
   - "CI 3개 작업 모두 성공" 확인 → 서버 `deploy.sh` 출력 → `배포 완료` → 러너 IP 제거
   - 브라우저로 `http://15.135.58.179`에서 분류 동선을 확인합니다.
   - 콘솔에서 방화벽 규칙이 기준 표와 같은지 다시 확인합니다(이후 3·4단계 뒤에도 매번).
3. **같은 버전 다시 deploy:** 안전하게 `OK`로 끝나는지 확인합니다. 빌드 캐시를 쓰고, 마이그레이션이 없고, 컨테이너도 그대로입니다.
4. **rollback:** `check` 결과의 이미지 목록에서 이전 태그를 골라 action `rollback`, rollback_tag에 입력합니다.
4-1. **실패·취소해도 정리되는지:** `deploy`를 실행한 뒤 "서버에서 실행" 단계가 진행 중일 때 Actions 화면에서 **Cancel workflow**를 누릅니다.
   - 로그에서 "SSH 22번에서 러너 IP 제거" 단계가 실행되어 성공했는지 확인합니다.
   - 콘솔의 22번 규칙에 러너 IP가 남지 않았는지 확인합니다.
   - 서버에서는 진행 중이던 배포가 끝까지 돌 수 있습니다. 끝난 뒤 `check`로 상태를 봅니다.
5. 모두 성공하면 §4-3 `CD_AUTO_DEPLOY=true`를 켭니다. 이후에는 main에 병합하고 CI가 성공하면 자동으로 배포됩니다(production 승인을 켰다면 승인 후).

**확인할 것과 실패 시 볼 곳**

| 증상 | 원인 후보 |
|---|---|
| "production Environment 설정이 빠졌습니다" | §4-2 이름 오타, Environment가 아닌 곳에 등록 |
| AWS 인증 실패 (`Not authorized to perform sts:AssumeRoleWithWebIdentity`) | §1-4 신뢰 정책의 `sub`(저장소·환경 이름), OIDC 공급자 Audience |
| `AccessDenied ... PutInstancePublicPorts` | §1-3 정책 미연결, §1-2 태그(`lemit-deploy=allowed`) 누락·오타, 리전 조건 |
| "SSH 연결 실패" | §2 호스트 키 줄, §3 authorized_keys 등록(한 줄, 따옴표), 배포 키 Secret 내용 |
| `Host key verification failed` | 서버 호스트 키가 바뀌었거나 `LIGHTSAIL_KNOWN_HOSTS`가 틀림 → §2 다시 확인. **바로 맞추지 말고 원인부터 확인** |
| "러너 IP가 방화벽에 남아 있습니다" | §7 수동 복구 |
| "읽는 사이 규칙이 바뀌었습니다" / "방화벽이 기대와 다릅니다" | 배포 중 콘솔에서 방화벽을 바꿨거나 다른 작업이 동시에 바꿈 → §7에서 기준 표와 비교 후 다시 실행 |
| "다른 배포·롤백이 진행 중" | 서버에서 수동 배포가 돌고 있음 (서버 잠금) |

## 6. 안전장치 요약

| 위험 | 장치 |
|---|---|
| 테스트 안 된 코드 배포 | main에 포함된 커밋 + CI 3개 성공만 배포 (워크플로가 GitHub API로 재확인) |
| AWS 키 유출 | 장기 키 없음(OIDC). 역할은 production 환경에서만, 방화벽 읽기·쓰기만 |
| SSH 상시 노출 | 22번은 관리자 IP만. 배포 중에만 러너 IP /32 추가 후 반드시 제거(`if: always()`), 제거 확인 |
| 배포 키 유출 | 강제 명령: `deploy <SHA 40자리>`·`rollback <태그 12자리>`·`status`만. 셸·포트 포워딩 불가 |
| 가짜 서버 접속 | 저장한 호스트 키로만 접속(`StrictHostKeyChecking=yes`) |
| 동시 배포 | 워크플로 동시 실행 1개 + 서버 `flock` 잠금 |
| 공개 로그 노출 | 방화벽 규칙(관리자 IP)·키·`.env`를 출력하지 않음 |
| fork PR 악용 | 배포는 이 저장소 main의 push에 대한 CI 성공일 때만(fork·PR 이벤트 제외). 스크립트는 항상 main에서 가져옴 |

**남는 한계**
- 러너가 갑자기 죽으면(`always()`도 못 도는 경우) 러너 IP가 22번에 남을 수 있습니다. §7로 확인·복구합니다.
- 방화벽은 "전체 다시 쓰기" API로 바꿉니다. 쓰기 직전 다시 읽어 비교하고 쓴 뒤 검증하지만, API에 조건부 쓰기가 없어 아주 짧은 틈은 남습니다. 배포 중 콘솔 변경을 피합니다.
- 배포 중 약 20~30초 동안 실패한 새 버전이 응답할 수 있습니다(DEPLOY.md §3, 자동 롤백 전).

## 7. 방화벽 수동 확인·복구 (러너가 비정상 종료됐거나 실패했을 때)

언제 하나요?
- 워크플로가 "러너 IP가 남아 있습니다", "방화벽이 기대와 다릅니다", "계속 바뀌어 중단" 오류로 끝났을 때
- 러너가 갑자기 죽거나 오래 멈춰 정리 단계가 실행되지 않았을 때(로그에 "SSH 22번에서 러너 IP 제거"가 없음)

순서
1. **Actions 로그에서 러너 IP를 확인합니다.** "러너 공인 IP 확인" 단계에 `러너 IP: x.x.x.x`로 나옵니다.
2. **Lightsail 콘솔** → `lemit-server` → **네트워킹** → IPv4 방화벽의 SSH 22 규칙을 엽니다.
   - 허용 IP 목록에서 그 러너 IP만 지웁니다. **관리자 IP는 지우지 않습니다.**
   - 22번 규칙이 러너 IP만 허용하고 있었다면(원래 22번 규칙이 없던 경우) 규칙 자체를 삭제합니다.
3. **§5 기준 표와 비교합니다.** 80·443·IPv6 규칙이 바뀌었으면 기준 표대로 되돌립니다.
4. **PC에서 관리자 SSH 접속을 확인합니다.** 지금 열린 창은 닫지 말고, 새 창에서 접속합니다.
5. 원인이 정리되면 Actions에서 `check`를 다시 실행합니다. 정상이면 22번 규칙이 기준 표와 같은 상태로 끝납니다.

콘솔 대신 AWS CLI를 쓸 수 있다면, 같은 스크립트로 그 IP만 정확히 뺄 수 있습니다. 관리자 PC에 AWS CLI가 설정되어 있을 때만 해당합니다.
```bash
python3 scripts/lightsail_firewall.py close --instance lemit-server --cidr <러너IP>/32
```

