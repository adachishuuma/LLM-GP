from __future__ import annotations

import ast
import difflib
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


_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"//.*")
_WHITESPACE_RUN = re.compile(r"\s+")
# License headers never change and would otherwise dilute the diff ratio, so
# comparisons start from the first namespace opening (the actual algorithm).
_NAMESPACE_MARKER = "namespace global_planner {"


def _normalize_cpp_for_diff(source: str) -> str:
    body = source
    marker_index = body.find(_NAMESPACE_MARKER)
    if marker_index != -1:
        body = body[marker_index:]
    stripped = _COMMENT_LINE.sub("", _COMMENT_BLOCK.sub("", body))
    return _WHITESPACE_RUN.sub(" ", stripped).strip()


# Token pairs that are semantically near-equivalent in the contexts this
# project's LLM operators actually produce (e.g. push_back(X(...)) vs
# emplace_back(X(...)) -- still constructs a temporary either way, so it is
# not a real optimization, just a rename). A diff-ratio threshold alone
# cannot distinguish "trivial rename" from "one real line of algorithm
# change" in a file this small (both can be well under 2% of the body), so
# these are canonicalized away before comparing rather than relying on size.
_TRIVIAL_TOKEN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("push_back", "emplace_back"),
    ("nullptr", "NULL"),
)
_TRIVIAL_TOKEN_PATTERN = {
    alias: re.compile(rf"\b{re.escape(alias)}\b")
    for group in _TRIVIAL_TOKEN_GROUPS
    for alias in group[1:]
}


def _canonicalize_trivial_tokens(text: str) -> str:
    for group in _TRIVIAL_TOKEN_GROUPS:
        canonical, *aliases = group
        for alias in aliases:
            text = _TRIVIAL_TOKEN_PATTERN[alias].sub(canonical, text)
    return text


def validate_meaningful_change(
    old_source: str, new_source: str, min_change_ratio: float = 0.005
) -> tuple[bool, str | None]:
    """Reject an LLM edit that has no real effect on the compiled algorithm.

    Two independent checks, both scoped to the code after the license header
    (see _NAMESPACE_MARKER) so that invariant boilerplate never counts as
    "changed":
      1. Exact match after stripping comments/whitespace -- catches
         comment-only or reformatting-only edits.
      2. Exact match after ALSO canonicalizing known trivial-equivalent
         token swaps (e.g. push_back<->emplace_back) -- catches superficial
         renames that a size-based diff ratio can't reliably separate from a
         genuine one-line algorithm change in a file this small.
    A loose diff-ratio backstop (default: reject only if under 0.5% of the
    body differs) catches near-total-no-ops not covered by 1/2, without
    being tight enough to reject a real single-line edit.

    This directly targets the failure mode observed in a real 10-generation
    run: the "best" individual's only change from the unmutated baseline was
    comment removal plus two push_back -> emplace_back swaps.
    """
    old_norm = _normalize_cpp_for_diff(old_source)
    new_norm = _normalize_cpp_for_diff(new_source)
    if old_norm == new_norm:
        return False, "Generated source is identical to the input aside from comments/whitespace"
    if _canonicalize_trivial_tokens(old_norm) == _canonicalize_trivial_tokens(new_norm):
        return False, (
            "Generated source only swaps known equivalent tokens "
            f"({', '.join(' / '.join(group) for group in _TRIVIAL_TOKEN_GROUPS)}) "
            "with no other change; make a real algorithmic change instead"
        )
    ratio = difflib.SequenceMatcher(None, old_norm, new_norm).ratio()
    if ratio >= (1.0 - min_change_ratio):
        return False, (
            f"Generated source differs from the input by only {(1 - ratio) * 100:.3f}% "
            f"after normalizing comments/whitespace (< {min_change_ratio * 100:.3f}% required); "
            "make a more substantive algorithmic change"
        )
    return True, None
