"""Exercise documented startup commands without altering the real workspace."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="trace-startup-check-") as directory:
    work = Path(directory)
    shutil.copy2(root / "start.sh", work / "start.sh")
    (work / "config").mkdir()
    shutil.copy2(
        root / "config/settings.example.toml", work / "config/settings.example.toml"
    )
    binary = work / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE_VERIFY_DOCKER_LOG"\n')
    docker.chmod(0o755)
    log = work / "docker.log"
    env = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "TRACE_VERIFY_DOCKER_LOG": str(log),
    }

    def run(mode):
        return subprocess.run(
            ["bash", str(work / "start.sh"), mode],
            env=env,
            capture_output=True,
            text=True,
        )

    initial = run("production")
    assert initial.returncode == 1 and (work / "config/settings.toml").exists()
    assert stat.S_IMODE((work / ".env").stat().st_mode) == 0o600
    secret = (work / ".env").read_text().splitlines()[0].split("=", 1)[1]
    assert len(secret) >= 12 and secret not in initial.stdout + initial.stderr
    config = (work / "config/settings.toml").read_text()
    ready = run("production")
    assert ready.returncode == 0 and "데모:" not in ready.stdout
    assert (
        "compose -p trace-production -f compose.yaml up -d --build --wait"
        in log.read_text()
    )
    assert (work / "config/settings.toml").read_text() == config
    assert secret in (work / ".env").read_text()
    demo = run("demo")
    assert demo.returncode == 0 and "데모:" in demo.stdout
    assert (
        "compose -p trace-demo -f compose.demo.yaml up -d --build --wait"
        in log.read_text()
    )
    before = log.read_text()
    assert run("invalid").returncode == 1 and log.read_text() == before
print(
    "Startup bootstrap, production/demo mode selection, secret handling and invalid mode passed."
)
