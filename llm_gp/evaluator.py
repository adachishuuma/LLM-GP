from __future__ import annotations

import json
import os
import random
import subprocess
import threading
from pathlib import Path
from typing import Protocol

from .config import RosGazeboSettings
from .models import EvaluationResult, GoalPose, Individual
from .workspace_pool import WorkspacePool


class Evaluator(Protocol):
    def evaluate(self, individual: Individual) -> EvaluationResult: ...


class MockEvaluator:
    """Seeded evaluator producing realistic values across all three bin ranges."""

    def __init__(
        self, rng: random.Random, fixed_goal: GoalPose, repetitions: int = 3
    ) -> None:
        self._rng = rng
        self.fixed_goal = fixed_goal
        self.repetitions = repetitions

    def evaluate(self, individual: Individual) -> EvaluationResult:
        samples = [
            (
                self._rng.uniform(0.05, 0.45),
                self._rng.uniform(7.0, 20.0),
                self._rng.uniform(12.0, 48.0),
                self._rng.uniform(80.0, 3000.0),
            )
            for _ in range(self.repetitions)
        ]
        planning_time = sum(item[0] for item in samples) / len(samples)
        path_length = sum(item[1] for item in samples) / len(samples)
        arrival_time = sum(item[2] for item in samples) / len(samples)
        node_expansions = sum(item[3] for item in samples) / len(samples)
        return EvaluationResult(
            success=True,
            planning_time=round(planning_time, 6),
            path_length=round(path_length, 6),
            arrival_time=round(arrival_time, 6),
            node_expansions=round(node_expansions, 2),
        )


