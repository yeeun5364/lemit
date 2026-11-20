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


if __name__ == "__main__":
    unittest.main()


# ---------- 가짜 aws CLI로 실제 적용 흐름 시험 (PR #43 리뷰 P1·P2) ----------

import os  # noqa: E402
import tempfile  # noqa: E402

from lightsail_firewall import FirewallError, apply, main, ssh_allows  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_aws_lightsail.py"
HTTPS_RULE = {"fromPort": 443, "toPort": 443, "protocol": "tcp", "cidrs": ["0.0.0.0/0"]}


class ApplyWithFakeAwsTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.env = mock_env = {
            "FAKE_AWS_STATE": self.path,
            "LIGHTSAIL_AWS_CLI": f'"{sys.executable}" "{FAKE}"',
            "LIGHTSAIL_FIREWALL_WAIT": "0",
        }
        self._old = {k: os.environ.get(k) for k in mock_env}
        os.environ.update(mock_env)
        self.addCleanup(self._restore)
        self.write(copy.deepcopy(STATE))

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write(self, db):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(db, f)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def apply(self, action):
        return apply("lemit-server", RUNNER, action, log=lambda *_: None)

    def test_open_then_close_restores_original_rules(self):
        self.apply("open")
        self.assertTrue(ssh_allows(self.read(), RUNNER))
        self.apply("close")
        db = self.read()
        self.assertFalse(ssh_allows(db, RUNNER))
        self.assertEqual(normalize_states(db), normalize_states(STATE))  # 관리자 IP·별칭·80·IPv6 모두 원래대로

    def test_admin_change_between_read_and_write_is_not_overwritten(self):
        """P1: 처음 읽은 뒤 쓰기 직전에 관리자가 443을 추가 → 덮어쓰지 않고 다시 시도해 443을 보존."""
        db = copy.deepcopy(STATE)
        db["mutate_on_get"] = {"2": HTTPS_RULE}  # 2번째 get(쓰기 직전 다시 읽기) 때 변경 발생
        self.write(db)
        self.apply("open")
        db = self.read()
        self.assertTrue(rule([i for i in db["portStates"]], 443), "관리자가 추가한 443이 사라짐")
        self.assertTrue(ssh_allows(db, RUNNER))
        self.assertEqual(db["puts"], 1)

    def test_change_after_write_is_detected_as_failure(self):
        """P1: 쓴 직후 누군가 규칙을 바꾸면 기대와 달라 실패로 알린다 (조용히 성공 처리하지 않음)."""
        db = copy.deepcopy(STATE)
        db["mutate_after_put"] = HTTPS_RULE
        self.write(db)
        with self.assertRaises(FirewallError) as ctx:
            self.apply("open")
        self.assertIn("tcp/443-443", str(ctx.exception))
        self.assertNotIn(ADMIN.split("/")[0], str(ctx.exception))  # 공개 로그에 관리자 IP 미출력

    def test_propagation_lag_is_tolerated(self):
        db = copy.deepcopy(STATE)
        db["lag_gets"] = 2  # 쓴 뒤 두 번은 예전 상태가 보임
        self.write(db)
        self.apply("open")
        self.assertTrue(ssh_allows(self.read(), RUNNER))

    def test_keeps_retrying_then_gives_up_without_writing(self):
        db = copy.deepcopy(STATE)
        db["mutate_on_get"] = {str(n): dict(HTTPS_RULE, fromPort=9000 + n, toPort=9000 + n) for n in (2, 4, 6)}
        self.write(db)
        with self.assertRaises(FirewallError):
            self.apply("open")
        self.assertEqual(self.read().get("puts", 0), 0)  # 한 번도 덮어쓰지 않음

    def test_no_write_when_already_in_desired_state(self):
        self.apply("close")  # 러너 IP가 없는데 close → 쓰기 없음
        self.assertEqual(self.read().get("puts", 0), 0)

    def test_close_check_is_structural_not_string_search(self):
        """P2: 다른 포트(8080)에 같은 IP가 있어도 22번에서 빠졌으면 성공."""
        db = copy.deepcopy(STATE)
        db["portStates"].append(
            {"fromPort": 8080, "toPort": 8080, "protocol": "tcp", "state": "open", "cidrs": [RUNNER]}
        )
        self.write(db)
        self.assertEqual(main(["open", "--instance", "lemit-server", "--cidr", RUNNER]), 0)
        self.assertEqual(main(["close", "--instance", "lemit-server", "--cidr", RUNNER]), 0)
        db = self.read()
        self.assertFalse(ssh_allows(db, RUNNER))
        self.assertEqual(rule(db["portStates"], 8080)[0]["cidrs"], [RUNNER])  # 다른 포트 규칙은 그대로

    def test_ssh_allows_ignores_closed_and_other_ports(self):
        state = {
            "portStates": [
                {"fromPort": 22, "toPort": 22, "protocol": "tcp", "state": "closed", "cidrs": [RUNNER]},
                {"fromPort": 2222, "toPort": 2222, "protocol": "tcp", "state": "open", "cidrs": [RUNNER]},
                {"fromPort": 22, "toPort": 22, "protocol": "udp", "state": "open", "cidrs": [RUNNER]},
            ]
        }
        self.assertFalse(ssh_allows(state, RUNNER))

    def test_cli_process(self):
        """명령줄 실행(워크플로와 같은 방식): open 후 22번에 러너 IP, 종료 코드 0, 출력에 관리자 IP 없음."""
        script = Path(__file__).resolve().parent / "lightsail_firewall.py"
        done = subprocess.run(
            [sys.executable, str(script), "open", "--instance", "lemit-server", "--cidr", RUNNER],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(ssh_allows(self.read(), RUNNER))
        self.assertNotIn(ADMIN.split("/")[0], done.stdout + done.stderr)

    def test_main_reports_aws_failure(self):
        os.environ["LIGHTSAIL_AWS_CLI"] = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
        self.assertEqual(main(["open", "--instance", "lemit-server", "--cidr", RUNNER]), 1)


def normalize_states(db):
    from lightsail_firewall import normalize, port_infos

    return normalize(port_infos(db))
