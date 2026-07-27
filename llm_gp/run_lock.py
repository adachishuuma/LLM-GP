from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


def _try_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def ros_gazebo_run_lock(project_root: Path) -> Iterator[Path]:
    """Prevent two project processes from modifying and evaluating rastar.cpp."""

    if os.environ.get("LLM_GP_SKIP_RUN_LOCK") == "1":
        yield project_root / "experiment_results" / ".ros_gazebo_experiment.lock"
        return

    lock_path = project_root / "experiment_results" / ".ros_gazebo_experiment.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists() and lock_path.stat().st_size > 0:
        try:
            existing = lock_path.read_text(encoding="utf-8").strip()
            if not existing or existing == "\x00" or not existing.isdigit():
                lock_path.unlink(missing_ok=True)
            elif int(existing) != os.getpid() and not _pid_is_running(int(existing)):
                lock_path.unlink(missing_ok=True)
            elif lock_path.stat().st_mtime < time.time() - 60 * 60 * 6:
                lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    handle = lock_path.open("a+b")
    try:
        if lock_path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _try_lock(handle)
        except OSError as exc:
            if lock_path.exists():
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                handle.close()
            except OSError:
                pass
            handle = lock_path.open("a+b")
            try:
                handle.seek(0)
                _try_lock(handle)
            except OSError as retry_exc:
                raise RuntimeError(
                    "別のROS/Gazebo実験が実行中です。終了してから再実行してください。"
                ) from retry_exc
        handle.seek(0)
        handle.truncate(0)
        handle.write(f"{os.getpid()}".encode("utf-8"))
        handle.flush()
        yield lock_path
    finally:
        try:
            _unlock(handle)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass
        if lock_path.exists():
            try:
                lock_path.unlink()
            except OSError:
                pass


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
