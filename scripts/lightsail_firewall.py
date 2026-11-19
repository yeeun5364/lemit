"""Lightsail 방화벽에서 SSH(22) 규칙에 IP 하나를 잠깐 추가·제거한다 (#42, CD 배포 시에만 SSH 임시 개방).

AWS를 직접 부르지 않고 JSON만 바꾼다. 워크플로가 다음처럼 쓴다:

    aws lightsail get-instance-port-states --instance-name lemit-server > state.json
    python scripts/lightsail_firewall.py open  --cidr 203.0.113.7/32 < state.json > ports.json
    aws lightsail put-instance-public-ports --instance-name lemit-server --port-infos file://ports.json

- 입력: get-instance-port-states 결과({"portStates": [...]}).
- 출력: put-instance-public-ports에 넣을 portInfos 목록. put은 규칙 전체를 바꾸므로
  **기존 규칙(다른 포트, 22번의 관리자 IP, IPv6, 브라우저 SSH 별칭 등)을 모두 그대로 옮기고** 22번에 IP만 더하거나 뺀다.
- close는 배포가 끝날 때 **그 시점의 최신 상태**를 다시 읽어 그 IP만 뺀다.
  원래 22번 규칙이 없었으면(추가한 IP만 남으면) 22번 규칙 자체를 없앤다.
"""

import argparse
import ipaddress
import json
import sys

SSH_PORT = 22
KEEP_FIELDS = ("fromPort", "toPort", "protocol", "cidrs", "ipv6Cidrs", "cidrListAliases")


def _to_port_info(state):
    info = {k: state[k] for k in KEEP_FIELDS if k in state and state[k] not in (None, [])}
    info.setdefault("cidrs", [])
    return info


def _is_ssh_rule(info):
    return (
        info.get("protocol", "").lower() == "tcp"
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["open", "close"])
    parser.add_argument("--cidr", required=True, help="러너 공인 IP, 예: 203.0.113.7/32")
    args = parser.parse_args(argv)
    state = json.load(sys.stdin)
    infos = open_ssh(state, args.cidr) if args.action == "open" else close_ssh(state, args.cidr)
    json.dump(infos, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
