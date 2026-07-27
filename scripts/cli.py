#!/usr/bin/env python3
"""Simple CLI wrapper to call workspace skills via short commands.

Examples:
  python scripts/cli.py "/initial-vs run_20260722_053957"
  python scripts/cli.py "/initial-vs experiment_results/ten_generation_repeated/run_20260722_053957"
"""
import sys
from pathlib import Path


def cmd_initial_vs(arg: str):
    # delegate to generate_initial_vs_best_diff script
    from subprocess import run
    script = Path(__file__).resolve().parent / 'generate_initial_vs_best_diff.py'
    if not script.exists():
        print('generate_initial_vs_best_diff.py not found', file=sys.stderr)
        return 2
    return run([sys.executable, str(script), arg]).returncode


def main():
    if len(sys.argv) < 2:
        print('Usage: cli.py "/initial-vs <run>"')
        sys.exit(2)
    raw = ' '.join(sys.argv[1:]).strip()
    if raw.startswith('/initial-vs'):
        parts = raw.split(None, 1)
        if len(parts) != 2:
            print('Usage: /initial-vs <run_dir_or_name>')
            sys.exit(2)
        arg = parts[1].strip()
        rc = cmd_initial_vs(arg)
        sys.exit(rc)
    else:
        print('Unknown command', raw)
        sys.exit(3)


if __name__ == '__main__':
    main()
