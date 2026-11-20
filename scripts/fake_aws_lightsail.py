"""테스트용 가짜 aws CLI (scripts/test_lightsail_firewall.py 전용). 실제 AWS를 부르지 않는다.

상태 파일(FAKE_AWS_STATE, JSON)에 방화벽 규칙을 두고 다음 두 명령만 흉내 낸다.
  aws lightsail get-instance-port-states --instance-name X --output json
  aws lightsail put-instance-public-ports --instance-name X --port-infos file://path --output json

상태 파일의 시나리오 키
  mutate_on_get: {"N": rule}  N번째 get 직전에 rule을 추가 (관리자가 콘솔에서 바꾼 상황)
  lag_gets: k                 put 뒤 k번의 get은 put 이전 상태를 돌려줌 (반영 지연)
  mutate_after_put: rule      put 직후 rule을 추가 (쓴 뒤 누군가 바꾼 상황)
기록: gets(읽기 횟수), puts(쓰기 횟수)
"""

import json
import os
import sys


def main():
    path = os.environ["FAKE_AWS_STATE"]
    with open(path, encoding="utf-8") as f:
        db = json.load(f)
    args = sys.argv[1:]
    op = args[1]
    if op == "get-instance-port-states":
        db["gets"] = db.get("gets", 0) + 1
        rule = db.get("mutate_on_get", {}).pop(str(db["gets"]), None)
        if rule:
            db["portStates"].append(dict(rule, state="open"))
        if db.get("lag_left", 0) > 0:
            db["lag_left"] -= 1
            out = {"portStates": db["pre_put"]}
        else:
            out = {"portStates": db["portStates"]}
    elif op == "put-instance-public-ports":
        src = args[args.index("--port-infos") + 1].removeprefix("file://")
        with open(src, encoding="utf-8") as f:
            infos = json.load(f)
        db["puts"] = db.get("puts", 0) + 1
        db["pre_put"] = db["portStates"]
        db["lag_left"] = db.get("lag_gets", 0)
        db["portStates"] = [dict(i, state="open") for i in infos]
        rule = db.pop("mutate_after_put", None)
        if rule:
            db["portStates"].append(dict(rule, state="open"))
        out = {"operation": {"status": "Succeeded"}}
    else:
        print(f"unsupported: {op}", file=sys.stderr)
        return 2
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f)
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
