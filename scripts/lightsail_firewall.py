"""Lightsail 방화벽 SSH(22) 규칙에 IP 하나를 잠깐 추가·제거한다 (#42, CD 배포 시에만 SSH 임시 개방).

워크플로가 이렇게 쓴다 (AWS 자격 증명은 워크플로가 OIDC로 준비):

    python3 scripts/lightsail_firewall.py open  --instance lemit-server --cidr 203.0.113.7/32
    python3 scripts/lightsail_firewall.py close --instance lemit-server --cidr 203.0.113.7/32

왜 put(전체 다시 쓰기)인가 (PR #43 리뷰 P1)
- PutInstancePublicPorts는 "요청에 없는 열린 포트는 닫는다"고 문서에 명시되어 있어 결과를 정확히 예측할 수 있다.
- OpenInstancePublicPorts/CloseInstancePublicPorts는 같은 22번 규칙이 이미 있을 때 IP를 덧붙이는지·바꾸는지,
  닫을 때 그 IP만 빼는지·22번 전체를 닫는지가 문서에 없다. 관리자 IP 규칙을 바꾸거나 닫을 위험이 있어 쓰지 않는다.

전체를 다시 쓰므로 다른 변경을 덮어쓰지 않게 다음을 지킨다
1. 읽기(S0) → 바꿀 규칙 계산 → **쓰기 직전 다시 읽기(S1)**.
   S1이 S0과 다르면(그사이 누가 바꿈) 쓰지 않고 처음부터 다시(최대 3회).
2. 쓰기 후 다시 읽어 **기대한 규칙과 정확히 같은지 검증**(반영 지연을 고려해 몇 번 재확인). 다르면 실패(종료 코드 1).
3. 이미 원하는 상태면 쓰지 않는다(쓰기 횟수 최소화, 같은 요청 반복에 안전).
4. close는 그 시점의 최신 상태에서 러너 IP만 뺀다.
   성공 판정은 "열린 TCP 22번 규칙의 cidrs에 러너 IP가 없다"를 구조적으로 확인(P2).
Lightsail API에는 조건부 쓰기(버전 비교)가 없어 다시 읽기와 쓰기 사이의 아주 짧은 틈은 남는다.
배포 중에는 콘솔에서 방화벽을 바꾸지 않는다.

공개 저장소의 Actions 로그는 누구나 보므로 IP 목록은 출력하지 않고 포트 번호만 출력한다.
테스트용: 환경변수 LIGHTSAIL_AWS_CLI로 aws 명령을 바꿀 수 있다 (예: "python fake_aws.py").
"""

import argparse
import ipaddress
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time

SSH_PORT = 22
KEEP_FIELDS = ("fromPort", "toPort", "protocol", "cidrs", "ipv6Cidrs", "cidrListAliases")
MAX_ATTEMPTS = 3
VERIFY_TRIES = 5


class FirewallError(Exception):
    """방화벽 변경을 안전하게 끝내지 못함 (동시 변경, 검증 실패 등)."""


def _wait():
    time.sleep(float(os.environ.get("LIGHTSAIL_FIREWALL_WAIT", "2")))


# ---------- 순수 함수: 규칙 계산 ----------


def _to_port_info(state):
    info = {k: state[k] for k in KEEP_FIELDS if k in state and state[k] not in (None, [])}
    info.setdefault("cidrs", [])
    return info


def _is_ssh_rule(info):
    return (
        str(info.get("protocol", "")).lower() == "tcp"
        and info.get("fromPort") == SSH_PORT
        and info.get("toPort") == SSH_PORT
    )


def _validate_cidr(cidr):
    # 추가한 문자열과 똑같은 문자열로 지워야 하므로 항상 "IP/32" 형식만 받는다
    if "/" not in cidr:
        raise ValueError(f"'IP/32' 형식으로 주세요: {cidr}")
    net = ipaddress.ip_network(cidr, strict=True)
    if net.version != 4 or net.prefixlen != 32:
        raise ValueError(f"IPv4 /32 하나만 허용합니다: {cidr}")
    return str(net)


def port_infos(state_json):
    states = state_json.get("portStates")
    if states is None:
        raise ValueError("입력에 portStates가 없습니다 (aws lightsail get-instance-port-states 결과가 아님)")
    return [_to_port_info(s) for s in states if str(s.get("state", "open")).lower() == "open"]


def open_ssh(state_json, cidr):
    cidr = _validate_cidr(cidr)
    infos = port_infos(state_json)
    ssh = [i for i in infos if _is_ssh_rule(i)]
    if ssh:
        rule = ssh[0]
        if "0.0.0.0/0" not in rule["cidrs"] and cidr not in rule["cidrs"]:
            rule["cidrs"] = rule["cidrs"] + [cidr]
    else:
        infos.append({"fromPort": SSH_PORT, "toPort": SSH_PORT, "protocol": "tcp", "cidrs": [cidr]})
    return infos


