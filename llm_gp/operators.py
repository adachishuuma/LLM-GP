from __future__ import annotations

import os
import random
import re
import time
import warnings
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from .config import FitnessSettings, ValidationSettings
from .models import Individual, LLMCallRecord, MutationContext
from .validation import validate_cpp_source, validate_meaningful_change


def _create_response_with_retry(
    client: object,
    *,
    model: str,
    input: str,
    max_retries: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
):
    """Call client.responses.create(), retrying only transient failures
    (connection errors, timeouts, rate limits, 5xx server errors) with
    exponential backoff. Non-transient errors (bad API key, invalid request,
    etc.) are raised immediately since retrying can't fix them.

    Exists so a momentary network/API outage during a multi-hour unattended
    GP run doesn't crash the whole process and lose all progress -- this is
    exactly what happened to a real run (openai.APIConnectionError from a
    transient DNS failure killed the process after 3 of 5 generations).
    """
    from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

    delay = base_delay_seconds
    for attempt in range(max_retries + 1):
        try:
            return client.responses.create(model=model, input=input)
        except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
            if attempt == max_retries:
                raise
            detail = str(exc)
        except APIStatusError as exc:
            if attempt == max_retries or exc.status_code not in (408, 409, 429, 500, 502, 503, 504):
                raise
            detail = f"HTTP {exc.status_code}"
        print(
            f"OpenAI API call failed ({detail}); retrying in {delay:.0f}s "
            f"(attempt {attempt + 1}/{max_retries + 1})...",
            flush=True,
        )
        time.sleep(delay)
        delay = min(delay * 2, max_delay_seconds)
    raise AssertionError("unreachable: loop always returns or raises")


class IdFactory:
    def __init__(self) -> None:
        self._counter = 0

    def next(self, prefix: str = "ind") -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:06d}"


class CrossoverOperator(Protocol):
    def crossover(self, parent1: Individual, parent2: Individual) -> Individual: ...


class MutationOperator(Protocol):
    def mutate(self, individual: Individual, context: MutationContext) -> Individual: ...


import hashlib


