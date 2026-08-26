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
    # Extra catkin workspaces (independent clones of `workspace`, see
    # scripts/setup_parallel_workspaces.sh) that let RosGazeboEvaluator run
    # multiple evaluations concurrently instead of serializing every
    # candidate through the same rastar.cpp. [workspace, *additional_workspaces]
    # forms the pool; empty here means the pool has exactly one slot and
    # everything behaves exactly as it did before this field existed.
    additional_workspaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationSettings:
    evaluator_type: str
    repetitions_per_goal: int
    timeout_seconds: int
    fixed_goal: GoalPose
    ros_gazebo: RosGazeboSettings | None = None
    # When set, each repetition samples a fresh goal x/y uniformly from these
    # ranges (yaw stays fixed_goal.yaw) instead of always using fixed_goal.
    # Tests that the evolved planner reaches a target zone, not one exact point.
    goal_x_range: tuple[float, float] | None = None
    goal_y_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class ValidationSettings:
    max_repair_attempts: int
    build_timeout_seconds: int
    forbid_dwa_changes: bool
    # Gate LLM-generated mutation/crossover output through
    # validate_meaningful_change() (see llm_gp/validation.py) so an edit that
    # only touches comments/whitespace or swaps a known trivial-equivalent
    # token pair (e.g. push_back<->emplace_back) is rejected and retried,
    # instead of silently accepted as if it were a real algorithm change.
    require_meaningful_change: bool = True
    min_change_ratio: float = 0.005
    # If every retry still fails validate_meaningful_change (but is
    # otherwise valid C++), fall back to a small deterministic jitter of
    # kLlmGpHeuristicWeight rather than discarding the attempt outright, so
    # the individual still carries *some* real numeric change.
    jitter_fallback_on_trivial_change: bool = True


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    fallback_to_mock: bool
    # Resilience for the OpenAI API calls mutation/crossover make during a
    # multi-hour unattended run. Only transient failures (connection errors,
    # timeouts, rate limits, 5xx) are retried, with exponential backoff from
    # connection_retry_base_seconds up to connection_retry_max_seconds; other
    # errors (bad API key, invalid request, ...) are never retried since
    # retrying can't fix them. See operators.py's _create_response_with_retry.
    connection_max_retries: int = 8
    connection_retry_base_seconds: float = 15.0
    connection_retry_max_seconds: float = 300.0


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
            goal_x_range=_load_range(evaluation.get("goal_x_range")),
            goal_y_range=_load_range(evaluation.get("goal_y_range")),
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
    if len(config.islands) < 1 or len(set(config.islands)) != len(config.islands):
        raise ValueError("At least one unique island is required")
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
    if not (0.0 < config.validation.min_change_ratio < 1.0):
        raise ValueError("validation.min_change_ratio must be between 0 and 1 (exclusive)")
    if config.llm.connection_max_retries < 0:
        raise ValueError("llm.connection_max_retries must be non-negative")
    if not (0.0 < config.llm.connection_retry_base_seconds <= config.llm.connection_retry_max_seconds):
        raise ValueError(
            "llm.connection_retry_base_seconds must be positive and at most connection_retry_max_seconds"
        )
    minimum_repetitions = 1 if config.evolution.smoke_mode else 3
    if config.evaluation.repetitions_per_goal < minimum_repetitions:
        raise ValueError(
            f"repetitions_per_goal must be at least {minimum_repetitions}"
        )
    if not all(math.isfinite(value) for value in config.evaluation.fixed_goal.as_tuple()):
        raise ValueError("fixed_goal must contain finite x, y, and yaw values")
    if config.evaluation.evaluator_type == "ros_gazebo" and config.evaluation.ros_gazebo is None:
        raise ValueError("evaluation.ros_gazebo is required for the ROS/Gazebo evaluator")
    if (config.evaluation.goal_x_range is None) != (config.evaluation.goal_y_range is None):
        raise ValueError("goal_x_range and goal_y_range must be set together")
    if config.evaluation.ros_gazebo is not None:
        settings = config.evaluation.ros_gazebo
        effective_workspaces = (settings.workspace, *settings.additional_workspaces)
        if len(set(effective_workspaces)) != len(effective_workspaces):
            # Two pool entries pointing at the same directory would let two
            # concurrent evaluations "acquire" what is physically the same
            # workspace at once, silently defeating WorkspacePool's mutual
            # exclusion and re-introducing the rastar.cpp race it exists to
            # prevent -- fail fast instead of racing intermittently later.
            raise ValueError(
                "evaluation.ros_gazebo.workspace and additional_workspaces must all be distinct paths"
            )


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
        additional_workspaces=tuple(str(item) for item in (raw.get("additional_workspaces") or ())),
    )


def _load_range(raw: list[float] | None) -> tuple[float, float] | None:
    if raw is None:
        return None
    low, high = (float(raw[0]), float(raw[1]))
    if not (math.isfinite(low) and math.isfinite(high)) or low > high:
        raise ValueError(f"Invalid range {raw}: expected [min, max] with min <= max")
    return (low, high)


def _default_node_expansions_topic(plan_topic: str) -> str:
    """Derive .../node_expansions from a plan topic ending in .../plan.

    Config files may set node_expansions_topic explicitly; this is only the
    fallback so existing ros_gazebo.yaml files keep loading unmodified.
    """
    if plan_topic.endswith("/plan"):
        return plan_topic[: -len("plan")] + "node_expansions"
    return plan_topic + "/node_expansions"
