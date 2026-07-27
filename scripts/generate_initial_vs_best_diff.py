#!/usr/bin/env python3
"""
Generate unified diff, HTML side-by-side, and function-level summary
for the initial vs best individual in a run directory.

Usage:
  python scripts/generate_initial_vs_best_diff.py <run_dir_or_name>

If given a bare run name like `run_20260722_053957`, the script will search
under `experiment_results/` for a matching path.
"""
from pathlib import Path
import sys
import re
import difflib


def find_run_dir(arg: str) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    # search under experiment_results
    base = Path('experiment_results')
    for candidate in base.rglob(arg):
        if candidate.is_dir():
            return candidate
    # try pattern match
    for candidate in base.rglob('run_*'):
        if candidate.name == arg:
            return candidate
    raise FileNotFoundError(f'Run directory for "{arg}" not found under experiment_results')


def parse_report_for_ids(report_path: Path):
    text = report_path.read_text(encoding='utf-8', errors='ignore')
    # initial id: look for line like '初期基準個体: `ind_000001`'
    m_init = re.search(r'初期基準個体:\s*`(ind_\d{6})`', text)
    initial = m_init.group(1) if m_init else None
    # best id: take the last occurrence of an ind_###### in the report (heuristic)
    ids = re.findall(r'`(ind_\d{6})`', text)
    best = ids[-1] if ids else None
    return initial, best


def extract_functions(lines):
    funcs = []
    func_re = re.compile(r'^[A-Za-z_~][A-Za-z0-9_:<>\s\*&\~]*\([^;]*\)\s*\{\s*$')
    for i, line in enumerate(lines):
        if func_re.match(line.strip()):
            funcs.append((i + 1, line.strip()))
    return funcs


def find_enclosing(funcs, line_no):
    last = None
    for ln, sig in funcs:
        if ln <= line_no:
            last = (ln, sig)
        else:
            break
    return last