def close_ssh(state_json, cidr):
    cidr = _validate_cidr(cidr)
    result = []
    for info in port_infos(state_json):
        if _is_ssh_rule(info) and cidr in info["cidrs"]:
            info["cidrs"] = [c for c in info["cidrs"] if c != cidr]
            if not info["cidrs"] and not info.get("ipv6Cidrs") and not info.get("cidrListAliases"):
                continue  # 추가했던 IP만 남아 있던 규칙 → 22번 규칙 자체를 없앰 (원래 없던 규칙)
        result.append(info)
    return result


def ssh_allows(state_json, cidr):
    """열린 TCP 22번 규칙의 cidrs에 이 IP가 있는가 (P2: 문자열 검색이 아니라 규칙 구조로 판정)."""
    cidr = _validate_cidr(cidr)
    return any(_is_ssh_rule(i) and cidr in i["cidrs"] for i in port_infos(state_json))


def normalize(infos):
    """규칙 비교용: 순서·대소문자 차이를 없앤 형태."""
    return sorted(
        (
            str(i.get("protocol", "")).lower(),
            i.get("fromPort"),
            i.get("toPort"),
            tuple(sorted(i.get("cidrs") or [])),
            tuple(sorted(i.get("ipv6Cidrs") or [])),
            tuple(sorted(i.get("cidrListAliases") or [])),
        )
        for i in infos
    )


def changed_ports(a, b):
    """두 규칙 목록에서 달라진 포트만 (IP는 공개 로그에 남기지 않음)."""
    sa, sb = set(normalize(a)), set(normalize(b))
    return sorted({f"{r[0]}/{r[1]}-{r[2]}" for r in sa ^ sb})


# ---------- AWS 호출과 안전한 적용 ----------


def _aws(args):
    cmd = shlex.split(os.environ.get("LIGHTSAIL_AWS_CLI", "aws")) + args + ["--output", "json"]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise FirewallError(f"aws {args[0]} {args[1]} 실패: {done.stderr.strip()[:300]}")
    return json.loads(done.stdout or "{}")


def get_state(instance):
    return _aws(["lightsail", "get-instance-port-states", "--instance-name", instance])


def put_rules(instance, infos):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(infos, f)
        path = f.name
    try:
        _aws(["lightsail", "put-instance-public-ports", "--instance-name", instance, "--port-infos", f"file://{path}"])
    finally:
        os.unlink(path)


def apply(instance, cidr, action, log=print):
    compute = open_ssh if action == "open" else close_ssh
    for attempt in range(1, MAX_ATTEMPTS + 1):
        s0 = get_state(instance)
        current = port_infos(s0)
        desired = compute(s0, cidr)
        if normalize(desired) == normalize(current):
            log(f"[firewall] {action}: 이미 원하는 상태라 바꾸지 않습니다")
            return desired
        latest = port_infos(get_state(instance))  # 쓰기 직전 다시 읽기
        if normalize(latest) != normalize(current):
            log(
                f"[firewall] {action}: 읽는 사이 규칙이 바뀌었습니다 {changed_ports(current, latest)}"
                f" → 덮어쓰지 않고 다시 시도 ({attempt}/{MAX_ATTEMPTS})"
            )
            _wait()
            continue
        put_rules(instance, desired)
        after = []
        for _ in range(VERIFY_TRIES):  # 쓴 결과 검증 (반영 지연 고려)
            after = port_infos(get_state(instance))
            if normalize(after) == normalize(desired):
                log(f"[firewall] {action}: 22번 규칙 변경 완료, 다른 규칙은 그대로 (규칙 {len(desired)}개)")
                return desired
            _wait()
        raise FirewallError(
            f"{action} 후 방화벽이 기대와 다릅니다 {changed_ports(desired, after)}. "
            "배포 중 콘솔에서 규칙이 바뀌었을 수 있습니다. Lightsail 콘솔에서 규칙을 확인하세요 (docs/CD.md §7)"
        )
    raise FirewallError(f"{action}: 규칙이 계속 바뀌어 {MAX_ATTEMPTS}번 시도 후 중단했습니다 (docs/CD.md §7)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["open", "close"])
    parser.add_argument("--instance", required=True)
    parser.add_argument("--cidr", required=True, help="러너 공인 IP, 예: 203.0.113.7/32")
    args = parser.parse_args(argv)
    try:
        apply(args.instance, args.cidr, args.action)
        if args.action == "close" and ssh_allows(get_state(args.instance), args.cidr):
            raise FirewallError(
                "러너 IP가 22번 규칙에 남아 있습니다. Lightsail 콘솔에서 직접 제거하세요 (docs/CD.md §7)"
            )
        if args.action == "open" and not ssh_allows(get_state(args.instance), args.cidr):
            state = get_state(args.instance)
            if not any(_is_ssh_rule(i) and "0.0.0.0/0" in i["cidrs"] for i in port_infos(state)):
                raise FirewallError("러너 IP가 22번 규칙에 추가되지 않았습니다")
    except (FirewallError, ValueError) as e:
        print(f"::error::[firewall] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
