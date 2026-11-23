# GitHub Actions 자동 배포 (CD) 설정 안내 — Self-hosted Runner

> **상태 (2026-11-20):** CD를 **Self-hosted Runner**(개인 PC의 러너 전용 WSL2 배포판) 방식으로 바꿨습니다(#56). 워크플로 규칙 검사, 서버 강제 명령의 입력·main 포함 검증, 설치 스크립트, 러너 격리 확인 단계는 **자동 테스트·컨테이너에서 검증**했습니다. **러너 PC 격리, 서버 강제 명령 설치, GitHub 설정, 실제 배포 시험은 아직 안 했습니다.** 이 문서의 "직접" 단계는 사용자가 합니다.
> **버전 태그 배포 (2026-11-22):** 자동 배포를 main push에서 **버전 태그(`vX.Y.Z`) push**로 바꿨습니다. PR·main push는 CI만 실행합니다. 태그 판정(워크플로)과 서버 `deploy-tag`는 **자동 테스트로 검증**했고, 실제 태그 배포는 PR 검토 후 새 태그로 시험합니다(§6-2). 기존 `v0.1.0` 태그는 그대로 둡니다.
> **태그 파이프라인 (2026-11-22, PR 검토 전):** 태그 배포를 Deploy 워크플로 한 실행의 `plan → test → train → parity → image → release → deploy`로 바꿨습니다. 태그 커밋으로 학습한 모델을 넣어 GitHub-hosted에서 시험한 이미지를 GHCR에 게시하고, Release에 기록한 digest를 서버가 받아 실행합니다(서버 빌드 없음). 단계 판정·서버 `deploy-image`·Release 기록은 **자동 테스트**로, 이미지 빌드·실행·게시·digest 재수신·서버 이미지 모드 배포·전환 전 버전 롤백은 **로컬 Docker 시험**으로 확인했습니다. **GitHub에서 실제 실행, GHCR 공개 설정, 서버 강제 명령 갱신, 실제 태그 배포는 아직입니다**(§5-4, §6-2). `v0.1.1` 태그와 Self-hosted 러너의 SSH `check` 성공은 사용자가 확인했습니다(이 PR과 무관, 그대로 둠).
> 전제: 서버 준비와 첫 수동 배포를 먼저 끝냅니다([DEPLOY.md](DEPLOY.md) §1~§4).
> 이전 방식(GitHub-hosted 러너 + OIDC + 배포 중 SSH 임시 개방, #42)은 설정하기 전에 바뀌어 실제로 쓰인 적이 없습니다. §9에 기록만 남깁니다.

> ⚠️ **공개 저장소의 Self-hosted Runner는 위험을 "줄이는" 구성이지 "완전히 막는" 구성이 아닙니다.** 남는 위험은 §8에 있습니다.
> ⚠️ **§4의 검증을 모두 마치기 전에는 배포 키 Secret을 등록하지 않고, 자동 배포(`CD_AUTO_DEPLOY`)를 켜지 않습니다.**

## 0. 전체 흐름

```text
PR·main push → CI(린트·테스트·Docker, GitHub-hosted)만 실행. 배포하지 않음

버전 태그 push (예: git tag -a v0.1.2 -m v0.1.2 <main 커밋> && git push origin v0.1.2)
   └→ ① Release tag 워크플로 (가벼운 진입점. GitHub-hosted, 읽기 권한, Secret 없음, .github/workflows/release-tag.yml)
         - 태그 형식 vMAJOR.MINOR.PATCH, 태그가 가리키는 커밋, 그 커밋이 main에 포함됐는지
         - 그 커밋의 main push CI(ci.yml) 결과를 기다림 (진행 중이면 최대 30분). 성공하면 이 워크플로 성공
   └→ ② Deploy 워크플로 (workflow_run: 언제나 main의 deploy.yml로 실행. CD_AUTO_DEPLOY=true일 때만)
         한 실행 화면에 7개 작업이 needs로 이어진다. 앞 작업이 실패하면 뒤 작업은 실행되지 않는다.
         0. plan    (GitHub-hosted) Release tag 결과를 믿지 않고 다시 확인 — 태그 → 커밋, 가장 높은 버전 태그인지,
                    그 커밋의 실제 main push CI 실행과 3개 작업 성공, main 포함
         1. test    (GitHub-hosted) 태그 커밋으로 ruff·워크플로 규칙·check·마이그레이션 누락·ml.verify·Django 테스트
         2. train   (GitHub-hosted) 태그 커밋의 데이터·코드로 학습 → 성능 기준(검증 macro F1 ≥ 0.80) 통과 시에만 저장
                    → ml.verify → 학습 환경 예측 기록. 모델 버전·검증 점수·모델 해시를 출력, 파일은 아티팩트 model로
         3. parity  (GitHub-hosted) 같은 모델·같은 입력으로 학습 환경 ↔ 서비스 런타임(Dockerfile 기반 이미지) 예측 비교
         4. image   (GitHub-hosted) 그 모델을 넣어 빌드 → 컨테이너 실행(/healthz/·추론 1건·모델 해시·이미지 안 parity)
                    → GHCR에 게시 → digest로 다시 받아 시험한 이미지와 같은지 확인
         5. release (GitHub-hosted) GitHub Release에 태그·커밋·모델 버전·모델 해시·검증 점수·이미지 digest 기록
                    (같은 버전 재시도는 기록을 재사용, 기록된 digest와 다르면 덮어쓰지 않고 실패)
         6. deploy  (내 PC의 러너 전용 WSL 배포판, 라벨 lemit-deploy, production 승인 후)
             - 러너 격리 확인(root, 관리자·docker 그룹, 비밀번호 없는 sudo, Windows 드라이브 마운트, Windows 프로그램 실행이 있으면 중단)
             - 저장소 코드를 받지 않음. 태그가 그대로이고 가장 높은 버전인지 다시 확인 (PC가 늦게 켜진 경우)
             - 호스트 키 확인 후 배포 전용 키로 SSH 요청 한 줄: deploy-image <태그> <커밋> <digest> (수동 배포는 deploy <커밋>)
   └→ ③ 서버: 이 키로는 /usr/local/bin/lemit-ci-deploy(root 소유 고정 경로)만 실행
         → origin에서 태그가 그 커밋을 가리키는지, 커밋이 origin/main에 포함됐는지, GitHub Release에 기록된 커밋·digest가
           요청과 같은지 다시 확인 → 서버 잠금을 기다려 잡음
         → 롤백 뒤 보류 중이거나 그 커밋·digest가 이미 배포돼 있으면 '건너뜀'으로 끝 (서버는 바뀌지 않음)
         → 작업 트리를 그 커밋으로 바꾸고 그 커밋의 deploy.sh를 DEPLOY_IMAGE=<저장소>@<digest>로 실행:
           digest로 이미지를 받아(빌드하지 않음) lemit-web:<커밋 12자리> 태그 → check --deploy → 마이그레이션 → 교체
           → 스모크 → 공개 /healthz/ → 실행 컨테이너 이미지 ID 확인 → 성공 기록(OK, current_image)

수동: Actions → Deploy → Run workflow (main) → check / deploy / rollback  (plan·deploy만 실행. deploy는 서버에서 빌드)
```

**여섯 단계를 보는 곳.** Actions → **Deploy** → 해당 실행. 왼쪽 작업 목록과 그래프에 `0. plan` → `1. test` → `2. train` → `3. parity` → `4. image` → `5. release` → `6. deploy`가 순서대로 보이고, 실행 요약(Summary) 아래에 작업마다 결과 표가 붙습니다(대상 커밋, 모델 버전·점수·해시, parity 비교 표, 이미지 digest, Release 링크, 서버 실행 결과·공개 경로 점검). 앞의 Release tag 실행은 Actions → **Release tag**에 따로 남습니다(진입점, 대개 CI 대기 시간만큼 걸림). `0. plan`은 수동 실행(check·deploy·rollback)과 같이 쓰는 검증 작업이라 여섯 단계 앞에 둡니다.

**왜 이렇게 나눴나 (한 화면 + 운영 권한 보호).**
- `push`로 실행되는 워크플로는 **태그가 가리키는 커밋 안의** 워크플로 파일을 씁니다. 태그 커밋의 파일이 곧바로 러너나 배포 키를 쓰면, main 밖 커밋에 고친 워크플로를 넣고 태그를 다는 것만으로 운영 권한에 닿을 수 있습니다. 그래서 태그는 GitHub-hosted이고 읽기 권한만 있는 `release-tag.yml`이 받고(진입점), 여섯 단계는 **언제나 기본 브랜치(main)의 파일로 실행되는** `workflow_run` 워크플로(`deploy.yml`) 한 실행에 둡니다.
- 1~5는 태그 커밋의 코드를 실행하지만 GitHub-hosted이고, production Environment가 없어 배포 키를 받을 수 없습니다. 작업마다 필요한 권한만 줍니다: test·train·parity는 `contents: read`, image만 `packages: write`(GHCR 게시, 빌드·시험을 마친 뒤 로그인), release만 `contents: write`(저장소 코드를 받지 않음). Self-hosted 러너와 배포 키는 production 승인을 받은 `6. deploy`만 씁니다.
- 정책 검사(`scripts/check_workflow_policy.py`, CI 린트 작업)가 이 구성을 고정합니다: 작업 목록·needs 순서, 상태 함수(`always()` 등)로 실패를 건너뛰지 않음, 작업별 권한, Secret·Environment는 deploy만, 모든 checkout이 대상 커밋을 명시, 액션 SHA 고정, 태그 배포 요청은 `deploy-image`로만.

**커밋 SHA 두 가지.** `workflow_run`에서 `github.sha`는 **main의 최신 커밋**(워크플로 파일을 읽은 커밋)이고 배포 대상이 아닙니다. 배포할 커밋은 Release tag 실행의 `head_sha`이고, plan이 태그 → 커밋으로 다시 확인해 `needs.plan.outputs.sha`(작업 환경변수 `TARGET_SHA`)로 넘깁니다. 1~5의 모든 checkout은 `ref`로 이 값을 명시하고(`persist-credentials: false`), 받은 HEAD가 이 값인지 다시 확인합니다. 이 값이 이미지 라벨(`org.opencontainers.image.revision`), Release 기록(`commit=`), 서버 요청(`deploy-image <태그> <커밋> <digest>`)에 그대로 들어가고, 서버는 받은 이미지의 라벨이 배포할 커밋과 같은지 확인합니다. 각 작업 요약에 두 값을 따로 적습니다.

**중복 배포를 막는 방법.** 자동 배포 경로는 태그 하나뿐입니다(main push 자동 배포 `deploy-auto`와 서버 빌드 태그 배포 `deploy-tag`는 워크플로에서 뺐고, 정책 검사가 다시 넣지 못하게 막습니다. 강제 명령에는 예전 요청이 남아 있지만 쓰이지 않습니다). 같은 커밋·같은 digest가 이미 배포돼 있으면 서버가 건너뜁니다. 같은 버전을 다시 실행해도 Release는 하나이고(기록이 같으면 재사용, 다르면 실패), 태그 여러 개를 연달아 올리면 가장 높은 버전만 배포합니다(plan·러너가 낮은 버전은 건너뜀, 대기 중인 deploy는 가장 최근 것 하나만 남음).

**배포·롤백 직렬화와 롤백 보존 (PR #58 리뷰 P2).** GitHub concurrency 그룹은 진행 1개 + 대기 1개만 두고, 새 대기 작업이 기존 대기 작업을 취소합니다. 그래서 deploy와 rollback이 한 그룹이면 "배포 A 진행 중 + 수동 롤백 B 대기" 상태에서 최신 main 자동 배포 C가 들어올 때 B가 취소될 수 있었습니다. 지금은:

| 장치 | 위치 | 효과 |
|---|---|---|
| 그룹 분리: `deploy-production`(태그·수동 deploy), `deploy-production-rollback`, `deploy-production-check` | `deploy.yml` deploy 작업 | 새 자동 배포가 대기 중인 롤백을 취소하지 못함. 대기 중인 deploy는 가장 최근 것 하나만 남음 |
| 서버 잠금 대기(최대 20분) | 강제 명령 `ci_deploy.sh` → 잠금을 `deploy.sh`·`rollback.sh`에 넘김 | 그룹이 달라 동시에 시작된 deploy·rollback도 서버에서는 하나씩 실행 |
| 자동 배포 보류 (2단계) | ① 대기 표시 `deploy/state/rollback_pending/<PID>`: `rollback`이 잠금을 기다리기 **전에** 만들고 잠금을 얻으면 지움. 수동 deploy는 지우지 않음(프로세스가 없는 표시는 오래된 것으로 보고 정리). ② 확정 보류 `deploy/state/auto_deploy_hold`: `rollback`이 잠금을 잡은 **뒤** 롤백 직전에 만듦. 태그 배포 `deploy-image`(예전 `deploy-tag`·`deploy-auto`도)는 잠금을 잡은 뒤 둘 중 하나라도 있으면 건너뜀 | B·C가 어떤 순서로 잠금을 잡든 끝 상태는 "롤백됨, 자동 배포는 건너뜀". 보류 중에는 새 태그를 올려도 배포되지 않음(테스트). 수동 deploy가 대기 중인 롤백보다 먼저 잠금을 잡아도 그 롤백이 실행되며 보류가 다시 확정됨(3차 리뷰 P2). 확정 보류는 **수동 `deploy`가 성공한 뒤에만 풀림**(4차 리뷰): 실패하면 유지되고, 배포하는 사이에 새 롤백이 건 보류도 남음(시작할 때 본 보류 내용과 같을 때만 지움). `check`에 표시됨 |
| 예전 작업 트리 호환 | 실행할 `deploy.sh`·`rollback.sh`가 잠금 넘겨받기(`DEPLOY_LOCK_HELD`)를 모르면 강제 명령이 잠금을 놓고 실행. `deploy-image`는 작업 트리를 대상 커밋으로 바꾼 뒤 **그 커밋의** `deploy.sh`를 실행하고, 그 파일이 이미지 배포(`DEPLOY_IMAGE`)를 모르면 빌드로 대신하지 않고 실패 | 강제 명령만 먼저 갱신한 서버, 예전 커밋을 수동 배포한 뒤에도 배포·롤백이 동작(3차 리뷰 P1). 이 기능 이전 커밋에 태그를 달면 서버에서 빌드되지 않고 멈춤 |

- 시험: `scripts/test_ci_deploy.sh`에서 배포 A가 잠금을 잡은 상태로 롤백 B와 자동 배포 C를 두 순서로 보내 끝 상태를 확인합니다(DRY_RUN). GitHub의 그룹 대기·취소 동작 자체는 로컬에서 재현할 수 없어 정책 검사(그룹 식이 정확히 이 세 갈래인지)로 고정하고, 실제 동작은 §6의 첫 시험에서 확인합니다.
- 수동 요청끼리(수동 deploy와 rollback)는 서버 잠금을 먼저 잡은 순서로 실행됩니다. 마지막에 실행된 것이 롤백이면 보류가 확정돼 있고, 성공한 수동 deploy면 풀려 있습니다(테스트로 확인).
- 시험(자식 스크립트까지 실제 실행): 잠금 코드만 진짜와 같은 가짜 `deploy.sh`·`rollback.sh`로 새 작업 트리·예전 작업 트리(이 기능 이전 잠금 코드) 각각에서 배포·롤백이 실행되는지, "배포 A 진행 + 수동 배포 D·롤백 B 대기" 경합을 3회 반복해 위 규칙이 지켜지는지, `deploy-image`가 대상 커밋으로 바꾼 뒤 기록된 digest로 `deploy.sh`를 실행하는지 확인합니다.
- 서버에서 관리자가 `scripts/rollback.sh`를 직접 실행한 경우에는 보류가 걸리지 않습니다(강제 명령을 거친 rollback만).

- **판단은 GitHub 컴퓨터에서, 내 PC에서는 최소한만.** 러너 작업은 `run` 단계 몇 개뿐이고 외부 액션·저장소 코드를 실행하지 않습니다.
- **SSH 방화벽을 열고 닫지 않습니다.** 러너 PC의 공인 IP가 이미 22번에 허용된 관리자 IP이므로 러너에 AWS 권한이 필요 없습니다.
- **배포 키는 배포 명령만 실행합니다.** 셸·포트 포워딩·`.env`·DB 접근이 없습니다. 강제 명령은 저장소 밖 root 소유 경로라, 예전 커밋을 배포해도 강제 명령이 예전 버전으로 바뀌지 않습니다.
- **태그 배포 중 서버는 빌드하지 않습니다.** image 작업에서 시험하고 Release에 기록한 digest만 받아 실행합니다. 수동 `deploy`는 예전처럼 서버에서 빌드합니다(레지스트리 장애 때 쓰는 우회로).

## 1. 공개 저장소에서 Self-hosted Runner: 위험과 대응

GitHub는 공개 저장소에 Self-hosted Runner를 권장하지 않습니다. 포크 PR은 **자기가 고친 워크플로 파일**로 실행되므로, PR의 워크플로에 `runs-on: [self-hosted, lemit-deploy]`를 적으면 승인되는 순간 내 러너에서 돕니다. 개인 계정 저장소에서는 러너를 특정 워크플로 전용으로 묶는 기능(러너 그룹, 조직 기능)을 쓸 수 없습니다.

| 위험 | 대응 | 한계 |
|---|---|---|
| 포크 PR이 내 러너에서 코드 실행 | 외부 기여자의 모든 PR은 승인해야 실행(§1-1). 승인 전 `.github/` 변경 확인 | **승인을 잘못 누르면 실행됨** |
| 저장소 안에서 실수로 PR·push 작업이 러너를 씀 | `scripts/check_workflow_policy.py`(CI): self-hosted는 `deploy.yml`의 작업 하나만, 다른 워크플로는 GitHub-hosted 라벨 문자열만, `deploy.yml` 트리거는 `workflow_run`(Release tag·completed)·`workflow_dispatch`만(push·PR 금지), 태그 push 트리거는 `release-tag.yml`(이름·권한 읽기 전용·Secret 미사용 검사)에만, 검증 작업의 저장소·이벤트·워크플로 경로·자동 배포 스위치 조건, 태그 배포는 `deploy-image`로만 요청(`deploy-tag`·`deploy-auto` 금지), production·작업 단위 concurrency·러너 작업 액션 미사용, 파이프라인 작업 순서·상태 함수 금지·작업별 권한·Secret은 deploy만·checkout 대상 커밋 고정 | 조건의 **존재**를 보며 논리 전체를 증명하지는 않음. 포크 PR에는 효과 없음 |
| 러너 작업이 개인 PC에 접근 | 러너 전용 WSL 배포판 + sudo 없는 전용 사용자 + Windows 드라이브·프로그램 실행 끄기(§2). 배포 작업이 매번 실제 마운트(`/proc/self/mountinfo`의 drvfs, `/mnt` 밖·fstab 포함)·sudo 권한·그룹·interop(설정 + Windows 실행 시험)을 확인하고 아니면 중단 | 확인 단계는 배포 작업에만 있음. 승인된 악성 PR 작업은 이 확인을 거치지 않으므로 **격리 자체**가 방어선 |
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
   - **`/proc/sys/fs/binfmt_misc/WSLInterop`이 `enabled`로 보여도 정상일 수 있습니다.** WSL2의 배포판들은 한 VM의 커널을 함께 쓰고, 이 등록(`.exe` 파일을 WSL의 `/init`으로 넘기는 규칙)도 배포판끼리 공유합니다. 개인 배포판이 interop을 쓰면 러너 배포판에서도 `enabled`로 보입니다. 실제로 Windows 프로그램이 실행되는지는 Windows 쪽이 배포판별 설정으로 정하므로, 등록 상태가 아니라 **실행 시험**(§4)으로 확인합니다. 배포 작업도 매번 같은 시험을 합니다(약 10초).
4. **라벨 `lemit-deploy`**: 등록할 때 `./config.sh --url https://github.com/yeeun5364/lemit --token <등록 토큰> --labels lemit-deploy`, 또는 Settings → Actions → Runners에서 라벨 추가.
5. **서비스 실행:** 러너 폴더에서 `sudo ./svc.sh install ghrunner && sudo ./svc.sh start`
   - **WSL은 systemd 서비스만으로 계속 켜져 있지 않습니다.** Windows를 재부팅하면 배포판이 시작되어야 러너가 온라인이 되고, 배포판 안에 실행 중인 세션이 없으면 WSL이 배포판을 멈출 수 있습니다. 터미널을 한 번 열었다 닫는 것만으로는 부족할 수 있습니다.
   - **합격 기준:** Windows 재부팅 → 로그인 → **WSL 터미널을 모두 닫은 상태로 10분 이상** 기다린 뒤에도 Settings → Actions → Runners에서 러너가 Idle(온라인)이고, Deploy `check`를 받아 실행합니다.
   - 유지 방법 후보(**아직 시험하지 않음**): Windows 작업 스케줄러에 "로그온할 때" 작업으로 `wsl.exe -d lemit-runner --exec sleep infinity`를 숨김 실행해 배포판을 켜 둡니다. 위 합격 기준으로 확인한 방법만 씁니다.
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
   - 설치 스크립트는 `/usr/local/bin`이 root 소유이고 다른 사용자가 쓸 수 없는지 확인하고, 내용이 같아도 항상 `root:root 755`로 다시 맞춥니다.
   - 설치 스크립트는 작업 트리의 `deploy.sh`·`rollback.sh`가 잠금 넘겨받기를 지원하는지 출력합니다. "이전 버전" 주의가 나오면 위의 `git checkout --detach origin/main`을 했는지 확인하고 다시 설치합니다. 예전 버전이어도 강제 명령은 호환 모드(잠금을 놓고 실행)로 동작합니다.
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
| `id` | uid가 0이 아니고 groups에 sudo·wheel·admin·docker·lxd 없음 |
| `sudo -n -l` | 실패(비밀번호 없이 쓸 수 있는 sudo 권한이 하나도 없음) |
| 관리자 사용자로 `sudo -l -U ghrunner` | "not allowed to run sudo" (sudo 규칙 없음) |
| `grep -E 'aname=drvfs\| - drvfs ' /proc/self/mountinfo` | 아무것도 안 나옴 (Windows 드라이브 마운트 없음. `/mnt` 밖·`/etc/fstab`·Docker Desktop 연동 마운트 포함) |
| `powershell.exe` 또는 `cmd.exe` | 실행 안 됨(명령 없음) |
| `cat /etc/wsl.conf` | `[interop]` 아래 `enabled=false` (§2-3) |
| Windows 실행 시험: `p=$(mktemp --suffix=.exe); printf 'MZ' > $p; chmod 700 $p; $p < /dev/null; echo "종료 $?"; rm -f $p` | 약 10초 뒤 `UtilAcceptVsock:…: accept4 failed 110`, `종료 1` (Windows가 요청을 받지 않음). 바로 `Invalid argument`가 나오거나 `종료 0`이면 interop이 켜진 것 |
| `cat /proc/sys/fs/binfmt_misc/WSLInterop` | 참고용. 개인 배포판이 interop을 쓰면 `enabled`로 보이는 것이 정상(§2-3). 판단은 위 실행 시험으로 |
| 개발 배포판에서 `wsl -l -v` | 러너가 개발 배포판이 아닌 `lemit-runner`에 있음 |

> 2026-11-20 실측(WSL 3.0.5, 커널 6.18): 러너 배포판(interop 끔)은 `WSLInterop`이 `enabled`였지만 실제 `whoami.exe` 실행이 위 오류로 막혔고, `WSL_INTEROP=/run/WSL/1_interop`처럼 소켓을 직접 지정해도 막혔습니다. 같은 파일을 개인 배포판(interop 켬)에서 실행하면 바로 실행됐습니다.

**SSH 최소 권한** (배포 키로, 러너 배포판 또는 PC에서. `KEY=lemit-github-deploy 키 경로`)

| 명령 | 기대 결과 |
|---|---|
| `ssh -i $KEY ec2-user@서버 status` | 상태 출력, 끝에 "강제 명령: 저장소 버전과 같음" |
| `ssh -i $KEY ec2-user@서버 ls` | "허용하지 않는 요청" (종료 2) |
| `ssh -i $KEY ec2-user@서버 'deploy 0123456789abcdef0123456789abcdef01234567'` | "main에 포함되지 않은 커밋" |
| `ssh -i $KEY ec2-user@서버 'deploy-tag v9.9.9 0123456789abcdef0123456789abcdef01234567'` | "origin에 태그 v9.9.9 가 없습니다" (강제 명령이 이 기능 이전 버전이면 "허용하지 않는 요청") |
| `ssh -i $KEY ec2-user@서버 'deploy-image v9.9.9 0123456789abcdef0123456789abcdef01234567 sha256:'$(printf '0%.0s' {1..64})` | "origin에 태그 v9.9.9 가 없습니다" (태그 파이프라인 강제 명령. 이전 버전이면 "허용하지 않는 요청") |
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
Settings → Secrets and variables → Actions → **Variables** → `CD_AUTO_DEPLOY` = `true`. **버전 태그 push 자동 배포**를 켭니다(main push는 이 값과 관계없이 배포하지 않음). 값이 없거나 `true`가 아니면 태그를 올려도 Release tag는 확인만 하고(요약에 "자동 배포 꺼짐") Deploy는 시작되지 않습니다. 수동 버튼은 이 값과 관계없이 동작합니다.
- 끄려면 값을 `false`로 바꾸거나 지웁니다. 이미 시작된 Deploy 실행에는 영향이 없습니다(필요하면 Actions에서 취소).
- Environment `production`의 **Deployment branches and tags는 `main`만**으로 그대로 둡니다. Deploy는 `workflow_run`이라 main에서 실행되므로 태그 규칙을 따로 추가하지 않아도 됩니다.

### 5-4. 컨테이너 레지스트리 GHCR (태그 파이프라인, 직접)
이미지는 `ghcr.io/yeeun5364/lemit-web`에 게시합니다. 이름은 워크플로(`deploy.yml`의 `IMAGE_REPO`)와 서버 강제 명령(`ci_deploy.sh`의 `IMAGE_REPO`)에 고정돼 있어 요청으로 바꿀 수 없습니다.

| 권한 | 누가 | 범위 | 설정 |
|---|---|---|---|
| 게시(push) | `4. image` 작업의 `GITHUB_TOKEN` | 이 작업에서만 `packages: write`. 빌드·컨테이너 시험이 끝난 **뒤** 로그인하고 끝나면 로그아웃 | 없음 (워크플로에 있음). 새 PAT·Secret 불필요 |
| 받기(pull) | Lightsail 서버 | 읽기만 | **패키지를 Public으로** (아래). 서버에 레지스트리 비밀값을 두지 않음 |
| Release 기록 | `5. release` 작업의 `GITHUB_TOKEN` | 이 작업에서만 `contents: write`, 저장소 코드를 받지 않음 | 없음 |

1. **첫 태그 실행의 `4. image`가 끝난 뒤**(패키지가 처음 만들어진 뒤) GitHub → 프로필 → **Packages → lemit-web → Package settings**
   - **Danger Zone → Change visibility → Public.** 이미지에는 공개 저장소의 코드와 모델 파일만 들어 있습니다(`.env`·`.git`은 `.dockerignore`로 제외). `4. image` 요약의 "인증 없이 받기"가 `no`면 아직 비공개입니다(경고만 하고 계속 진행, 서버가 받지 못하면 `FAILED_PULL`로 멈춤).
   - **Manage Actions access**에 저장소 `yeeun5364/lemit`이 **Write**로 있는지 확인합니다(처음 게시한 저장소는 보통 자동으로 연결됨). 같은 이름의 패키지가 이미 다른 곳에서 만들어져 있으면 게시가 `permission_denied`로 실패하므로 여기서 연결합니다.
2. `6. deploy`의 production 승인은 공개로 바꾼 **뒤에** 누릅니다(첫 실행만).
3. 비공개로 두고 싶다면(권장하지 않음): 서버에 `read:packages`만 있는 토큰으로 `docker login ghcr.io`를 한 번 해야 하고, 그 토큰이 서버 `~/.docker/config.json`에 남습니다. 이 PR은 이 방식을 쓰지 않습니다.
- 게시 태그는 `<버전 태그>-run<실행 ID>.<시도>`(예: `v0.1.2-run123456789.1`)라 이전 실행의 이미지를 덮어쓰지 않습니다. 배포는 태그가 아니라 **digest**로만 합니다. 오래된 이미지는 Packages 화면에서 직접 지웁니다(롤백에 쓸 수 있는 버전은 남겨 둠).

### 5-5. 서버 준비 (태그 파이프라인, 직접, 병합 후)
1. **강제 명령 다시 설치**(§3-2 1번과 같음): 이 PR의 `ci_deploy.sh`가 `deploy-image` 요청을 처리합니다. 다시 설치하기 전에는 서버가 "허용하지 않는 요청입니다: deploy-image"로 거부합니다(배포 안 됨, 안전한 쪽 실패). 요청 길이 상한은 허용 형식 중 가장 긴 요청(`deploy-image v999999.999999.999999 <SHA 40> sha256:<64>`, 147자)에 맞춰져 있습니다(PR #74 리뷰). 태그 형식이나 요청 형식을 바꾸면 이 상한과 `test_ci_deploy.sh`의 경계값 시험도 함께 바꿉니다.
2. 서버에 `curl`·`python3`가 있는지(Amazon Linux 2023 기본 포함): 강제 명령이 GitHub API로 Release 기록을 확인합니다. `check` 결과는 바뀌지 않으므로 `ssh ec2-user@서버 'command -v curl python3'`로 확인합니다.
3. 서버에서 `https://ghcr.io`·`https://api.github.com`으로 나가는 연결이 되는지(Lightsail 기본 허용): `curl -fsS -o /dev/null https://ghcr.io/v2/ ; echo $?` (401이 나오면 정상 연결, 종료 22)
- 서버 작업 트리는 `deploy-image` 때 대상 커밋으로 바뀝니다(`deploy.sh`도 원래 그 커밋으로 바꿈). 서버 작업 폴더에 커밋하지 않은 변경이 있으면 거부합니다.

## 6. 첫 시험과 버전 태그 배포 (직접)

### 6-1. 첫 시험 (순서대로)

1. **check:** Actions → Deploy → Run workflow(브랜치 main) → `check`
   - plan 성공 → deploy 작업이 `lemit-runner`의 러너에서 시작 → "러너 격리 확인" 통과 → 서버 `status`
2. **deploy:** `deploy`, sha 비움 → plan "CI 실행 …: 3개 작업 모두 성공" → 서버 `deploy.sh` → `배포 완료`
   - 브라우저로 `https://lemit.eta.pyhub.kr`에서 분류 동선을 확인합니다. (IP `15.135.58.179`는 SSH 주소로만 씁니다. IP로 HTTPS 접속하면 인증서가 없어 끊깁니다)
3. **같은 버전 다시 deploy:** `OK`로 끝나는지
4. **rollback:** `check`의 이미지 목록에서 이전 태그로 `rollback` → 다음 `check`에 "자동 배포: 보류 중" 표시 → 수동 `deploy`가 성공하면 보류가 풀리는지 (실패하면 "보류를 유지합니다"로 끝나고 `check`에 계속 보류 중)
5. **러너가 꺼져 있을 때:** `sudo ./svc.sh stop` 후 `check` → "Waiting for a runner"로 대기 → 서비스 시작 후 이어서 실행되는지
6. **Windows 재부팅 후** §2-5의 합격 기준(터미널을 모두 닫고 10분 뒤에도 온라인, `check` 실행)을 통과하는지
7. 모두 성공하면 §5-3 자동 배포를 켜고, **§6-2 순서로 새 버전 태그를 하나 올려** 태그 배포를 시험합니다. 이어서 롤백 → 새 태그 → "건너뜀: 자동 배포 보류 중" → 수동 `deploy` 성공 → 보류 해제도 한 번 확인합니다.
8. 태그 파이프라인의 강제 명령(`deploy-image`)을 서버에 설치하기 전에 태그를 올리면 서버가 "허용하지 않는 요청입니다: deploy-image"로 거부합니다(배포 안 됨). §5-5로 다시 설치합니다.

| 증상 | 원인 후보 |
|---|---|
| "러너가 격리되지 않았습니다: …" | §2의 해당 항목. 고친 뒤 §4를 다시 확인 |
| "… `[interop] enabled=false`가 없음" | 러너 배포판 `/etc/wsl.conf` (§2-3) |
| "Windows 프로그램 실행(interop)이 막혔는지 확인하지 못함(종료 코드 …)" | `Invalid argument`·종료 0: interop이 켜져 있음 → wsl.conf 확인 후 `wsl --terminate`로 다시 시작. 종료 124: 30초 안에 끝나지 않음. 그 밖의 문구: WSL 업데이트로 오류 문구가 바뀌었을 수 있음 → §4 실행 시험으로 직접 확인한 뒤 deploy.yml의 판정 문구를 PR로 고침(확인 전에는 배포하지 않음) |
| "production Environment 설정이 빠졌습니다" | §5-2 이름 오타, Environment가 아닌 곳에 등록 |
| deploy 작업이 계속 "Waiting for a runner" | 러너 오프라인(배포판 미시작 포함), 라벨 `lemit-deploy` 누락 |
| "성공한 CI 실행을 찾지 못했습니다" | 그 커밋의 main push CI가 없거나 실패 |
| "SSH 연결 실패" | 러너 공인 IP(§2-7), authorized_keys(§3-2), Secret 내용 |
| `Host key verification failed` | 서버 호스트 키 변경 또는 known_hosts 오류 → **바로 맞추지 말고 원인부터 확인** |
| "main에 포함되지 않은 커밋입니다" | main 밖의 커밋 요청. 서버 `git fetch`가 안 됐으면 "git fetch 실패" |
| "강제 명령은 저장소 밖의 고정 경로에 설치해야 합니다" | authorized_keys가 예전 경로를 가리킴 → §3-2 |
| "다른 배포·롤백이 진행 중입니다. 끝날 때까지 기다립니다" | 다른 배포·롤백이 서버 잠금을 잡고 있음. 끝나면 이어서 실행 (정상) |
| "…초 안에 잠금을 얻지 못했습니다" | 20분 넘게 잠금이 풀리지 않음 → `check`와 관리자 SSH로 진행 중인 배포 확인 |
| 자동 배포가 "자동 배포 보류 중이라 배포하지 않습니다"로 성공 종료 | 그 전에 강제 명령으로 rollback을 했거나 rollback이 대기 중임 (정상). 원인을 확인한 뒤 수동 `deploy`(sha 비움)로 다시 배포해 **성공하면** 확정 보류가 풀림 (대기 중인 롤백이 있으면 그 롤백이 끝난 뒤 다시 보류됨) |
| 수동 deploy가 "수동 배포 실패(종료 …): 자동 배포 보류를 유지합니다" | 배포가 실패해 보류를 그대로 둠 (정상). 원인을 고친 뒤 다시 수동 `deploy` |
| "… 잠금 넘겨받기 이전 버전이라 잠금을 놓고 실행합니다 (호환 모드)" | 작업 트리가 이 기능 이전 커밋. 동작에는 문제없음. 다음 배포부터 새 스크립트가 쓰임 |
| 태그 배포가 "건너뜀: 자동 배포 보류 중이라 태그 … 를 배포하지 않습니다"로 성공 종료 | 롤백 뒤 보류 중 (정상). 원인을 확인한 뒤 수동 `deploy`로 배포해 성공하면 보류가 풀리고, 그다음 태그부터 자동 배포 |
| "건너뜀: 태그 … 의 커밋(…)이 이미 배포돼 있습니다" | 같은 커밋을 다시 배포하려 함 (중복 방지, 정상) |
| "허용하지 않는 요청입니다: deploy-tag" 또는 "…: deploy-image" | 서버 강제 명령이 이 기능 이전 버전 → §5-5로 다시 설치 |
| "GitHub Release … 를 찾지 못했습니다" / "Release에 기록된 이미지 digest가 요청과 다릅니다" | `5. release`가 끝나지 않았거나 Release를 손으로 고침. 요청한 digest가 Release 기록과 다르면 서버는 배포하지 않음 (정상 차단) |
| `FAILED_PULL` "이미지를 받지 못했습니다" | GHCR 패키지가 비공개(§5-4), 서버 외부 연결(§5-5) |
| `FAILED_IMAGE_REVISION` "이미지 라벨의 커밋 … 이 배포할 커밋 … 과 다릅니다" | 다른 커밋의 이미지 digest를 요청함 → 배포하지 않음. Release 기록과 이미지가 맞는지 확인 |
| "커밋 … 의 scripts/deploy.sh는 이미지 배포를 지원하지 않습니다" | 태그 파이프라인 이전 커밋에 태그를 담. 이 기능이 병합된 뒤의 커밋에 새 태그 |
| "건너뜀: 태그 … 의 이미지(…)가 이미 배포돼 있습니다" | 같은 커밋·같은 digest를 다시 배포하려 함 (중복 방지, 정상) |
| "태그 … 가 커밋 … 를 가리키지 않습니다" | 태그를 지웠거나 다른 커밋으로 옮김. 태그는 옮기지 말고 새 버전 태그를 올림 |

### 6-2. 버전 태그로 배포하기 (§5-3 자동 배포를 켠 뒤)

**병합 후 처음 한 번 (순서대로)**
1. 이 PR을 병합하고 Actions → **CI**에서 병합 커밋의 main push CI가 성공했는지 확인합니다.
2. 서버에서 강제 명령을 다시 설치합니다(§5-5 1번). Deploy `check` 결과의 "강제 명령: 저장소 버전과 같음"으로 확인합니다.
3. production Environment에 **Required reviewers**가 켜져 있는지 확인합니다(§5-1).
4. 아래 "사용 순서"대로 새 버전 태그(예: `v0.1.2`)를 올립니다. `4. image`가 끝나면 GHCR 패키지를 **Public**으로 바꿉니다(§5-4, 첫 실행만).
5. `6. deploy`의 승인 대기 화면에서 1~5 요약(모델 점수·parity 표·digest)을 확인하고 승인합니다.
6. 배포가 끝나면 §6-3 순서로 모델·이미지가 같은지 확인하고, 브라우저로 분류 동선을 확인합니다.

**사용 순서**
1. PR을 main에 병합하고, Actions → **CI**에서 그 **병합 커밋의 main push CI가 성공**했는지 확인합니다.
2. 그 커밋에 다음 버전 태그를 달아 올립니다. 태그는 `vMAJOR.MINOR.PATCH`만 배포합니다. `v0.1`, `v1.0.0-rc1`, `v01.0.0` 같은 태그는 Release tag가 실패로 끝내고 배포하지 않습니다. **태그 파이프라인이 병합된 뒤의 커밋**에 답니다(이전 커밋에는 `ml/parity.py`가 없어 `3. parity`에서 멈추고, 서버도 이미지 배포를 거부함).
   ```bash
   git fetch origin
   git tag -a v0.1.2 -m "v0.1.2" origin/main     # 가리킬 커밋을 명시 (예: CI가 성공한 병합 커밋)
   git push origin v0.1.2
   ```
3. Actions → **Release tag**가 태그·main 포함을 확인하고 CI 결과를 기다립니다. 성공하면 Actions → **Deploy**에 새 실행이 생기고 `0. plan` → … → `5. release`가 차례로 실행된 뒤 `6. deploy`가 승인을 기다립니다.
4. 요약을 보고 `6. deploy`를 승인합니다. 끝나면 요약의 "서버 실행 결과", "이미지 ID", "공개 경로 점검"을 확인하고 브라우저로 사이트를 확인합니다. 서버 기록은 `check`의 "최근 배포 기록"(`deploy/state/history.log`)과 "현재 레지스트리 이미지"(`deploy/state/current_image`)에 남습니다.
- **태그는 옮기거나 지우지 않습니다.** 고칠 것이 있으면 다음 버전을 올립니다. 이미 올린 태그를 옮기면 plan·러너·서버가 "가리키지 않습니다"로 멈춥니다.
- 더 낮은 버전 태그는 배포하지 않습니다(예: `v0.2.0`이 있는데 `v0.1.5`를 올림 → 건너뜀). 예전 버전으로 되돌릴 때는 `rollback`(이미지, §6-4) 또는 수동 `deploy`(커밋)를 씁니다.

**같은 버전을 다시 실행할 때 (재시도)**
- `6. deploy`만 실패했다면(러너 꺼짐·SSH 실패·승인 거절 등) 그 실행에서 **Re-run failed jobs**를 씁니다. 1~5의 결과(같은 digest)를 그대로 쓰므로 다시 빌드하지 않습니다.
- 실행 전체를 다시 돌리면(Re-run all jobs) 이미지를 다시 빌드해 digest가 달라질 수 있습니다. 이때 `5. release`는 기존 Release를 **덮어쓰지 않고** "기존 기록과 다름" 표(항목·기존·이번)를 요약에 남긴 채 실패하며 배포하지 않습니다. 기록된 이미지를 배포하려면 원래 실행에서 Re-run failed jobs를, 새 변경을 배포하려면 새 버전 태그를 씁니다.
- Release가 이미 같은 커밋·같은 digest로 기록돼 있으면 새로 만들지 않고 그대로 씁니다(중복 Release 없음). 서버도 같은 커밋·digest가 이미 실행 중이면 건너뜁니다.

**CI가 아직 진행 중인 태그**
- 태그를 CI가 끝나기 전에 올려도 됩니다. Release tag가 **30초마다 최대 30분** 그 커밋의 main push CI를 기다립니다. CI가 성공하면 이어서 파이프라인이 시작됩니다.
- CI가 **실패·취소**로 끝나면 Release tag가 실패하고 배포하지 않습니다. 고친 커밋을 병합한 뒤 **새 버전 태그**를 올립니다. (CI가 취소된 경우, 예: main에 바로 다음 커밋이 병합되어 그 커밋의 CI가 취소됨 → CI를 그 커밋에서 다시 실행해 성공한 뒤 Release tag를 다시 실행하거나, 최신 커밋에 새 태그를 답니다)
- 30분 안에 끝나지 않으면 Release tag가 실패합니다. CI가 끝난 뒤 Actions → Release tag → 그 실행 → **Re-run jobs**로 다시 실행하면 이어서 파이프라인이 시작됩니다.
- 그 커밋에 main push CI 실행이 아예 없으면(5분 동안 보이지 않음) 실패합니다. 여러 커밋을 한 번에 push하면 CI는 마지막 커밋에만 돌므로, **CI가 돈 커밋(보통 병합 커밋)에 태그를 답니다.**
- plan은 기다리지 않고 "성공한 CI 실행"이 있는지만 다시 확인합니다(Release tag가 성공한 뒤에만 시작되므로). `1. test`는 main CI와 별도로 태그 커밋에서 테스트를 다시 실행합니다.

**실패했을 때 확인할 곳**

| 어디서 멈췄나 | 볼 곳 | 흔한 원인 |
|---|---|---|
| Release tag 실패 | Actions → Release tag → 실행 → 작업 "태그 확인·CI 대기" 로그와 요약 | 태그 형식, main 밖 커밋, CI 실패·취소·없음·30분 초과 |
| Release tag는 성공했는데 Deploy 작업이 모두 건너뜀 | Release tag 요약의 "자동 배포 꺼짐" | `CD_AUTO_DEPLOY`가 `true`가 아님(§5-3), Deploy 작업 조건(저장소·이벤트) |
| `0. plan` 실패 또는 건너뜀 | Actions → Deploy → `0. plan` 로그(알림 `::notice`/`::error`)와 요약 | 태그 이동, 더 높은 버전 태그 있음(건너뜀), CI 작업 실패 |
| `1. test` 실패 | `1. test` 로그(ruff·워크플로 규칙·check·마이그레이션·ml.verify·Django 테스트 중 실패한 단계) | 태그 커밋의 코드 문제 → 고쳐서 새 버전 |
| `2. train` 실패 | "모델 학습·검증" 단계 로그 | 성능 기준 미달("성능 기준 미달 … 저장하지 않습니다"), 데이터 오류, `ml.verify` 재현 실패 |
| `3. parity` 실패 | 요약의 비교 표(실패 항목), "받은 모델이 train 결과와 같은지" 단계 | 라이브러리·기반 이미지 차이로 예측이 다름, 아티팩트 해시 불일치 (docs/ML.md §6) |
| `4. image` 실패 | 단계별: 빌드 → 컨테이너 실행 → 스모크 → 모델 해시·버전 → 게시. 실패하면 "실패 시 컨테이너 로그" | 빌드 오류, healthz·추론 실패, 이미지 안 모델 해시 불일치, GHCR 권한(§5-4) |
| `5. release` 실패 | 요약의 "기존 기록과 다름" 표 또는 로그 | 같은 버전을 다시 빌드해 digest가 달라짐(위 재시도), 손으로 만든 같은 이름 Release |
| `6. deploy`가 시작하지 않음 | 실행 화면의 "Waiting for review" / "Waiting for a runner" | production 승인 대기(§5-1), 러너 오프라인 |
| `6. deploy` 러너 작업 실패 | 작업 로그, 단계별: 러너 격리 → 설정값 → 태그 재확인 → 서버 요청 만들기 → 서버에서 실행 | §6-1 표 참고 |
| 서버가 거부·건너뜀 | 같은 작업의 "서버에서 실행" 단계 출력(`[ci_deploy] …`)과 요약의 "건너뜀(서버)" | 보류 중, 이미 배포됨, 강제 명령 미갱신, Release 기록 불일치 |
| 배포 스크립트 실패 | "서버에서 실행" 단계의 `deploy.sh` 출력, 서버의 `deploy/state/history.log`(`check`로 확인), 관리자 SSH로 `docker compose logs web` | `FAILED_PULL`·`FAILED_IMAGE_REVISION`(§6-1 표), DEPLOY.md §4·§7 |

- 시험: 판정 로직은 `scripts/test_deploy_workflow.py`(워크플로 run 본문을 가짜 GitHub API로 실행: 정상 태그 → deploy-image, 주석 태그, main 밖 커밋, CI 진행 중→성공·실패·없음·시간 초과, 자동 배포 꺼짐, 더 높은 태그, 태그 이동, 수동 동작 유지, 서버 요청에 digest 붙이기, Release 새로 만들기·재사용·digest 다름·커밋 다름·기록 없는 Release·API 오류), 작업 구조는 `scripts/test_check_workflow_policy.py`(순서·상태 함수·권한·Secret·checkout 대상), 서버 쪽은 `scripts/test_ci_deploy.sh`(Release 기록과 digest·커밋·태그 비교, 초안·기록 없음, main 밖, 형식, 롤백 보류·대기 중 건너뜀, 이미 배포됨, 대상 커밋으로 바꾼 뒤 실제 `deploy.sh` 실행, 이미지 배포를 모르는 `deploy.sh` 거부, 요청 길이 상한 경계값 147자 통과·148자 거부)와 `scripts/test_deploy_buildx.sh`(이미지 배포는 buildx가 낮아도 이미지 받기까지 진행·빌드 안 함, 서버 빌드는 buildx가 낮으면 빌드 전에 중단)에서 확인합니다. GitHub의 `workflow_run` 연결, 아티팩트 전달, GHCR 게시, production 승인은 로컬에서 재현할 수 없어 첫 태그 시험에서 확인합니다.

### 6-3. 모델·이미지가 같은지 확인하기

**train에서 만든 모델 = image 안의 모델**
- `2. train` 요약의 "모델 파일 SHA-256"이 기준값입니다(Release 기록 `model_sha256=`도 같은 값).
- `3. parity`·`4. image`는 아티팩트를 받자마자 `sha256sum -c`로 train 작업이 출력한 해시와 비교하고, 다르면 실패합니다.
- `4. image`는 이미지 안의 `ml/artifacts/message_classifier.joblib`을 실행 중인 컨테이너에서 다시 해시해 같은지 보고, `/healthz/`의 모델 버전이 train의 모델 버전(예: `v2+v0.1.2`)인지 확인합니다. 이미지 라벨 `io.lemit.model.sha256`에도 같은 값을 남깁니다.
- 서버에서 직접 보려면(관리자 SSH): `docker compose exec web sha256sum ml/artifacts/message_classifier.joblib` 결과를 Release의 `model_sha256`과 비교합니다.

**image에서 시험한 이미지 = Lightsail이 실행한 이미지**
1. `4. image`는 시험한 로컬 이미지 그대로 게시하고, 로컬 이미지를 지운 뒤 **digest로 다시 받아** 이미지 ID가 시험한 이미지와 같은지 확인합니다(다르면 실패). 이 digest가 요약·Release(`image=…@sha256:…`)에 남습니다.
2. 서버 강제 명령은 요청의 digest가 GitHub Release 기록과 같을 때만 진행하고, `deploy.sh`는 그 digest로만 받습니다(빌드 없음). 받은 이미지의 라벨 커밋이 배포할 커밋과 다르면 `FAILED_IMAGE_REVISION`으로 멈춥니다.
3. `deploy.sh`는 교체 뒤 **실행 중인 web 컨테이너의 이미지 ID**가 받은 이미지의 ID와 같은지 확인하고, 다르면 실패로 보고 직전 이미지로 되돌립니다. 성공하면 `deploy/state/current_image`에 `ghcr.io/yeeun5364/lemit-web@sha256:…`를 남깁니다.
4. `6. deploy` 요약의 "서버 실행 결과"(`image=…@sha256:… image_id=… running_image_id=…`)와 "이미지 ID" 줄에서 image 작업의 이미지 ID와 서버 실행 컨테이너의 이미지 ID를 비교합니다. Docker 저장 방식(containerd 이미지 저장소 여부)이 GitHub 러너와 서버에서 다르면 ID 표기가 달라질 수 있어 이 줄은 참고용이고, 판정은 1~3(digest로 받기·라벨·서버의 실행 ID 확인)으로 합니다.
5. 서버에서 직접: Deploy `check`의 "현재 레지스트리 이미지" 줄, 또는 관리자 SSH로 `docker image inspect lemit-web:<커밋 12자리> --format '{{json .RepoDigests}}'`가 Release의 digest를 포함하는지 봅니다.

### 6-4. 실패했을 때 롤백

- **서버 배포 중 실패:** `deploy.sh`가 스모크·컨테이너 상태·실행 이미지 확인에 실패하면 직전 성공 이미지로 web을 자동으로 되돌리고 `FAILED_ROLLED_BACK_TO_<태그>`를 남깁니다. 공개 경로 확인만 실패하면 되돌리지 않고 `FAILED_PUBLIC_CHECK`(current_tag 그대로, DEPLOY.md §3).
- **배포는 성공했지만 되돌리고 싶을 때:** Actions → Deploy → Run workflow → `rollback`, `rollback_tag`에 `check`의 이미지 목록·배포 기록에 있는 12자리 태그. 레지스트리에서 받은 이미지도 서버에 `lemit-web:<커밋 12자리>`로 태그를 붙여 두므로, **전환 전에 서버에서 빌드한 버전과 같은 방법으로** 되돌립니다(로컬 시험: 서버 빌드 버전 ↔ 레지스트리 버전 양쪽으로 롤백 확인). 서버 빌드 이미지로 돌아가면 `current_image`는 지워지고, 레지스트리 이미지로 돌아가면 그 digest가 남습니다.
- 롤백 뒤에는 태그 자동 배포가 **보류**됩니다(§0). 원인을 고친 뒤 수동 `deploy`가 성공하면 보류가 풀리고, 그다음 버전 태그부터 다시 자동 배포됩니다.
- DB 스키마는 되돌리지 않습니다(DEPLOY.md §5). GHCR의 이미지는 지우지 않습니다(롤백 대상).

## 7. 러너 PC가 꺼져 있을 때

| 상황 | 동작 |
|---|---|
| PC가 꺼진 동안 버전 태그를 올림 | Release tag와 plan은 GitHub 컴퓨터에서 끝나고, deploy 작업은 맞는 러너를 기다립니다. GitHub 문서 기준 Self-hosted 러너를 기다리는 작업은 **24시간** 뒤 실패합니다. 실패 알림은 GitHub 알림 설정에 따르며 이 워크플로가 보장하지 않습니다. |
| 그사이 배포가 여러 번 | deploy 작업의 동시 실행 그룹(`deploy-production`)에서 진행 중인 것은 취소하지 않고, **기다리는 deploy는 나중에 들어온 하나만** 남습니다(검증을 통과한 작업만 그룹에 들어감). rollback·check는 각각 별도 그룹이라 새 deploy가 대기 중인 rollback을 취소하지 않습니다(§0 직렬화와 롤백 보존). |
| PC를 켰는데 남은 태그 배포가 오래된 버전 | plan(요청 시점)과 러너(실행 직전)가 그 태그가 그대로이고 **가장 높은 버전 태그**인지 확인해, 더 높은 태그가 있으면 **건너뜁니다**(그 태그의 배포가 따로 실행됨). 서버는 잠금을 잡은 뒤 태그·main 포함을 다시 확인하고, 이미 배포된 커밋이면 건너뜁니다. |
| 24시간이 지나 실패 | 서버는 바뀌지 않았습니다. PC를 켠 뒤 수동 `deploy`(sha 비움). |
| 배포 도중 PC가 꺼지거나 네트워크가 끊김 | SSH가 끊기면 서버의 `deploy.sh`가 중간에 멈출 수 있습니다. **이 경우의 동작은 시험하지 않았습니다.** 관리자 SSH나 `check`로 상태를 보고, 이상하면 `rollback` 또는 서버에서 `deploy.sh`를 다시 실행합니다(DEPLOY.md). |
| 예상하지 못한 시점의 배포가 싫다면 | §5-1 Required reviewers |

## 8. 남는 위험 (인정하고 운영)

- **외부 PR 승인 실수:** 승인된 포크 PR의 작업은 내 러너에서 실행됩니다. 배포 작업의 격리 확인 단계를 거치지 않으므로, 피해 범위는 §2의 격리 상태에 달려 있습니다.
- **계정 탈취:** 저장소 쓰기 권한이 있으면 워크플로를 바꿔 러너를 쓸 수 있습니다(2단계 인증).
- **러너가 작업마다 새로 만들어지지 않습니다(ephemeral 아님).** 악성 작업이 남긴 프로그램이 다음 배포 때 키를 읽을 수 있습니다. 의심되면 러너 배포판을 지우고 다시 만든 뒤 배포 키를 교체합니다(§3-2).
- **최신 버전 확인과 서버 배포 사이의 짧은 틈:** 러너가 "가장 높은 버전 태그"를 확인한 직후 더 높은 태그가 올라오면, 낮은 버전이 먼저 배포되고 이어서 높은 버전 배포가 실행됩니다. 서버는 "가장 높은 버전"을 따로 확인하지 않습니다.
- **태그를 올릴 수 있는 사람:** 저장소 쓰기 권한이 있으면 누구나 `v*` 태그를 올릴 수 있습니다. 그래도 배포되는 것은 **main에 포함되고 main push CI가 성공한 커밋**뿐입니다(plan·서버 확인). 태그 생성을 제한하려면 Settings → Rules → Rulesets에서 `v*` 태그 생성·삭제·이동을 본인만 허용하는 규칙을 둘 수 있습니다(이 PR에서는 설정하지 않음).
- **Release tag는 태그 커밋의 파일로 실행됩니다:** main 밖 커밋에 태그를 달면 그 커밋의 `release-tag.yml`이 돌 수 있지만, GitHub-hosted·읽기 권한·Secret 없음이라 러너와 배포 키에 닿지 않습니다. 그 결과가 성공이어도 Deploy가 main 포함·CI를 다시 확인하므로 배포되지 않습니다.
- **강제 명령 갱신 전에는 태그 배포가 동작하지 않습니다:** `deploy-image` 요청은 예전 강제 명령에서 "허용하지 않는 요청"으로 거부됩니다(배포 안 됨, 안전한 쪽 실패). 태그 파이프라인을 병합한 뒤 §5-5로 강제 명령을 다시 설치해야 태그 배포가 동작합니다. 예전 `deploy-tag`·`deploy-auto`는 강제 명령에 남아 있지만 워크플로가 더는 요청하지 않습니다(정책 검사가 막음).
- **호환 모드의 틈:** 작업 트리의 스크립트가 잠금 넘겨받기 이전 버전이면 강제 명령이 잠금을 놓고 실행하므로, 그 틈에 다른 요청이 잠금을 잡으면 예전 스크립트가 "다른 배포·롤백이 진행 중"(75)으로 실패합니다(겹쳐 실행되지는 않음, 롤백 보류도 사라지지 않음).
- **대기 표시의 PID 확인:** 롤백 대기 표시는 PID로 살아 있는지 판단합니다. 비정상 종료 후 같은 PID가 다른 프로세스에 재사용되면 그 프로세스가 끝날 때까지 자동 배포가 보류될 수 있습니다(안전한 쪽). 수동 deploy는 대기 표시를 지우지 않으므로, `check`에 "대기 중인 롤백 있음"이 계속 보이면 진행 중인 롤백이 없는지 확인한 뒤 관리자가 서버에서 `deploy/state/rollback_pending/`의 파일을 지웁니다.
- **격리 확인의 범위:** `/usr/lib/wsl/drivers`(WSL2가 항상 붙이는 읽기 전용 Windows 드라이버 저장소)는 허용합니다. interop은 공유되는 binfmt 등록 대신 설정 파일과 실제 실행 시험으로 판단하며, 실행 시험은 WSL이 내는 오류 문구에 기대므로 WSL 버전이 바뀌면 다시 확인해야 합니다(문구가 다르면 배포를 멈추는 쪽으로 실패). 비밀번호가 필요한 sudo 규칙은 확인 단계가 잡지 못하므로, 전용 사용자는 비밀번호 없이(`--disabled-password`) 만들고 §4에서 `sudo -l -U ghrunner`로 확인합니다.
- **태그 커밋의 코드는 GitHub 컴퓨터에서 실행됩니다:** `1. test`~`4. image`는 main에 포함되고 CI가 성공한 태그 커밋의 코드(테스트·학습·Dockerfile)를 실행합니다. 이 작업들은 GitHub-hosted이고 배포 키를 받을 수 없지만, `4. image`는 게시하는 동안 `packages: write` 토큰을 씁니다(빌드·시험 뒤에 로그인). main에 들어간 코드가 악의적이면 GHCR 패키지를 바꿀 수 있으나, 서버는 Release에 기록된 digest만 받으므로 바뀐 이미지가 그대로 배포되지는 않습니다. main 보호 규칙과 PR 검토에 의존합니다.
- **GHCR 패키지는 공개입니다:** 이미지에는 공개 저장소의 코드와 모델만 들어 있고 `.env`·`.git`은 빠집니다(`.dockerignore`). 비밀값을 이미지에 넣지 않습니다(빌드 중 쓰는 값은 빌드용 가짜 값).
- **Release 기록을 바꿀 수 있는 사람:** 저장소 쓰기 권한이 있으면 Release 본문을 고칠 수 있습니다. 서버는 Release의 digest와 요청 digest가 같은지, 받은 이미지 라벨의 커밋이 배포할 커밋과 같은지를 함께 확인하지만, 쓰기 권한이 있는 사람이 다른 이미지를 만들어 Release를 고치는 것까지 막지는 못합니다(계정 보호·2단계 인증).
- **서버의 Release 확인은 인증 없는 GitHub API를 씁니다:** 시간당 60회 제한이라 배포 빈도에는 충분하지만, GitHub API 장애 때는 태그 배포가 거부됩니다(안전한 쪽 실패). 이때는 수동 `deploy`(서버 빌드)로 배포할 수 있습니다.
- **같은 버전 재빌드는 digest가 달라질 수 있습니다:** 이미지 빌드는 바이트 단위로 재현되지 않습니다. 그래서 같은 버전의 전체 재실행은 Release 단계에서 멈춥니다(§6-2 재시도). 모델 학습은 같은 커밋·같은 라이브러리에서 같은 파일을 만들었습니다(로컬 확인, train 요약의 "커밋된 모델 파일과 같은 바이트").
- **정책 검사의 범위:** 조건 문자열이 있는지 확인하며, 조건식 전체의 논리를 증명하지는 않습니다. 워크플로 변경 PR은 사람이 함께 봅니다.
- **강제 명령 갱신은 수동:** 갱신을 잊으면 서버는 예전(이미 검토한) 강제 명령을 계속 씁니다.
- 관리자 IP(집 인터넷)가 바뀌면 배포가 실패합니다(안전한 쪽 실패).
- 배포 중 약 20~30초 동안 실패한 새 버전이 응답할 수 있습니다(DEPLOY.md §3, 자동 롤백 전).

## 9. 이전 방식 기록 (#42, 사용하지 않음)

GitHub-hosted 러너가 OIDC로 AWS 역할을 받아, 배포하는 동안만 SSH 22번에 러너 IP를 추가하고 끝나면 빼는 방식이었습니다. production Environment와 AWS 설정을 하기 전에 Self-hosted 방식으로 바뀌어 실제 배포에 쓰이지 않았습니다.
- AWS에 OIDC 공급자, 역할 `lemit-github-deploy`, 서버 태그 `lemit-deploy=allowed`를 만들었다면 이제 필요 없습니다.
- `scripts/lightsail_firewall.py`(22번 규칙에서 IP 하나만 추가·제거, 테스트 포함)는 남겨 둡니다. 관리자 PC에 AWS CLI가 있으면 쓸 수 있습니다: `python3 scripts/lightsail_firewall.py close --instance lemit-server --cidr <IP>/32`
