from __future__ import annotations

from pathlib import Path

import pytest

from llm_gp.run_lock import ros_gazebo_run_lock


def test_ros_gazebo_lock_rejects_a_second_experiment(tmp_path: Path) -> None:
    with ros_gazebo_run_lock(tmp_path):
        with pytest.raises(RuntimeError, match="別のROS/Gazebo実験"):
            with ros_gazebo_run_lock(tmp_path):
                pass


def test_ros_gazebo_lock_writes_pid_and_cleans_up(tmp_path: Path) -> None:
    with ros_gazebo_run_lock(tmp_path) as lock_path:
        assert lock_path.exists()
        assert lock_path.stat().st_size > 0
    assert not lock_path.exists()


def test_runner_uses_private_masters_and_group_cleanup() -> None:
    script = Path("scripts/run_ros_gazebo_evaluation.sh").read_text(encoding="utf-8")
    assert "export ROS_MASTER_URI=" in script
    assert "export GAZEBO_MASTER_URI=" in script
    assert 'kill -KILL -- "-$launch_pid"' in script