def generate(run_dir: Path):
    run_dir = Path(run_dir)
    analysis_dir = run_dir / 'analysis'
    analysis_dir.mkdir(parents=True, exist_ok=True)

    report = analysis_dir / 'generation_algorithm_report.md'
    if not report.exists():
        # fallback to run_manifest
        report = run_dir / 'analysis' / 'generation_algorithm_report.md'

    if report.exists():
        initial_id, best_id = parse_report_for_ids(report)
    else:
        initial_id = None
        best_id = None

    if initial_id is None or best_id is None:
        # try to infer from individual_comparison.csv
        ic = analysis_dir / 'individual_comparison.csv'
        if ic.exists():
            lines = ic.read_text(encoding='utf-8', errors='ignore').splitlines()
            # header at 0, entries follow; initial marked with 'initial' in 4th column
            initial = None
            best = None
            for row in lines[1:]:
                cols = row.split(',')
                if len(cols) > 4 and cols[3] == 'initial' and initial is None:
                    initial = cols[0]
                # pick any with generation > 0 as candidate for best; keep last
                if len(cols) > 6:
                    try:
                        gen = int(cols[1])
                    except Exception:
                        gen = 0
                    best = cols[0]
            initial_id = initial_id or initial
            best_id = best_id or best

    if initial_id is None or best_id is None:
        raise RuntimeError('Could not determine initial or best individual IDs from report or CSV')

    src_dir = run_dir / 'individual_sources'
    file_a = src_dir / f'{initial_id}.cpp'
    file_b = src_dir / f'{best_id}.cpp'

    # If reported IDs do not have source files, try to resolve candidates from CSV or available sources
    if not file_a.exists() or not file_b.exists():
        # try individual_comparison.csv to pick available IDs
        ic = analysis_dir / 'individual_comparison.csv'
        candidate_ids = []
        if ic.exists():
            for row in ic.read_text(encoding='utf-8', errors='ignore').splitlines()[1:]:
                cols = row.split(',')
                if cols:
                    candidate_ids.append(cols[0])

        # also list files present in individual_sources
        available = sorted([p.stem for p in src_dir.glob('ind_*.cpp')])

        def choose_existing(id_list):
            for cid in id_list:
                if (src_dir / f'{cid}.cpp').exists():
                    return cid
            return None

        if not file_a.exists():
            # try to choose initial from report, then candidate list, then available files
            chosen = choose_existing([initial_id] + candidate_ids + available)
            if chosen:
                file_a = src_dir / f'{chosen}.cpp'
                initial_id = chosen

        if not file_b.exists():
            # prefer candidate with highest reported fitness if CSV exists
            best_candidate = None
            if ic.exists():
                best_score = -1.0
                for row in ic.read_text(encoding='utf-8', errors='ignore').splitlines()[1:]:
                    cols = row.split(',')
                    try:
                        fitness = float(cols[6]) if len(cols) > 6 and cols[6] else -1.0
                    except Exception:
                        fitness = -1.0
                    cid = cols[0] if cols else None
                    if cid and (src_dir / f'{cid}.cpp').exists() and fitness > best_score:
                        best_score = fitness
                        best_candidate = cid
            if best_candidate:
                file_b = src_dir / f'{best_candidate}.cpp'
                best_id = best_candidate
            else:
                # fallback: pick last available file
                if available:
                    file_b = src_dir / f'{available[-1]}.cpp'
                    best_id = available[-1]

    if not file_a.exists() or not file_b.exists():
        raise FileNotFoundError('Individual source files not found after resolution: ' + str(file_a) + ' or ' + str(file_b))

    txt_a = file_a.read_text(encoding='utf-8', errors='ignore').splitlines()
    txt_b = file_b.read_text(encoding='utf-8', errors='ignore').splitlines()

    # unified diff
    udiff_path = analysis_dir / 'initial_vs_best.diff'
    with udiff_path.open('w', encoding='utf-8') as f:
        for line in difflib.unified_diff(txt_a, txt_b, fromfile=str(file_a), tofile=str(file_b), n=3):
            f.write(line + '\n')

    # html side-by-side
    html_path = analysis_dir / 'initial_vs_best.html'
    html = difflib.HtmlDiff(tabsize=4).make_file(txt_a, txt_b, fromdesc=str(file_a), todesc=str(file_b))
    html_path.write_text(html, encoding='utf-8')

    # function-level summary
    funcs_a = extract_functions(txt_a)
    funcs_b = extract_functions(txt_b)

    from difflib import SequenceMatcher
    sm = SequenceMatcher(a=txt_a, b=txt_b)
    changed_idx_b = set()
    changed_idx_a = set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ('replace', 'insert'):
            for j in range(j1, j2):
                changed_idx_b.add(j + 1)
        if tag in ('replace', 'delete'):
            for i in range(i1, i2):
                changed_idx_a.add(i + 1)

    func_changes = {}
    for ln in sorted(changed_idx_b):
        fn = find_enclosing(funcs_b, ln)
        if fn is None:
            fn = (0, '<top-level>')
        func_changes.setdefault(fn[1], {'start': fn[0], 'added': 0, 'removed': 0, 'lines': set()})
        func_changes[fn[1]]['added'] += 1
        func_changes[fn[1]]['lines'].add(ln)

    for ln in sorted(changed_idx_a):
        fn = find_enclosing(funcs_a, ln)
        if fn is None:
            fn = (0, '<top-level>')
        name = fn[1]
        if name not in func_changes:
            func_changes[name] = {'start': fn[0], 'added': 0, 'removed': 0, 'lines': set()}
        func_changes[name]['removed'] += 1

    summary_path = analysis_dir / 'initial_vs_best_functions.md'
    with summary_path.open('w', encoding='utf-8') as f:
        f.write('# Function-level change summary\n\n')
        f.write(f'Comparing {file_a.name} -> {file_b.name}\n\n')
        if not func_changes:
            f.write('No function-level changes detected.\n')
        else:
            for sig, info in func_changes.items():
                f.write(f'- **{sig}** (defined at line {info["start"]})\n')
                f.write(f'  - added_lines: {info["added"]}\n')
                f.write(f'  - removed_lines: {info["removed"]}\n')
                f.write(f'  - changed_line_numbers_in_new: {sorted(info["lines"])[:50]}\n')

    print('WROTE', udiff_path, html_path, summary_path)


def main():
    if len(sys.argv) < 2:
        print('Usage: generate_initial_vs_best_diff.py <run_dir_or_name>')
        sys.exit(2)
    arg = sys.argv[1]
    run_dir = find_run_dir(arg)
    generate(run_dir)


if __name__ == '__main__':
    main()
