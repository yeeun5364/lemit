"""deploy.yml '러너 격리 확인' 단계의 Windows 드라이브·interop 판정 테스트 (#56, PR #58 2차 리뷰 P1).

러너 작업은 저장소 코드를 받지 않으므로 검사는 deploy.yml 안에 있다. 여기서는 그 run 본문을 꺼내
가짜 /proc/self/mountinfo·binfmt_misc(LEMIT_CHECK_MOUNTINFO·LEMIT_CHECK_BINFMT)로 실행해 해당 판정만 본다.
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
        self.script.write_text("set -eo pipefail\n" + isolation_script(), encoding="utf-8", newline="\n")
        self.binfmt = self.dir / "binfmt"
        self.binfmt.mkdir()

    def run_check(self, mounts, interop=None):
        mountinfo = self.dir / "mountinfo"
        mountinfo.write_text("\n".join([ROOT_EXT4, *mounts]) + "\n", encoding="utf-8", newline="\n")
        for name, state in (interop or {}).items():
            (self.binfmt / name).write_text(f"{state}\ninterpreter /init\n", encoding="utf-8", newline="\n")
        env = dict(os.environ, LEMIT_CHECK_MOUNTINFO=str(mountinfo), LEMIT_CHECK_BINFMT=str(self.binfmt))
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

    def test_interop_enabled_is_flagged(self):
        for name in ("WSLInterop", "WSLInterop-late"):
            with self.subTest(name=name):
                for f in self.binfmt.iterdir():
                    f.unlink()
                self.assert_flagged(self.run_check([], {name: "enabled"}), "interop")

    def test_interop_disabled_or_absent_is_not_flagged(self):
        self.assert_not_flagged(self.run_check([], {"WSLInterop": "disabled"}), "interop")
        for f in self.binfmt.iterdir():
            f.unlink()
        self.assert_not_flagged(self.run_check([]), "interop")


if __name__ == "__main__":
    unittest.main()
