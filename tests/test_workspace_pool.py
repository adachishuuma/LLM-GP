from __future__ import annotations

import threading
import time

import pytest

from llm_gp.workspace_pool import WorkspacePool


def test_empty_pool_is_rejected() -> None:
    with pytest.raises(ValueError):
        WorkspacePool([])


def test_lease_returns_and_restores_a_workspace() -> None:
    pool = WorkspacePool(["ws_a"])
    with pool.lease() as workspace:
        assert workspace == "ws_a"
    # released back after the context manager exits
    assert pool.acquire() == "ws_a"


def test_acquire_blocks_until_a_workspace_is_released() -> None:
    pool = WorkspacePool(["ws_a"])
    held = pool.acquire()
    acquired_event = threading.Event()

    def waiter() -> None:
        pool.acquire()
        acquired_event.set()

    thread = threading.Thread(target=waiter)
    thread.start()
    try:
        # No workspace available yet -- the waiter must still be blocked.
        assert not acquired_event.wait(timeout=0.1)
        pool.release(held)
        # Now it should unblock promptly.
        assert acquired_event.wait(timeout=1.0)
    finally:
        thread.join(timeout=1.0)


def test_pool_never_double_assigns_a_workspace_under_concurrency() -> None:
    pool = WorkspacePool(["ws_a", "ws_b"])
    held: set[str] = set()
    lock = threading.Lock()
    violations: list[str] = []

    def worker() -> None:
        with pool.lease() as workspace:
            with lock:
                if workspace in held:
                    violations.append(workspace)
                held.add(workspace)
            time.sleep(0.02)
            with lock:
                held.discard(workspace)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert violations == []
