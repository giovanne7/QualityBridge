import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProcessResult:
    exit_code: int | None
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None


def stop_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def execute(
    command: list[str], cwd: Path, env: dict[str, str], log: Path, timeout: int
) -> ProcessResult:
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with log.open("wb") as stream:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                shell=False,
                **options,
            )
        except OSError as exc:
            stream.write(str(exc).encode("utf-8", errors="replace"))
            return ProcessResult(None, error=str(exc))
        try:
            return ProcessResult(process.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            stop_tree(process)
            return ProcessResult(process.returncode, timed_out=True)
        except KeyboardInterrupt:
            stop_tree(process)
            return ProcessResult(process.returncode, cancelled=True)
