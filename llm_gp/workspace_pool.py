from __future__ import annotations

import queue
from collections.abc import Iterator, Sequence
from contextlib import contextmanager


class WorkspacePool:
    """Bounded pool of workspace path strings handed out one at a time to
    concurrent callers, so N independent catkin workspaces can back N
    concurrently-running ROS/Gazebo evaluations without racing on the same
    candidate-source file. Backed by queue.Queue for built-in thread safety
    and blocking acquire semantics.
    """

    def __init__(self, workspaces: Sequence[str]) -> None:
        workspaces = list(workspaces)
        if not workspaces:
            raise ValueError("WorkspacePool requires at least one workspace")
        self.size = len(workspaces)
        self._queue: queue.Queue[str] = queue.Queue()
        for workspace in workspaces:
            self._queue.put(workspace)

    def acquire(self) -> str:
        """Block until a workspace is available, then remove it from the pool."""
        return self._queue.get()

    def release(self, workspace: str) -> None:
        """Return a previously acquired workspace to the pool."""
        self._queue.put(workspace)

    @contextmanager
    def lease(self) -> Iterator[str]:
        workspace = self.acquire()
        try:
            yield workspace
        finally:
            self.release(workspace)
