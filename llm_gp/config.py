from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import GoalPose


@dataclass(frozen=True)
class EvolutionSettings:
    max_generations: int
    population_size_per_island: int
    parent_pairs_per_island: int
    elite_count: int
    smoke_mode: bool = False


@dataclass(frozen=True)
class SelectionSettings:
    parent_method: str
    survivor_method: str
    epsilon: float
    avoid_same_parent_pair: bool


@dataclass(frozen=True)
class MigrationSettings:
    interval: int
    migrants_per_island: int
    migrant_selection: str
    topology: str
    reevaluate_migrants: bool


@dataclass(frozen=True)
class FitnessSettings:
    planning_weight: float
    path_weight: float
    arrival_weight: float
    planning_reference: float
    path_reference: float
    arrival_reference: float
    failure_fitness: float
    # Node-expansion count is the search-cost metric actually scored by
    # calculate_fitness (it replaces wall-clock planning_time, which is
    # dominated by ROS/Gazebo overhead on small maps and barely varies
    # between candidates). planning_time/planning_weight/planning_reference
    # are kept for logging and as a fallback when node_expansions is
    # unavailable (e.g. MockEvaluator runs predating this field, or old
    # *_repetition_*.json logs). Calibrate this against the baseline's own
    # observed node_expansions (see scripts/repeat10_compare.py) rather than
    # trusting the default below.
    expansion_reference: float = 2000.0


@dataclass(frozen=True)
class RosGazeboSettings:
    wsl_distribution: str
    workspace: str
    launch_file: Path
    package_name: str
    candidate_target: str
    robot_model: str
    gazebo_model_name: str
    start_pose: GoalPose
    startup_timeout_seconds: int
    plan_topic: str
    # Published by GlobalPlanner as std_msgs/Int32 right after each
    # calculatePotentials() call (see planner_core.cpp publishNodeExpansions).
    node_expansions_topic: str = ""


@dataclass(frozen=True)
class EvaluationSettings:
    evaluator_type: str
    repetitions_per_goal: int
    timeout_seconds: int
    fixed_goal: GoalPose
    ros_gazebo: RosGazeboSettings | None = None


@dataclass(frozen=True)
class ValidationSettings:
    max_repair_attempts: int
    build_timeout_seconds: int
    forbid_dwa_changes: bool


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    fallback_to_mock: bool