def _write_source(
    directory: Path, individual_id: str, body: str, suffix: str = ".py"
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    candidate = directory / f"{individual_id}{suffix}"
    existing_matches = sorted(directory.glob(f"*{suffix}"))
    for path in existing_matches:
        if path.name == candidate.name:
            continue
        try:
            if path.read_text(encoding="utf-8") == body:
                return path
        except OSError:
            continue
    path = candidate
    path.write_text(body, encoding="utf-8")
    return path


def initial_source(directory: Path, individual_id: str, island_name: str) -> Path:
    body = (
        '"""Mock Relaxed A* individual used until ROS/Gazebo integration."""\n\n'
        f'ISLAND = "{island_name}"\n'
        'ALGORITHM = "relaxed_astar"\n\n'
        'def plan(start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:\n'
        '    return [start, goal]\n'
    )
    return _write_source(directory, individual_id, body)


_WEIGHT_DECLARATION = "constexpr float kLlmGpHeuristicWeight = {weight:.8f}f;"
_WEIGHT_PATTERN = re.compile(
    r"constexpr\s+float\s+kLlmGpHeuristicWeight\s*=\s*([0-9.]+)f\s*;"
)


def _cpp_with_weight(source: str, weight: float) -> str:
    weight = min(3.0, max(0.1, weight))
    declaration = _WEIGHT_DECLARATION.format(weight=weight)
    if _WEIGHT_PATTERN.search(source):
        return _WEIGHT_PATTERN.sub(declaration, source)
    namespace_marker = "namespace global_planner {"
    if namespace_marker not in source:
        raise ValueError("Baseline source is not the expected global_planner implementation")
    source = source.replace(namespace_marker, f"{namespace_marker}\n\n{declaration}", 1)
    if "RAStarExpansion::calculatePotentials" in source:
        # Two Relaxed A* implementations have existed: the current open-list
        # binary heap (queue_/RIndex) and the earlier std::set-based openSet
        # version. Support injecting the weight into either one.
        heap_expression = "distance * neutral_cost_ * tBreak"
        legacy_expression = "tBreak * heuristic_cost(neighbor, goal)"
        if heap_expression in source:
            return source.replace(
                heap_expression,
                "distance * neutral_cost_ * tBreak * kLlmGpHeuristicWeight",
                1,
            )
        if legacy_expression in source:
            return source.replace(
                legacy_expression,
                "tBreak * kLlmGpHeuristicWeight * heuristic_cost(neighbor, goal)",
                1,
            )
        raise ValueError("Relaxed A* heuristic expression was not found")
    queue_expression = "distance * neutral_cost_"
    if "AStarExpansion::calculatePotentials" in source and queue_expression in source:
        return source.replace(
            queue_expression,
            f"{queue_expression} * kLlmGpHeuristicWeight",
            1,
        )
    raise ValueError("Supported Relaxed A*/A* heuristic expression was not found")


def _cpp_weight(source: str) -> float:
    match = _WEIGHT_PATTERN.search(source)
    return float(match.group(1)) if match else 1.0


def initial_cpp_source(
    directory: Path, individual_id: str, baseline_source: Path
) -> Path:
    source = baseline_source.read_text(encoding="utf-8")
    return _write_source(
        directory, individual_id, _cpp_with_weight(source, 1.0), suffix=".cpp"
    )


def _queue_structure_hint(source: str) -> str:
    """Two Relaxed A* implementations have existed: the current open-list
    binary heap (queue_/RIndex) and the earlier std::set-based openSet
    version. Point the model at whichever data structure this candidate
    actually uses, shared by both the mutation and crossover prompts."""
    return (
        "queue_ is std::vector<RIndex>: use push_back/emplace_back, pop_heap and "
        "push_heap; never call queue_.top(), queue_.pop(), or one-argument "
        "queue_.emplace()."
        if "queue_" in source
        else "openSet is std::set<Node>: use insert()/erase() on it; Node has x,y "
        "fields. Do not introduce a queue_ member."
    )


def _metric_ratio(value: float | None, reference: float) -> str:
    if value is None:
        return "unknown"
    relation = "above" if value > reference else "at/below"
    return f"{value:.4g} (reference {reference:.4g}, {relation} reference)"


def _metrics_block(
    individuals: list[Individual], fitness_settings: FitnessSettings | None
) -> str:
    """Pure, network-free prompt fragment describing known evaluation
    results for one or more individuals, so mutation/crossover prompts can
    tell the model which metric has the most room to improve instead of
    editing blind. Falls back to a no-data notice when nothing is known yet
    (e.g. mutating a fresh crossover intermediate with no reference parents)."""
    if not individuals or fitness_settings is None:
        return (
            "No measured evaluation results are available for this candidate; "
            "use general Relaxed A* domain knowledge to choose an improvement."
        )
    lines = [
        "Known evaluation results (lower is better for every metric below; "
        "the metric currently furthest above its reference has the most room "
        "to improve fitness):"
    ]
    for individual in individuals:
        fitness_text = (
            f"{individual.fitness:.4f}" if individual.fitness is not None else "unevaluated"
        )
        lines.append(
            f"- {individual.individual_id} (fitness={fitness_text}): "
            f"node_expansions={_metric_ratio(individual.node_expansions, fitness_settings.expansion_reference)}, "
            f"path_length={_metric_ratio(individual.path_length, fitness_settings.path_reference)}, "
            f"arrival_time={_metric_ratio(individual.arrival_time, fitness_settings.arrival_reference)}"
        )
    lines.append(
        "Prioritize the metric that is furthest above its reference; do not regress the others."
    )
    return "\n".join(lines)


class MockCrossoverOperator:
    def __init__(self, ids: IdFactory, source_directory: Path) -> None:
        self.ids = ids
        self.source_directory = source_directory

    def crossover(self, parent1: Individual, parent2: Individual) -> Individual:
        individual_id = self.ids.next()
        body = (
            '"""Mock crossover child."""\n\n'
            f'PARENTS = {parent1.individual_id!r}, {parent2.individual_id!r}\n'
            'ALGORITHM = "relaxed_astar"\n\n'
            'def plan(start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:\n'
            '    midpoint = ((start[0] + goal[0]) / 2, (start[1] + goal[1]) / 2)\n'
            '    return [start, midpoint, goal]\n'
        )
        path = _write_source(self.source_directory, individual_id, body)
        return Individual(
            individual_id=individual_id,
            generation=parent1.generation,
            current_island=parent1.current_island,
            origin_island=parent1.origin_island,
            source_path=str(path),
            parent_ids=[parent1.individual_id, parent2.individual_id],
            operator_type="crossover_only",
            change_history=["Mock crossover combined two Relaxed A* parents"],
            llm_calls=[
                LLMCallRecord(
                    model_name="mock",
                    prompt_text="Combine two mock Relaxed A* parents",
                    response_text=body,
                    success=True,
                )
            ],
        )


class MockMutationOperator:
    def __init__(self, ids: IdFactory, rng: random.Random) -> None:
        self.ids = ids
        self.rng = rng

    def mutate(self, individual: Individual, context: MutationContext) -> Individual:
        individual_id = self.ids.next()
        tweak = self.rng.uniform(0.05, 0.25)
        body = (
            '"""Mock LLM-mutated Relaxed A* individual."""\n\n'
            f'PARENT = {individual.individual_id!r}\n'
            f'HEURISTIC_TWEAK = {tweak:.8f}\n'
            'ALGORITHM = "relaxed_astar"\n\n'
            'def plan(start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:\n'
            '    return [start, goal]\n'
        )
        path = _write_source(context.source_directory, individual_id, body)
        return Individual(
            individual_id=individual_id,
            generation=context.generation,
            current_island=context.island_name,
            origin_island=individual.origin_island,
            source_path=str(path),
            parent_ids=[individual.individual_id],
            operator_type=context.operator_type,
            change_history=[f"Mock LLM mutation set heuristic tweak to {tweak:.8f}"],
            llm_calls=[
                LLMCallRecord(
                    model_name="mock",
                    prompt_text="Apply a mock Relaxed A* mutation",
                    response_text=body,
                    success=True,
                )
            ],
        )


class CppRelaxedAStarCrossoverOperator:
    """Combines the heuristic weights of two Relaxed A* C++ candidates."""

    def __init__(self, ids: IdFactory, source_directory: Path) -> None:
        self.ids = ids
        self.source_directory = source_directory

    def crossover(self, parent1: Individual, parent2: Individual) -> Individual:
        source1 = Path(parent1.source_path).read_text(encoding="utf-8")
        source2 = Path(parent2.source_path).read_text(encoding="utf-8")
        weight = (_cpp_weight(source1) + _cpp_weight(source2)) / 2.0
        individual_id = self.ids.next()
        path = _write_source(
            self.source_directory,
            individual_id,
            _cpp_with_weight(source1, weight),
            suffix=".cpp",
        )
        return Individual(
            individual_id=individual_id,
            generation=parent1.generation,
            current_island=parent1.current_island,
            origin_island=parent1.origin_island,
            source_path=str(path),
            parent_ids=[parent1.individual_id, parent2.individual_id],
            operator_type="crossover_only",
            change_history=[f"Averaged Relaxed A* heuristic weights to {weight:.8f}"],
        )


class CppRelaxedAStarMutationOperator:
    """Applies a seeded mutation to the Relaxed A* heuristic weight."""

    def __init__(self, ids: IdFactory, rng: random.Random) -> None:
        self.ids = ids
        self.rng = rng

    def mutate(self, individual: Individual, context: MutationContext) -> Individual:
        source = Path(individual.source_path).read_text(encoding="utf-8")
        old_weight = _cpp_weight(source)
        new_weight = min(3.0, max(0.1, old_weight * self.rng.uniform(0.75, 1.25)))
        individual_id = self.ids.next()
        path = _write_source(
            context.source_directory,
            individual_id,
            _cpp_with_weight(source, new_weight),
            suffix=".cpp",
        )
        return Individual(
            individual_id=individual_id,
            generation=context.generation,
            current_island=context.island_name,
            origin_island=individual.origin_island,
            source_path=str(path),
            parent_ids=[individual.individual_id],
            operator_type=context.operator_type,
            change_history=[
                f"Changed Relaxed A* heuristic weight from {old_weight:.8f} to {new_weight:.8f}"
            ],
        )
_ALGORITHM_CHANGE_INSTRUCTION = (
    "Make one concrete change to the Relaxed A* SEARCH ALGORITHM itself -- e.g. the "
    "heuristic weighting/formula, the tie-break rule (tBreak), the cost-accumulation "
    "formula, the expansion/priority order, or a pruning condition -- that plausibly "
    "moves the metrics below in the right direction (lower node_expansions / "
    "path_length / arrival_time). Do not make a comment-only, whitespace-only edit, "
    "or a superficial API substitution (e.g. push_back->emplace_back) that does not "
    "change the algorithm's behavior."
)


class OpenAIGPT4oMiniMutationOperator:
    """Optional real mutation adapter; constructed only when an API key is available."""

    def __init__(
        self,
        ids: IdFactory,
        model: str = "gpt-4o-mini",
        max_retries: int = 1,
        client: object | None = None,
        fitness_settings: FitnessSettings | None = None,
        validation_settings: ValidationSettings | None = None,
        rng: random.Random | None = None,
        connection_max_retries: int = 8,
        connection_retry_base_seconds: float = 15.0,
        connection_retry_max_seconds: float = 300.0,
    ) -> None:
        self.ids = ids
        self.model = model
        self.max_retries = max(0, max_retries)
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.client = client
        self.fitness_settings = fitness_settings
        self.connection_max_retries = connection_max_retries
        self.connection_retry_base_seconds = connection_retry_base_seconds
        self.connection_retry_max_seconds = connection_retry_max_seconds
        self.validation_settings = validation_settings
        self.rng = rng or random.Random()

    def mutate(self, individual: Individual, context: MutationContext) -> Individual:
        source = Path(individual.source_path).read_text(encoding="utf-8")
        is_cpp = Path(individual.source_path).suffix == ".cpp"
        individual_id = self.ids.next()
        # The thing being mutated may itself be unevaluated (the crossover
        # intermediate in crossover_and_mutation) -- fall back to whatever
        # reference individuals generate_children() supplied (its parents).
        reference_individuals = (
            [individual]
            if individual.fitness is not None
            else list(context.reference_individuals)
        )
        metrics_block = _metrics_block(reference_individuals, self.fitness_settings)
        if is_cpp and "RAStarExpansion" in source:
            structure_hint = _queue_structure_hint(source)
            base_instruction = (
                "Return ONLY the complete C++ translation unit, starting with the existing "
                "license comment or #include. Do not use Markdown fences, introductory text, "
                "or an explanation after the code. "
                f"{_ALGORITHM_CHANGE_INSTRUCTION} "
                "Preserve global_planner::RAStarExpansion, both "
                "method signatures, all required includes, and kLlmGpHeuristicWeight. "
                f"{structure_hint} "
                "Do not modify or depend on DWA or any local planner.\n\n"
                f"{metrics_block}"
            )
        else:
            base_instruction = (
                "Return only valid Python source. Make one concrete, behavior-changing A* "
                "implementation improvement (not a cosmetic rename) while preserving a "
                f"callable plan(start, goal) function.\n\n{metrics_block}"
            )
        generated = source
        validation_error: str | None = None
        structural_valid = False
        valid = False
        attempts = 0
        call_records: list[LLMCallRecord] = []
        require_meaningful = bool(
            self.validation_settings and self.validation_settings.require_meaningful_change
        )
        min_change_ratio = (
            self.validation_settings.min_change_ratio if self.validation_settings else 0.005
        )
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            retry_instruction = (
                "\nYour previous response was rejected: "
                f"{validation_error}. Return a corrected complete source only."
                if validation_error
                else ""
            )
            request_input = base_instruction + retry_instruction + "\n\n" + source
            response = _create_response_with_retry(
                self.client,
                model=self.model,
                input=request_input,
                max_retries=self.connection_max_retries,
                base_delay_seconds=self.connection_retry_base_seconds,
                max_delay_seconds=self.connection_retry_max_seconds,
            )
            generated = _extract_generated_source(response.output_text, is_cpp)
            if not is_cpp:
                call_records.append(
                    LLMCallRecord(
                        self.model,
                        request_input,
                        response.output_text,
                        True,
                    )
                )
                valid = True
                break
            structural_valid, structural_error = validate_cpp_source(generated)
            if structural_valid and require_meaningful:
                valid, validation_error = validate_meaningful_change(
                    source, generated, min_change_ratio
                )
            else:
                valid, validation_error = structural_valid, structural_error
            call_records.append(
                LLMCallRecord(
                    self.model,
                    request_input,
                    response.output_text,
                    valid,
                    None if valid else validation_error,
                )
            )
            if valid:
                break
        change_history = [
            f"OpenAI {self.model} mutation ({attempts} generation attempt(s))"
        ]
        if (
            is_cpp
            and not valid
            and structural_valid
            and self.validation_settings
            and self.validation_settings.jitter_fallback_on_trivial_change
        ):
            # Structurally valid C++ that never cleared the meaningful-change
            # gate after every retry: guarantee at least a real numeric
            # change rather than shipping a comment-only/trivial edit.
            old_weight = _cpp_weight(generated)
            new_weight = min(3.0, max(0.1, old_weight * self.rng.uniform(0.85, 1.15)))
            generated = _cpp_with_weight(generated, new_weight)
            change_history.append(
                "Fell back to a deterministic heuristic-weight jitter "
                f"({old_weight:.8f} -> {new_weight:.8f}) after every attempt failed "
                "the meaningful-change check"
            )
        path = _write_source(
            context.source_directory,
            individual_id,
            generated + "\n",
            suffix=context.source_suffix,
        )
        return Individual(
            individual_id=individual_id,
            generation=context.generation,
            current_island=context.island_name,
            origin_island=individual.origin_island,
            source_path=str(path),
            parent_ids=[individual.individual_id],
            operator_type=context.operator_type,
            change_history=change_history,
            llm_calls=call_records,
        )


class OpenAIGPT4oMiniCrossoverOperator:
    """Optional real crossover adapter: asks the LLM to merge both evaluated
    parents' code (using their measured performance as guidance) into one
    candidate, instead of copying one parent verbatim and only averaging the
    heuristic weight. Falls back to that deterministic weight-average when
    the LLM can't produce a valid, non-trivial merge within the retry budget,
    so crossover always yields a buildable individual."""

    def __init__(
        self,
        ids: IdFactory,
        source_directory: Path,
        model: str = "gpt-4o-mini",
        max_retries: int = 1,
        client: object | None = None,
        fitness_settings: FitnessSettings | None = None,
        validation_settings: ValidationSettings | None = None,
        connection_max_retries: int = 8,
        connection_retry_base_seconds: float = 15.0,
        connection_retry_max_seconds: float = 300.0,
    ) -> None:
        self.ids = ids
        self.source_directory = source_directory
        self.model = model
        self.max_retries = max(0, max_retries)
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.client = client
        self.fitness_settings = fitness_settings
        self.validation_settings = validation_settings
        self.connection_max_retries = connection_max_retries
        self.connection_retry_base_seconds = connection_retry_base_seconds
        self.connection_retry_max_seconds = connection_retry_max_seconds

    def crossover(self, parent1: Individual, parent2: Individual) -> Individual:
        source1 = Path(parent1.source_path).read_text(encoding="utf-8")
        source2 = Path(parent2.source_path).read_text(encoding="utf-8")
        is_cpp = Path(parent1.source_path).suffix == ".cpp"
        individual_id = self.ids.next()
        metrics_block = _metrics_block([parent1, parent2], self.fitness_settings)
        if is_cpp and "RAStarExpansion" in source1:
            structure_hint = _queue_structure_hint(source1)
            base_instruction = (
                "You are given TWO candidate C++ Relaxed A* implementations of the same "
                "class (Parent A, Parent B) together with their measured performance below. "
                "Return ONLY ONE complete, valid C++ translation unit that merges whichever "
                "specific changes from EACH parent are most likely, given the metrics, to "
                "improve node_expansions/path_length/arrival_time -- keep the better idea "
                "from each side rather than discarding one parent's code wholesale, and do "
                "not simply return one parent unchanged. Do not use Markdown fences, "
                "introductory text, or an explanation after the code. Preserve "
                "global_planner::RAStarExpansion, both method signatures, all required "
                "includes, and exactly one kLlmGpHeuristicWeight declaration. "
                f"{structure_hint} Do not modify or depend on DWA or any local planner.\n\n"
                f"{metrics_block}"
            )
        else:
            base_instruction = (
                "You are given TWO candidate Python A* implementations (Parent A, Parent B). "
                "Return only valid Python source that merges the better ideas from each into "
                "one script, preserving a callable plan(start, goal) function; do not simply "
                f"return one parent unchanged.\n\n{metrics_block}"
            )
        generated = source1
        validation_error: str | None = None
        structural_valid = False
        valid = False
        attempts = 0
        call_records: list[LLMCallRecord] = []
        require_meaningful = bool(
            self.validation_settings and self.validation_settings.require_meaningful_change
        )
        min_change_ratio = (
            self.validation_settings.min_change_ratio if self.validation_settings else 0.005
        )
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            retry_instruction = (
                "\nYour previous response was rejected: "
                f"{validation_error}. Return a corrected complete merged source only."
                if validation_error
                else ""
            )
            request_input = (
                base_instruction
                + retry_instruction
                + f"\n\nParent A ({parent1.individual_id}):\n{source1}"
                + f"\n\nParent B ({parent2.individual_id}):\n{source2}"
            )
            response = _create_response_with_retry(
                self.client,
                model=self.model,
                input=request_input,
                max_retries=self.connection_max_retries,
                base_delay_seconds=self.connection_retry_base_seconds,
                max_delay_seconds=self.connection_retry_max_seconds,
            )
            generated = _extract_generated_source(response.output_text, is_cpp)
            if not is_cpp:
                call_records.append(
                    LLMCallRecord(self.model, request_input, response.output_text, True)
                )
                valid = True
                break
            structural_valid, structural_error = validate_cpp_source(generated)
            if structural_valid and require_meaningful:
                # Must diverge meaningfully from BOTH parents -- otherwise
                # this is just "copy one parent", the exact behavior being
                # replaced.
                ok_a, err_a = validate_meaningful_change(source1, generated, min_change_ratio)
                ok_b, err_b = validate_meaningful_change(source2, generated, min_change_ratio)
                valid = ok_a and ok_b
                validation_error = None if valid else (err_a or err_b)
            else:
                valid, validation_error = structural_valid, structural_error
            call_records.append(
                LLMCallRecord(
                    self.model,
                    request_input,
                    response.output_text,
                    valid,
                    None if valid else validation_error,
                )
            )
            if valid:
                break
        change_history = [
            f"OpenAI {self.model} crossover merge ({attempts} generation attempt(s))"
        ]
        if not valid:
            # Never produced a valid, non-trivial merge: fall back to the
            # deterministic weight-average of parent1 (same as
            # CppRelaxedAStarCrossoverOperator) so crossover always yields a
            # buildable individual.
            weight = (_cpp_weight(source1) + _cpp_weight(source2)) / 2.0
            generated = _cpp_with_weight(source1, weight)
            change_history = [
                f"OpenAI {self.model} crossover merge failed after {attempts} attempt(s) "
                f"({validation_error}); fell back to averaging heuristic weights to {weight:.8f}"
            ]
        path = _write_source(
            self.source_directory,
            individual_id,
            generated + "\n",
            suffix=".cpp" if is_cpp else ".py",
        )
        return Individual(
            individual_id=individual_id,
            generation=parent1.generation,
            current_island=parent1.current_island,
            origin_island=parent1.origin_island,
            source_path=str(path),
            parent_ids=[parent1.individual_id, parent2.individual_id],
            operator_type="crossover_only",
            change_history=change_history,
            llm_calls=call_records,
        )


_CODE_FENCE_PATTERN = re.compile(
    r"```[ \t]*(?:cpp|c\+\+|cc|cxx)?[ \t]*\r?\n(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)


def _extract_generated_source(response_text: str, is_cpp: bool) -> str:
    """Extract source even when the model surrounds a fence with prose."""
    text = response_text.strip()
    fenced_blocks = _CODE_FENCE_PATTERN.findall(text)
    if fenced_blocks:
        if is_cpp:
            matching = [block for block in fenced_blocks if "RAStarExpansion" in block]
            text = matching[0] if matching else fenced_blocks[0]
        else:
            text = fenced_blocks[0]
    return text.strip().lstrip("\ufeff")


def build_mutation_operator(
    provider: str,
    model: str,
    ids: IdFactory,
    rng: random.Random,
    cpp_mode: bool = False,
    max_retries: int = 1,
    fallback_to_mock: bool = True,
    fitness_settings: FitnessSettings | None = None,
    validation_settings: ValidationSettings | None = None,
    connection_max_retries: int = 8,
    connection_retry_base_seconds: float = 15.0,
    connection_retry_max_seconds: float = 300.0,
) -> MutationOperator:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", model)
    wants_openai = provider == "openai" or (provider == "auto" and bool(api_key))
    if wants_openai and api_key:
        try:
            return OpenAIGPT4oMiniMutationOperator(
                ids,
                model=model,
                max_retries=max_retries,
                fitness_settings=fitness_settings,
                validation_settings=validation_settings,
                rng=rng,
                connection_max_retries=connection_max_retries,
                connection_retry_base_seconds=connection_retry_base_seconds,
                connection_retry_max_seconds=connection_retry_max_seconds,
            )
        except (ImportError, KeyError):
            pass
    if wants_openai and not fallback_to_mock:
        raise RuntimeError("OpenAI mutation requested but OPENAI_API_KEY/SDK is unavailable")
    if wants_openai:
        warnings.warn(
            "OpenAI mutation is unavailable; falling back to a local mock mutation",
            RuntimeWarning,
            stacklevel=2,
        )
    return CppRelaxedAStarMutationOperator(ids, rng) if cpp_mode else MockMutationOperator(ids, rng)


def build_crossover_operator(
    provider: str,
    model: str,
    ids: IdFactory,
    source_directory: Path,
    cpp_mode: bool = False,
    max_retries: int = 1,
    fallback_to_mock: bool = True,
    fitness_settings: FitnessSettings | None = None,
    validation_settings: ValidationSettings | None = None,
    connection_max_retries: int = 8,
    connection_retry_base_seconds: float = 15.0,
    connection_retry_max_seconds: float = 300.0,
) -> CrossoverOperator:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", model)
    wants_openai = provider == "openai" or (provider == "auto" and bool(api_key))
    if wants_openai and api_key:
        try:
            return OpenAIGPT4oMiniCrossoverOperator(
                ids,
                source_directory,
                model=model,
                max_retries=max_retries,
                fitness_settings=fitness_settings,
                validation_settings=validation_settings,
                connection_max_retries=connection_max_retries,
                connection_retry_base_seconds=connection_retry_base_seconds,
                connection_retry_max_seconds=connection_retry_max_seconds,
            )
        except (ImportError, KeyError):
            pass
    if wants_openai and not fallback_to_mock:
        raise RuntimeError("OpenAI crossover requested but OPENAI_API_KEY/SDK is unavailable")
    if wants_openai:
        warnings.warn(
            "OpenAI crossover is unavailable; falling back to deterministic "
            "weight-averaging crossover",
            RuntimeWarning,
            stacklevel=2,
        )
    return (
        CppRelaxedAStarCrossoverOperator(ids, source_directory)
        if cpp_mode
        else MockCrossoverOperator(ids, source_directory)
    )


# Backward-compatible names for existing imports; real experiments use Relaxed A*.
CppAStarCrossoverOperator = CppRelaxedAStarCrossoverOperator
CppAStarMutationOperator = CppRelaxedAStarMutationOperator
