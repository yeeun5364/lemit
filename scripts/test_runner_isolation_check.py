"""deploy.yml '러너 격리 확인' 단계의 Windows 드라이브·interop 판정 테스트 (#56, PR #58 2차·3차 리뷰).

러너 작업은 저장소 코드를 받지 않으므로 검사는 deploy.yml 안에 있다. 여기서는 그 run 본문을 꺼내
가짜 /proc/self/mountinfo·binfmt_misc·/etc/wsl.conf와 가짜 PE 실행 결과
(LEMIT_CHECK_MOUNTINFO·LEMIT_CHECK_BINFMT·LEMIT_CHECK_WSLCONF·LEMIT_CHECK_PEEXEC)로 실행해 해당 판정만 본다.
(root·sudo·그룹 판정은 실행하는 컴퓨터에 따라 달라 여기서 단언하지 않는다. Docker에서 따로 확인)
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy.yml"
BASH = shutil.which("bash")

# 실제 WSL2(Ubuntu 24.04, Docker Desktop 연동) /proc/self/mountinfo에서 가져온 9p 줄 (2026-11-20)
DRIVERS = (
    "239 243 0:36 / /usr/lib/wsl/drivers ro,nosuid,nodev,noatime - 9p drivers "
    "ro,aname=drivers;fmask=222;dmask=222,cache=0x5,access=client,msize=65536,trans=fd,rfd=8,wfd=8"
)
MNT_C = (
    "262 243 0:79 / /mnt/c rw,noatime - 9p C:\\134 "
    "rw,aname=drvfs;path=C:\\;uid=1000;gid=1000;symlinkroot=/mnt/,cache=0x5,access=client,msize=65536,trans=fd"
)
DOCKER_HOST = (
    "597 243 0:92 / /Docker/host rw,noatime - 9p C:\\134Program\\040Files\\134Docker\\134Docker\\134resources "
    "rw,aname=drvfs;path=C:\\Program Files\\Docker\\Docker\\resources;symlinkroot=/mnt/,cache=0x5"
)
ROOT_EXT4 = "25 1 8:48 / / rw,relatime - ext4 /dev/sdd rw,discard,errors=remount-ro,data=ordered"
FSTAB_D = "300 243 0:80 / /data rw,noatime - 9p D:\\134 rw,aname=drvfs;path=D:\\;uid=1000;gid=1000"
WSL1_DRVFS = "40 25 0:40 / /mnt/e rw,noatime - drvfs E:\\ rw,case=off"

# PE 머리(MZ) 파일 실행 결과 (WSL 3.0.5, 커널 6.18, 2026-11-20 실측). (종료 코드, 출력)
BLOCKED = (1, "<3>WSL (432 - ) ERROR: UtilAcceptVsock:281: accept4 failed 110")  # interop 끈 LEMIT-Runner, 약 10초
WINDOWS_ANSWERED = (1, "/tmp/probe.fpvug9.exe: Invalid argument")  # interop 켠 개인 배포판, Windows가 즉시 응답
WINDOWS_RAN = (0, "")  # 실제로 실행됨
TIMED_OUT = (124, "")  # timeout 30에 걸림

# 실제 LEMIT-Runner /etc/wsl.conf 형식 (주석·공백·대소문자 변형 포함)
WSLCONF_OFF = "[automount]\nenabled=false\n\n[interop]\nenabled = False  # 러너 전용\nappendWindowsPath=false\n"
WSLCONF_NOT_OFF = [
    "[automount]\nenabled=false\n[interop]\nappendWindowsPath=false\n",  # interop 줄 없음 (기본값 켜짐)
    "[interop]\nenabled=true\n",
    "[interop]\n# enabled=false\n",  # 주석 처리
    "[boot]\nenabled=false\n[interop]\n",  # 다른 구역의 enabled
    "[interop]\nenabled=false\nenabled=true\n",  # 마지막 값이 적용됨
    None,  # 파일 없음
]


def isolation_script():
    doc = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))
    steps = doc["jobs"]["deploy"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "러너 격리 확인")


@unittest.skipUnless(BASH, "bash 필요")
class RunnerIsolationCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "check.sh"
        # GitHub Actions의 bash 기본 옵션과 같게 (-e -o pipefail)
        self.script.write_text("set -eo pipefail\n" + isolation_script(), encoding="utf-8", newline="\n")
        self.binfmt = self.dir / "binfmt"
        self.binfmt.mkdir()
        self.called = self.dir / "pe-called"

    def fake_pe_exec(self, result):
        """PE 파일 실행을 흉내 낸다: 받은 파일의 앞 2바이트를 기록하고 정해진 출력·종료 코드를 낸다."""
        rc, text = result
        fake = self.dir / "fake-pe-exec.sh"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            f"head -c 2 \"$1\" > '{self.called.as_posix()}'\n"
            f"printf '%s\\n' '{text}' >&2\n"
            f"exit {rc}\n",
            encoding="utf-8",
            newline="\n",
        )
        fake.chmod(0o755)
        return fake

    def run_check(self, mounts, interop=None, pe=WINDOWS_ANSWERED, wslconf=WSLCONF_OFF):
        mountinfo = self.dir / "mountinfo"
        mountinfo.write_text("\n".join([ROOT_EXT4, *mounts]) + "\n", encoding="utf-8", newline="\n")
        for f in self.binfmt.iterdir():
            f.unlink()
        for name, state in (interop or {}).items():
            (self.binfmt / name).write_text(f"{state}\ninterpreter /init\nflags: PF\n", encoding="utf-8", newline="\n")
        conf = self.dir / "wsl.conf"
        conf.unlink(missing_ok=True)
        if wslconf is not None:
            conf.write_text(wslconf, encoding="utf-8", newline="\n")
        self.called.unlink(missing_ok=True)
        env = dict(
            os.environ,
            LEMIT_CHECK_MOUNTINFO=str(mountinfo),
            LEMIT_CHECK_BINFMT=str(self.binfmt),
            LEMIT_CHECK_WSLCONF=str(conf),
            LEMIT_CHECK_PEEXEC=str(self.fake_pe_exec(pe)),
        )
        done = subprocess.run(
            [BASH, str(self.script)], capture_output=True, text=True, encoding="utf-8", env=env, check=False
        )
        return done.stdout + done.stderr  # 단언 실패 메시지에는 repr만 쓴다 (::error:: 줄 맨 앞 방지, #53)

    def assert_flagged(self, out, text):
        self.assertIn(text, out, repr(out))

    def assert_not_flagged(self, out, text):
        self.assertNotIn(text, out, repr(out))

    def test_driver_store_mount_alone_is_not_windows_drive(self):
        out = self.run_check([DRIVERS])
        self.assert_not_flagged(out, "Windows 드라이브")

    def test_mnt_c_is_flagged(self):
        self.assert_flagged(self.run_check([DRIVERS, MNT_C]), "Windows 드라이브가 연결됨(/mnt/c)")

    def test_mounts_outside_mnt_c_are_flagged(self):
        """2차 리뷰 P1: /mnt/c만 보면 /mnt/d·fstab·Docker 연동 마운트를 놓친다."""
        for line, where in ((FSTAB_D, "/data"), (DOCKER_HOST, "/Docker/host"), (WSL1_DRVFS, "/mnt/e")):
            with self.subTest(where=where):
                self.assert_flagged(self.run_check([DRIVERS, line]), where)

    def test_all_windows_mounts_are_listed(self):
        out = self.run_check([DRIVERS, MNT_C, DOCKER_HOST])
        self.assert_flagged(out, "Windows 드라이브가 연결됨(/mnt/c /Docker/host)")

    def test_shared_binfmt_with_blocked_execution_passes(self):
        """3차 리뷰 P1: WSL2는 binfmt 등록을 배포판끼리 공유해, interop을 끈 전용 배포판에서도 'enabled'로 보인다.
        wsl.conf로 껐고 실제 실행이 막히면 통과한다."""
        out = self.run_check([DRIVERS], {"WSLInterop": "enabled"}, pe=BLOCKED)
        self.assertIn("Windows 실행은 막혀 있음", out, repr(out))
        # 다른 항목(PATH·sudo 등)은 테스트를 돌리는 컴퓨터에 따라 걸릴 수 있으므로 interop 관련 오류만 없는지 본다
        errors = [line for line in out.splitlines() if line.startswith("::error::")]
        self.assertFalse(any("interop" in e or "wsl.conf" in e for e in errors), repr(out))
        self.assertEqual(self.called.read_bytes(), b"MZ", "PE 머리 파일로 실제 실행을 시도해야 한다")

    def test_interop_reaching_windows_is_flagged(self):
        """Windows가 실행 요청을 받거나(즉시 응답·실행) 결과를 알 수 없으면 막는다. WSLInterop-late도 같다."""
        for name in ("WSLInterop", "WSLInterop-late"):
            for result in (WINDOWS_ANSWERED, WINDOWS_RAN, TIMED_OUT):
                with self.subTest(name=name, rc=result[0]):
                    out = self.run_check([], {name: "enabled"}, pe=result)
                    self.assert_flagged(out, f"interop)이 막혔는지 확인하지 못함(종료 코드 {result[0]}")
                    self.assert_flagged(out, "::error::")

    def test_error_output_cannot_inject_workflow_commands(self):
        out = self.run_check([], {"WSLInterop": "enabled"}, pe=(1, "::add-mask::x"))
        self.assert_flagged(out, "종료 코드 1: add-maskx")
        self.assert_not_flagged(out, "::add-mask")

    def test_blocked_execution_without_wslconf_setting_is_flagged(self):
        """실행이 막혀 보여도 이 배포판 설정이 interop을 끄지 않으면 막는다 (두 조건 모두 필요)."""
        for conf in WSLCONF_NOT_OFF:
            with self.subTest(conf=conf):
                out = self.run_check([], {"WSLInterop": "enabled"}, pe=BLOCKED, wslconf=conf)
                self.assert_flagged(out, "[interop] enabled=false가 없음")

    def test_interop_disabled_or_absent_skips_probe(self):
        for interop in ({"WSLInterop": "disabled"}, None):
            with self.subTest(interop=interop):
                out = self.run_check([], interop, pe=WINDOWS_RAN, wslconf=None)
                self.assert_not_flagged(out, "interop")
                self.assertFalse(self.called.exists(), "binfmt 등록이 없으면 실행 시험을 하지 않는다")


if __name__ == "__main__":
    unittest.main()