@dataclass(frozen=True)
class AppConfig:
    evolution: EvolutionSettings
    selection: SelectionSettings
    migration: MigrationSettings
    fitness: FitnessSettings
    validation: ValidationSettings
    evaluation: EvaluationSettings
    llm: LLMSettings
    database_path: Path
    source_directory: Path
    generation_csv: Path
    islands: tuple[str, ...]
    random_seed: int


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    root = config_path.parent.parent.resolve()

    def project_path(value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else root / candidate

    evaluation = raw["evaluation"]
    fixed_goal = evaluation.get("fixed_goal", [1.69, 0.954, -0.00143])
    island_config = raw["islands"]
    island_names = tuple(island_config["names"])
    if int(island_config["count"]) != len(island_names):
        raise ValueError("islands.count must match the number of island names")
    output = raw.get("output", {})
    database_path = project_path(raw["database"]["path"])
    cfg = AppConfig(
        evolution=EvolutionSettings(**raw["evolution"]),
        selection=SelectionSettings(**raw["selection"]),
        migration=MigrationSettings(**raw["migration"]),
        fitness=FitnessSettings(**raw["fitness"]),
        validation=ValidationSettings(**raw["validation"]),
        evaluation=EvaluationSettings(
            evaluator_type=str(evaluation["evaluator_type"]),
            repetitions_per_goal=int(evaluation["repetitions_per_goal"]),
            timeout_seconds=int(evaluation["timeout_seconds"]),
            fixed_goal=GoalPose(*map(float, fixed_goal)),
            ros_gazebo=_load_ros_gazebo(evaluation.get("ros_gazebo"), root),
        ),
        llm=LLMSettings(**raw["llm"]),
        database_path=database_path,
        source_directory=project_path(
            output.get("source_directory", str(database_path.with_suffix("")) + "_sources")
        ),
        generation_csv=project_path(
            output.get("generation_csv", str(database_path.with_suffix("")) + "_summary.csv")
        ),
        islands=island_names,
        random_seed=int(raw["random_seed"]),
    )
    _validate_config(cfg)
    return cfg


def _validate_config(config: AppConfig) -> None:
    if len(config.islands) != 4 or len(set(config.islands)) != 4:
        raise ValueError("Exactly four unique islands are required")
    if config.evolution.smoke_mode:
        if config.evolution.population_size_per_island < 1:
            raise ValueError("smoke population_size_per_island must be positive")
        if config.evolution.parent_pairs_per_island < 1:
            raise ValueError("smoke parent_pairs_per_island must be positive")
    else:
        if config.evolution.population_size_per_island != 10:
            raise ValueError("population_size_per_island must be 10")
        if config.evolution.parent_pairs_per_island != 5:
            raise ValueError("parent_pairs_per_island must be 5")
    if config.evolution.elite_count != 1:
        raise ValueError("elite_count must be 1")
    if config.selection.parent_method != "roulette":
        raise ValueError("parent_method must be roulette")
    if config.selection.survivor_method != "roulette_without_replacement":
        raise ValueError("survivor_method must be roulette_without_replacement")
    if not config.selection.avoid_same_parent_pair:
        raise ValueError("avoid_same_parent_pair must be true")
    if config.selection.epsilon <= 0 or not math.isfinite(config.selection.epsilon):
        raise ValueError("selection.epsilon must be finite and positive")
    if config.migration.interval != 10 or config.migration.migrants_per_island != 1:
        raise ValueError("Migration must copy one best individual every 10 generations")
    if config.migration.topology != "ring" or config.migration.migrant_selection != "best":
        raise ValueError("Only best-individual ring migration is supported")
    if config.validation.max_repair_attempts < 0:
        raise ValueError("max_repair_attempts must be non-negative")
    minimum_repetitions = 1 if config.evolution.smoke_mode else 3
    if config.evaluation.repetitions_per_goal < minimum_repetitions:
        raise ValueError(
            f"repetitions_per_goal must be at least {minimum_repetitions}"
        )
    if not all(math.isfinite(value) for value in config.evaluation.fixed_goal.as_tuple()):
        raise ValueError("fixed_goal must contain finite x, y, and yaw values")
    if config.evaluation.evaluator_type == "ros_gazebo" and config.evaluation.ros_gazebo is None:
        raise ValueError("evaluation.ros_gazebo is required for the ROS/Gazebo evaluator")


def _load_ros_gazebo(raw: dict[str, Any] | None, root: Path) -> RosGazeboSettings | None:
    if raw is None:
        return None
    launch_file = Path(raw["launch_file"])
    if not launch_file.is_absolute():
        launch_file = root / launch_file
    plan_topic = str(raw["plan_topic"])
    return RosGazeboSettings(
        wsl_distribution=str(raw["wsl_distribution"]),
        workspace=str(raw["workspace"]),
        launch_file=launch_file,
        package_name=str(raw["package_name"]),
        candidate_target=str(raw["candidate_target"]),
        robot_model=str(raw["robot_model"]),
        gazebo_model_name=str(raw["gazebo_model_name"]),
        start_pose=GoalPose(*map(float, raw["start_pose"])),
        startup_timeout_seconds=int(raw["startup_timeout_seconds"]),
        plan_topic=plan_topic,
        node_expansions_topic=str(
            raw.get("node_expansions_topic") or _default_node_expansions_topic(plan_topic)
        ),
    )


def _default_node_expansions_topic(plan_topic: str) -> str:
    """Derive .../node_expansions from a plan topic ending in .../plan.

    Config files may set node_expansions_topic explicitly; this is only the
    fallback so existing ros_gazebo.yaml files keep loading unmodified.
    """
    if plan_topic.endswith("/plan"):
        return plan_topic[: -len("plan")] + "node_expansions"
    return plan_topic + "/node_expansions"
