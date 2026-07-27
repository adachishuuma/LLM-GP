from __future__ import annotations

import os
import random
import re
import warnings
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from .models import Individual, LLMCallRecord, MutationContext
from .validation import validate_cpp_source


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
        heuristic_expression = "distance * neutral_cost_ * tBreak"
        if heuristic_expression not in source:
            raise ValueError("Relaxed A* heuristic expression was not found")
        return source.replace(
            heuristic_expression,
            "distance * neutral_cost_ * tBreak * kLlmGpHeuristicWeight",
            1,
        )
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
class OpenAIGPT4oMiniMutationOperator:
    """Optional real mutation adapter; constructed only when an API key is available."""

    def __init__(
        self,
        ids: IdFactory,
        model: str = "gpt-4o-mini",
        max_retries: int = 1,
        client: object | None = None,
    ) -> None:
        self.ids = ids
        self.model = model
        self.max_retries = max(0, max_retries)
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.client = client

    def mutate(self, individual: Individual, context: MutationContext) -> Individual:
        source = Path(individual.source_path).read_text(encoding="utf-8")
        is_cpp = Path(individual.source_path).suffix == ".cpp"
        individual_id = self.ids.next()
        base_instruction = (
            "Return ONLY the complete C++ translation unit, starting with the existing "
            "license comment or #include. Do not use Markdown fences, introductory text, "
            "or an explanation after the code. Make one small change to improve only the "
            "Relaxed A* implementation. Preserve global_planner::RAStarExpansion, both "
            "method signatures, all required includes, and kLlmGpHeuristicWeight. queue_ "
            "is std::vector<RIndex>: use push_back/emplace_back, pop_heap and push_heap; "
            "never call queue_.top(), queue_.pop(), or one-argument queue_.emplace(). "
            "Do not modify or depend on DWA or any local planner."
            if is_cpp and "RAStarExpansion" in source
            else "Return only valid Python source. Make one small, safe A* implementation "
            "improvement while preserving a callable plan(start, goal) function."
        )
        generated = source
        validation_error: str | None = None
        attempts = 0
        call_records: list[LLMCallRecord] = []
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            retry_instruction = (
                "\nYour previous response was rejected: "
                f"{validation_error}. Return a corrected complete source only."
                if validation_error
                else ""
            )
            request_input = base_instruction + retry_instruction + "\n\n" + source
            response = self.client.responses.create(
                model=self.model,
                input=request_input,
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
                break
            valid, validation_error = validate_cpp_source(generated)
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
            change_history=[
                f"OpenAI {self.model} mutation ({attempts} generation attempt(s))"
            ],
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
) -> MutationOperator:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", model)
    wants_openai = provider == "openai" or (provider == "auto" and bool(api_key))
    if wants_openai and api_key:
        try:
            return OpenAIGPT4oMiniMutationOperator(
                ids, model=model, max_retries=max_retries
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


# Backward-compatible names for existing imports; real experiments use Relaxed A*.
CppAStarCrossoverOperator = CppRelaxedAStarCrossoverOperator
CppAStarMutationOperator = CppRelaxedAStarMutationOperator
