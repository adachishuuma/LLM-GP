from __future__ import annotations

import ast
import math
import re
from pathlib import Path

from .models import Individual

_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}


def validate_individual(individual: Individual) -> tuple[bool, str | None]:
    path = Path(individual.source_path)
    if not path.is_file():
        return False, "Generated source file does not exist"
    if path.suffix == ".cpp":
        return _validate_cpp(path)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        compile(tree, str(path), "exec")
    except (OSError, SyntaxError, UnicodeError) as exc:
        return False, f"Source validation failed: {exc}"
    if any(token in source for token in ("dwa_local_planner", "DWAPlanner", "DWAPlannerROS")):
        return False, "DWA-related change detected"
    has_plan = any(isinstance(node, ast.FunctionDef) and node.name == "plan" for node in tree.body)
    if not has_plan:
        return False, "Required plan function is missing"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            return False, f"Forbidden call detected: {node.func.id}"
        if isinstance(node, ast.Constant) and isinstance(node.value, float) and not math.isfinite(node.value):
            return False, "Non-finite numeric literal detected"
    return True, None


def _validate_cpp(path: Path) -> tuple[bool, str | None]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return False, f"C++ source validation failed: {exc}"
    return validate_cpp_source(source)


def validate_cpp_source(source: str) -> tuple[bool, str | None]:
    """Validate a generated Relaxed A* translation unit before catkin build."""
    if "```" in source:
        return False, "Markdown code fence remains in C++ source"
    stripped = source.lstrip("\ufeff \t\r\n")
    if not stripped.startswith(("/*", "//", "#include", "namespace")):
        return False, "Explanatory text appears before the C++ source"
    required = (
        "namespace global_planner",
        "#include <global_planner/rastar.h>",
        "RAStarExpansion::calculatePotentials",
        "RAStarExpansion::add",
    )
    missing = [symbol for symbol in required if symbol not in source]
    if missing:
        return False, f"Required C++ symbol is missing: {', '.join(missing)}"
    forbidden = ("system(", "popen(", "fork(", "std::filesystem::remove")
    detected = [token for token in forbidden if token in source]
    if detected:
        return False, f"Forbidden C++ operation detected: {', '.join(detected)}"
    dwa_tokens = ("dwa_local_planner", "DWAPlanner", "DWAPlannerROS")
    detected_dwa = [token for token in dwa_tokens if token in source]
    if detected_dwa:
        return False, f"DWA-related change detected: {', '.join(detected_dwa)}"
    # Ignore braces in comments (rastar.cpp documents an old `if (...) {` line).
    syntax_only = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    syntax_only = re.sub(r"//.*", "", syntax_only)
    if syntax_only.count("{") != syntax_only.count("}"):
        return False, "C++ braces are unbalanced"
    lowered = source.lower()
    if "nan(" in lowered or "infinity" in lowered:
        return False, "Potential non-finite numeric operation detected"
    return True, None
