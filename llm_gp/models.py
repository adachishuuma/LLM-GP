from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GoalPose:
    """A fixed goal pose in the map frame (x, y, yaw in radians)."""

    x: float
    y: float
    yaw: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.yaw)


@dataclass
class EvaluationResult:
    success: bool
    planning_time: float | None
    path_length: float | None
    arrival_time: float | None
    error_message: str | None = None
    raw_log_path: str | None = None
    # Node expansions counted inside the search loop itself (RAStarExpansion::
    # calculatePotentials' `cycle` counter). Unlike planning_time (wall-clock,
    # includes ROS/actionlib/message-passing overhead) this is immune to
    # scheduling jitter and directly reflects the algorithm's search cost.
    node_expansions: float | None = None


@dataclass
class LLMCallRecord:
    model_name: str
    prompt_text: str
    response_text: str
    success: bool
    error_message: str | None = None
    # Token usage reported by the provider for this call (None for mock
    # operators, or if the response carried no usage data), so 30+
    # generation runs can report actual LLM cost/consumption instead of
    # only call counts.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class Individual:
    individual_id: str
    generation: int
    current_island: str
    origin_island: str
    source_path: str
    parent_ids: list[str]
    operator_type: str
    planning_time: float | None = None
    path_length: float | None = None
    arrival_time: float | None = None
    node_expansions: float | None = None
    fitness: float | None = None
    valid: bool = True
    evaluation_success: bool = False
    error_message: str | None = None
    is_elite: bool = False
    selected_for_next_generation: bool = False
    random_seed: int | None = None
    change_history: list[str] = field(default_factory=list)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)


@dataclass(frozen=True)
class MutationContext:
    generation: int
    island_name: str
    source_directory: Path
    operator_type: str = "mutation_only"
    source_suffix: str = ".py"
    # Evaluated individuals to report metrics from when the thing actually
    # being mutated has no evaluation of its own yet (crossover_and_mutation:
    # the crossover intermediate is unevaluated, so its two parents are
    # passed here instead). Unused for mutation_only, where the individual
    # being mutated is itself already evaluated.
    reference_individuals: tuple[Individual, ...] = field(default_factory=tuple)


@dataclass
class GenerationSummary:
    generation: int
    generated_individuals: int
    migrations: int
    population_sizes: dict[str, int]
    best_fitness: dict[str, float]