class RosGazeboEvaluator:
    """Builds one C++ candidate and measures it in TurtleBot3 Gazebo via WSL."""

    def __init__(
        self,
        settings: RosGazeboSettings,
        fixed_goal: GoalPose,
        timeout_seconds: int,
        repetitions: int,
        build_timeout_seconds: int,
        log_directory: Path,
        project_root: Path,
        rng: random.Random | None = None,
        goal_x_range: tuple[float, float] | None = None,
        goal_y_range: tuple[float, float] | None = None,
    ) -> None:
        self.settings = settings
        self.fixed_goal = fixed_goal
        self.timeout_seconds = timeout_seconds
        self.repetitions = repetitions
        self.build_timeout_seconds = build_timeout_seconds
        self.log_directory = log_directory
        self.runner_script = project_root / "scripts" / "run_ros_gazebo_evaluation.sh"
        self.trial_script = project_root / "scripts" / "ros_gazebo_trial.py"
        self.rng = rng or random.Random()
        # random.Random isn't safe to call concurrently from multiple threads
        # without external synchronization; evaluate() may now run in a
        # worker thread (see workspace_pool below), so _sample_goal() must
        # serialize its access to self.rng.
        self._rng_lock = threading.Lock()
        self.goal_x_range = goal_x_range
        self.goal_y_range = goal_y_range
        # [workspace, *additional_workspaces]: independent catkin workspaces
        # that let multiple evaluate() calls run concurrently (each candidate
        # is built into its own workspace's rastar.cpp) instead of racing on
        # a single shared one. A single-entry pool (the default) makes every
        # evaluate() call block until the previous one releases it, i.e. the
        # same fully-serialized behavior as before this existed.
        self.workspace_pool = WorkspacePool(
            [settings.workspace, *settings.additional_workspaces]
        )

    def _sample_goal(self) -> GoalPose:
        if self.goal_x_range is None or self.goal_y_range is None:
            return self.fixed_goal
        with self._rng_lock:
            x = self.rng.uniform(*self.goal_x_range)
            y = self.rng.uniform(*self.goal_y_range)
        return GoalPose(x, y, self.fixed_goal.yaw)

    def evaluate(self, individual: Individual) -> EvaluationResult:
        self.log_directory.mkdir(parents=True, exist_ok=True)
        results: list[EvaluationResult] = []
        with self.workspace_pool.lease() as workspace:
            for repetition in range(self.repetitions):
                output_path = self.log_directory / (
                    f"{individual.individual_id}_repetition_{repetition + 1}.json"
                )
                goal = self._sample_goal()
                command = self._build_command(individual, output_path, goal, workspace)
                results.append(self._evaluate_once(command, output_path))

        successful = [result for result in results if result.success]
        raw_log_path = str(
            self.log_directory / f"{individual.individual_id}_repetition_*.json"
        )
        if len(successful) != self.repetitions:
            errors = [
                result.error_message or "unknown evaluation failure"
                for result in results
                if not result.success
            ]
            return EvaluationResult(
                False,
                _mean_metric(successful, "planning_time"),
                _mean_metric(successful, "path_length"),
                _mean_metric(successful, "arrival_time"),
                f"{len(successful)}/{self.repetitions} repetitions succeeded: "
                + " | ".join(errors),
                raw_log_path,
                node_expansions=_mean_metric(successful, "node_expansions"),
            )
        return EvaluationResult(
            True,
            planning_time=_mean_metric(successful, "planning_time"),
            path_length=_mean_metric(successful, "path_length"),
            arrival_time=_mean_metric(successful, "arrival_time"),
            raw_log_path=raw_log_path,
            node_expansions=_mean_metric(successful, "node_expansions"),
        )

    def _evaluate_once(
        self, command: list[str], output_path: Path
    ) -> EvaluationResult:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=(
                    self.timeout_seconds
                    + self.settings.startup_timeout_seconds
                    + 2 * self.build_timeout_seconds
                ),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return EvaluationResult(
                False, None, None, None, f"ROS/Gazebo runner failed: {exc}", str(output_path)
            )
        if not output_path.is_file():
            message = (completed.stderr or completed.stdout or "no runner output").strip()
            return EvaluationResult(
                False,
                None,
                None,
                None,
                f"ROS/Gazebo runner produced no result (exit {completed.returncode}): {message[-1000:]}",
                str(output_path),
            )
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return EvaluationResult(
                False, None, None, None, f"Invalid ROS result JSON: {exc}", str(output_path)
            )
        return EvaluationResult(
            success=bool(payload.get("success")),
            planning_time=_optional_float(payload.get("planning_time")),
            path_length=_optional_float(payload.get("path_length")),
            arrival_time=_optional_float(payload.get("arrival_time")),
            error_message=payload.get("error_message"),
            raw_log_path=str(output_path),
            node_expansions=_optional_float(payload.get("node_expansions")),
        )

    def _build_command(
        self, individual: Individual, output_path: Path, goal: GoalPose, workspace: str
    ) -> list[str]:
        command = []
        if os.name == "nt":
            command.extend(
                ["wsl.exe", "-d", self.settings.wsl_distribution, "--", "bash"]
            )
        else:
            command.append("bash")
        command.extend(
            [
                _to_wsl_path(self.runner_script),
                workspace,
                _to_wsl_path(Path(individual.source_path)),
                self.settings.candidate_target,
                self.settings.package_name,
                _to_wsl_path(self.settings.launch_file),
                _to_wsl_path(self.trial_script),
                _to_wsl_path(output_path),
                self.settings.robot_model,
                self.settings.gazebo_model_name,
                str(self.settings.start_pose.x),
                str(self.settings.start_pose.y),
                str(self.settings.start_pose.yaw),
                str(goal.x),
                str(goal.y),
                str(goal.yaw),
                str(self.timeout_seconds),
                str(self.settings.startup_timeout_seconds),
                self.settings.plan_topic,
                self.settings.node_expansions_topic,
            ]
        )
        return command


def cleanup_stray_ros_gazebo_processes(
    settings: RosGazeboSettings, project_root: Path
) -> None:
    """Force-kill any leftover gzserver/gzclient/roslaunch/rosmaster processes.

    Each individual evaluation already self-cleans via
    scripts/run_ros_gazebo_evaluation.sh's own exit trap, but that has been
    observed to occasionally miss a detached child; call this once after a
    whole batch of evaluations (a GP run, or a repeat-N comparison) finishes
    as a final sweep. Best-effort: failures here should never fail the run
    that just completed.
    """
    script = project_root / "scripts" / "cleanup_ros_gazebo.sh"
    command: list[str] = []
    if os.name == "nt":
        command.extend(["wsl.exe", "-d", settings.wsl_distribution, "--", "bash"])
    else:
        command.append("bash")
    command.append(_to_wsl_path(script))
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _mean_metric(results: list[EvaluationResult], attribute: str) -> float | None:
    values = [getattr(result, attribute) for result in results]
    numeric = [value for value in values if value is not None]
    return sum(numeric) / len(numeric) if numeric else None


def _to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix().split(":", maxsplit=1)[1].lstrip("/")
    return f"/mnt/{drive}/{tail}"
