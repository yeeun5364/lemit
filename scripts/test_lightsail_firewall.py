"""scripts/lightsail_firewall.py 단위 테스트:  python -m unittest discover -s scripts -p "test_*.py" """

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lightsail_firewall import close_ssh, open_ssh  # noqa: E402

RUNNER = "203.0.113.7/32"
ADMIN = "198.51.100.20/32"

# 실제 운영 예상 상태: 22는 관리자 IP + 브라우저 SSH 별칭, 80은 전체, IPv6 80도 전체
STATE = {
    "portStates": [
        {
            "fromPort": 22,
            "toPort": 22,
            "protocol": "tcp",
            "state": "open",
            "cidrs": [ADMIN],
            "ipv6Cidrs": [],
            "cidrListAliases": ["lightsail-connect"],
        },
        {
            "fromPort": 80,
            "toPort": 80,
            "protocol": "tcp",
            "state": "open",
            "cidrs": ["0.0.0.0/0"],
            "ipv6Cidrs": ["::/0"],
            "cidrListAliases": [],
        },
    ]
}


def rule(infos, port):
    return [i for i in infos if i["fromPort"] == port]


class OpenCloseTests(unittest.TestCase):
    def test_open_adds_runner_and_keeps_everything_else(self):
        infos = open_ssh(copy.deepcopy(STATE), RUNNER)
        ssh = rule(infos, 22)[0]
        self.assertEqual(ssh["cidrs"], [ADMIN, RUNNER])
        self.assertEqual(ssh["cidrListAliases"], ["lightsail-connect"])  # 브라우저 SSH 유지
        web = rule(infos, 80)[0]
        self.assertEqual((web["cidrs"], web["ipv6Cidrs"]), (["0.0.0.0/0"], ["::/0"]))  # 다른 포트·IPv6 유지
        self.assertNotIn("state", ssh)  # put 입력에는 state 필드가 없음

    def test_close_removes_only_runner(self):
        opened = {"portStates": [dict(i, state="open") for i in open_ssh(copy.deepcopy(STATE), RUNNER)]}
        infos = close_ssh(opened, RUNNER)
        self.assertEqual(rule(infos, 22)[0]["cidrs"], [ADMIN])
        self.assertEqual(len(rule(infos, 80)), 1)

    def test_round_trip_restores_original(self):
        original = close_ssh(copy.deepcopy(STATE), "192.0.2.99/32")  # 없는 IP 제거 = 원래 그대로
        opened = {"portStates": [dict(i, state="open") for i in open_ssh(copy.deepcopy(STATE), RUNNER)]}
        self.assertEqual(close_ssh(opened, RUNNER), original)

    def test_open_when_ssh_rule_missing_and_close_removes_rule(self):
        state = {"portStates": [STATE["portStates"][1]]}  # 22번 규칙 없음
        opened = open_ssh(copy.deepcopy(state), RUNNER)
        self.assertEqual(rule(opened, 22), [{"fromPort": 22, "toPort": 22, "protocol": "tcp", "cidrs": [RUNNER]}])
        closed = close_ssh({"portStates": [dict(i, state="open") for i in opened]}, RUNNER)
        self.assertEqual(rule(closed, 22), [])  # 원래 없던 규칙은 다시 없어짐

    def test_ssh_open_to_all_is_not_changed(self):
        state = copy.deepcopy(STATE)
        state["portStates"][0]["cidrs"] = ["0.0.0.0/0"]
        self.assertEqual(rule(open_ssh(state, RUNNER), 22)[0]["cidrs"], ["0.0.0.0/0"])

    def test_open_twice_does_not_duplicate(self):
        once = {"portStates": [dict(i, state="open") for i in open_ssh(copy.deepcopy(STATE), RUNNER)]}
        self.assertEqual(rule(open_ssh(once, RUNNER), 22)[0]["cidrs"], [ADMIN, RUNNER])

    def test_closed_port_states_are_dropped(self):
        state = copy.deepcopy(STATE)
        state["portStates"].append({"fromPort": 5432, "toPort": 5432, "protocol": "tcp", "state": "closed"})
        self.assertEqual(rule(open_ssh(state, RUNNER), 5432), [])

    def test_only_single_ipv4_host_allowed(self):
        for bad in ("0.0.0.0/0", "203.0.113.0/24", "2001:db8::1/128", "not-an-ip", "203.0.113.7"):
            with self.subTest(cidr=bad), self.assertRaises(ValueError):
                open_ssh(copy.deepcopy(STATE), bad)

    def test_missing_port_states_is_error(self):
        with self.assertRaises(ValueError):
            open_ssh({"instance": {}}, RUNNER)

    def test_cli(self):
        script = Path(__file__).resolve().parent / "lightsail_firewall.py"
        out = subprocess.run(
            [sys.executable, str(script), "open", "--cidr", RUNNER],
            input=json.dumps(STATE),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn(RUNNER, rule(json.loads(out), 22)[0]["cidrs"])


if __name__ == "__main__":
    unittest.main()
