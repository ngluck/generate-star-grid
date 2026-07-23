"""
Collect every failed track in a grid into one inspectable failure report.

A failed track is one that never produced its save file (grid_TAMS/TAMS_*.mod
for a main-sequence run). Re-running it unchanged will usually fail the same
way, so the useful action is not an automatic retry but a single document that
says what went wrong across the whole grid at once -- which reasons occurred,
how often, and which region of parameter space they cluster in.

Evidence is merged from three places, because no one of them is sufficient::

    the save file's absence        the failure signal itself
    LOGS/log_<dir>_TASK_<id>.txt   MESA's own verdict ('termination code: ...'),
                                   or, if it just stops, where it was cut off
    slurm_logs/*_<id>.out          the SLURM-level cause (time limit, OOM),
                                   which never appears in the MESA log

Categories, in the order they are decided::

    mesa_terminated  MESA printed a termination code but no save file -- it gave
                     up for a numerical/physical reason (e.g. min_timestep_limit)
    slurm_timeout    SLURM cancelled the task at its time limit
    slurm_oom        the task was killed for exceeding its memory allocation
    truncated        the MESA log stops mid-step with no verdict from either
                     side -- cut off by a time limit, node failure, or a kill
    no_mesa_output   the log is missing or empty: the task died before MESA
                     started, typically an environment or setup problem
    never_started    no log and no model directory -- the array task never ran
"""
import argparse
import datetime
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, Union

from .grid_utils import extract_constants_from_subdir_name

DEFAULT_SAVE_DIR = "grid_TAMS"
DEFAULT_SAVE_PREFIX = "TAMS_"
DEFAULT_SAVE_SUFFIX = ".mod"
DEFAULT_REPORT_NAME = "failure_report.txt"
DEFAULT_SLURM_LOG_DIR = "slurm_logs"

# Only the tail of a MESA log is read: everything that decides a category lives
# in the last few step blocks, and a large grid can hold thousands of logs.
TAIL_BYTES = 65536

_TERMINATION_RE = re.compile(r"termination code:\s*(.+)")
_STOPPING_RE = re.compile(r"stopping because (?:of )?(.+)")
_STEP_ROW_RE = re.compile(r"^\s*(\d+)\s+[-\d.]", re.M)
_TIMEOUT_RE = re.compile(r"DUE TO TIME LIMIT|TIME LIMIT\b", re.I)
_CANCELLED_RE = re.compile(r"\bCANCELLED\b", re.I)
_OOM_RE = re.compile(r"oom[-_ ]?kill|Out of memory|Exceeded job memory limit", re.I)


def save_file_path(
    parent_dir: Union[str, Path],
    folder: str,
    save_dir: str = DEFAULT_SAVE_DIR,
    save_prefix: str = DEFAULT_SAVE_PREFIX,
    save_suffix: str = DEFAULT_SAVE_SUFFIX,
) -> Path:
    """
    Return the path of the save file a completed model should have produced.

    Defaults describe a main-sequence run (grid_TAMS/TAMS_<dir>.mod, written by
    run_mesa_model on a genuine stop condition). Override the three components
    for a later evolutionary stage that saves elsewhere or under another prefix,
    so the completion test stays valid as the pipeline grows new stages.

    Args:
        parent_dir: Grid run directory.
        folder: The model's run directory name.
        save_dir: Subdirectory of parent_dir holding the save files.
        save_prefix: Filename prefix before the run directory name.
        save_suffix: Filename suffix (extension).

    Returns:
        Path to the expected save file (which may not exist).
    """
    return Path(parent_dir) / save_dir / f"{save_prefix}{folder}{save_suffix}"


def _read_tail(path: Path, max_bytes: int = TAIL_BYTES) -> str:
    """Return the last max_bytes of a text file, decoded leniently."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def find_slurm_log(parent_dir: Union[str, Path], task_id: int,
                   slurm_log_dir: str = DEFAULT_SLURM_LOG_DIR) -> Optional[Path]:
    """
    Return the most recent SLURM output file for an array task, if any.

    Matches the '<jobid>_<taskid>.out' naming that SLURM's %A_%a pattern
    produces. A task that was resubmitted has one file per attempt; the newest
    is the one that decided its fate.
    """
    d = Path(parent_dir) / slurm_log_dir
    if not d.is_dir():
        return None
    matches = list(d.glob(f"*_{task_id}.out"))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _slurm_verdict(slurm_log: Optional[Path]) -> Optional[tuple]:
    """Return (category, detail) if the SLURM log names a cause, else None."""
    if slurm_log is None:
        return None
    text = _read_tail(slurm_log)
    if _OOM_RE.search(text):
        return ("slurm_oom", "killed for exceeding its memory allocation")
    if _TIMEOUT_RE.search(text):
        return ("slurm_timeout", "cancelled at the SLURM time limit")
    if _CANCELLED_RE.search(text):
        return ("slurm_timeout", "cancelled by SLURM")
    return None


def classify_failure(
    parent_dir: Union[str, Path],
    folder: str,
    task_id: Optional[int] = None,
    log_path: Optional[Path] = None,
    slurm_log_dir: str = DEFAULT_SLURM_LOG_DIR,
) -> dict:
    """
    Work out why one model has no save file.

    Args:
        parent_dir: Grid run directory.
        folder: The model's run directory name.
        task_id: Array task id, used to locate the SLURM output file.
        log_path: The model's MESA log; derived from folder/task_id if omitted.
        slurm_log_dir: Subdirectory holding SLURM output files.

    Returns:
        Dict with 'category', 'reason' (a one-line human-readable cause),
        'last_model' (the last MESA step number seen, or None), 'log' and
        'slurm_log' (paths or None).
    """
    parent_dir = Path(parent_dir)
    if log_path is None and task_id is not None:
        log_path = parent_dir / "LOGS" / f"log_{folder}_TASK_{task_id}.txt"

    slurm_log = find_slurm_log(parent_dir, task_id, slurm_log_dir) if task_id is not None else None
    result = {
        "folder": folder,
        "task_id": task_id,
        "log": log_path,
        "slurm_log": slurm_log,
        "last_model": None,
    }

    has_log = log_path is not None and log_path.exists() and log_path.stat().st_size > 0
    tail = _read_tail(log_path) if has_log else ""

    if tail:
        steps = _STEP_ROW_RE.findall(tail)
        if steps:
            result["last_model"] = int(steps[-1])

    # MESA's own verdict wins: it ran, decided it could not continue, and said
    # why. That is a physical/numerical result, not an infrastructure problem.
    termination = _TERMINATION_RE.findall(tail)
    if termination:
        code = termination[-1].strip()
        result.update(category="mesa_terminated", reason=f"MESA terminated: {code}", code=code)
        return result

    stopping = _STOPPING_RE.findall(tail)
    if stopping:
        why = stopping[-1].strip()
        result.update(category="mesa_terminated", reason=f"MESA stopped: {why}", code=why)
        return result

    verdict = _slurm_verdict(slurm_log)
    if verdict:
        category, detail = verdict
        result.update(category=category, reason=detail, code=category)
        return result

    if has_log:
        # The step number varies per track, so it stays out of the reason (which
        # groups the report) and is carried per track in 'last_model'.
        result.update(
            category="truncated",
            reason="log ends mid-run with no termination code (cut off)",
            code="truncated",
        )
        return result

    if (parent_dir / folder).is_dir() or (log_path is not None and log_path.exists()):
        result.update(
            category="no_mesa_output",
            reason="no MESA output: the task died before MESA started",
            code="no_mesa_output",
        )
        return result

    result.update(category="never_started", reason="no log and no run directory",
                  code="never_started")
    return result


def collect_failures(
    parent_dir: Union[str, Path],
    keys: Optional[list] = None,
    save_dir: str = DEFAULT_SAVE_DIR,
    save_prefix: str = DEFAULT_SAVE_PREFIX,
    save_suffix: str = DEFAULT_SAVE_SUFFIX,
    slurm_log_dir: str = DEFAULT_SLURM_LOG_DIR,
) -> tuple:
    """
    Classify every task in a grid that never produced its save file.

    Completion is decided per model by the save file alone -- not by the size of
    history.data, which a track cut off mid-run can easily exceed while never
    reaching a stop condition.

    Args:
        parent_dir: Grid run directory containing LOGS/ and the save directory.
        keys: Parameter labels to extract from each failed directory name
            (default ['M', 'Y', 'Z', 'alpha']).
        save_dir, save_prefix, save_suffix: Where the completion save file lives
            (see save_file_path).
        slurm_log_dir: Subdirectory holding SLURM output files, if any.

    Returns:
        (failures, n_total, n_complete). 'failures' is a list of dicts from
        classify_failure, each with an added 'params' dict, sorted by task id.
    """
    parent_dir = Path(parent_dir)
    keys = keys if keys is not None else ["M", "Y", "Z", "alpha"]

    failures = []
    n_total = 0
    # Array tasks log to log_<dir>_TASK_<id>.txt; a local run (no --task_id)
    # logs to log_<dir>.txt. Both are collected, so the report covers a grid
    # run either way.
    for log in sorted(parent_dir.glob("LOGS/log_*.txt")):
        n_total += 1
        match = re.search(r"_TASK_(\d+)$", log.stem)
        task_id = int(match.group(1)) if match else None
        folder = re.sub(r"^log_", "", log.stem)
        if match:
            folder = re.sub(r"_TASK_\d+$", "", folder)
        if save_file_path(parent_dir, folder, save_dir, save_prefix, save_suffix).exists():
            continue
        record = classify_failure(parent_dir, folder, task_id, log_path=log,
                                  slurm_log_dir=slurm_log_dir)
        record["params"] = extract_constants_from_subdir_name(folder, keys)
        failures.append(record)

    failures.sort(key=lambda r: (r["task_id"] if r["task_id"] is not None else -1, r["folder"]))
    return failures, n_total, n_total - len(failures)


def _median(values: list) -> float:
    """Median of a non-empty list of floats."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _param_span(records: list, keys: list) -> str:
    """
    Summarize the parameter range a group of failures spans.

    The median is included because the range alone is rarely discriminating on a
    grid that samples the whole space -- a reason that afflicts one corner shows
    up as a median pulled well off the grid's centre, even when its range covers
    everything.
    """
    spans = []
    for key in keys:
        values = [r["params"][key] for r in records if key in r.get("params", {})]
        if not values:
            continue
        lo, hi = min(values), max(values)
        if lo == hi:
            spans.append(f"{key} {lo:g}")
        else:
            spans.append(f"{key} {lo:g}-{hi:g} (med {_median(values):g})")
    return ", ".join(spans) if spans else "n/a"


def format_report(
    parent_dir: Union[str, Path],
    failures: list,
    n_total: int,
    n_complete: int,
    keys: Optional[list] = None,
    max_detail_per_reason: Optional[int] = None,
) -> str:
    """
    Render the collected failures as one plain-text report.

    The report leads with a summary by reason and the parameter range each
    reason spans -- for a Sobol grid that is what shows whether failures cluster
    in one corner of parameter space -- then lists every failed track.

    Args:
        parent_dir: Grid run directory (recorded in the header).
        failures: Records from collect_failures.
        n_total: Tasks examined.
        n_complete: Tasks that produced their save file.
        keys: Parameter labels to show per track.
        max_detail_per_reason: Truncate each reason's per-track listing to this
            many entries (None lists all of them).

    Returns:
        The report text.
    """
    keys = keys if keys is not None else ["M", "Y", "Z", "alpha"]
    parent_dir = Path(parent_dir)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "=" * 78,
        "MESA GRID FAILURE REPORT",
        "=" * 78,
        f"Grid directory: {parent_dir}",
        f"Generated:      {now}",
        "",
    ]

    if n_total == 0:
        lines += ["No array task logs found in LOGS/ -- nothing to report.", ""]
        return "\n".join(lines)

    pct = 100.0 * n_complete / n_total
    lines += [
        f"Tasks examined: {n_total}",
        f"Completed:      {n_complete} ({pct:.1f}%)",
        f"Failed:         {len(failures)}",
        "",
    ]

    if not failures:
        lines += ["Every task produced its save file. No failures to report.", ""]
        return "\n".join(lines)

    groups = defaultdict(list)
    for record in failures:
        groups[record["reason"]].append(record)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    lines += ["SUMMARY BY REASON", "-" * 78]
    for reason, records in ordered:
        lines.append(f"{len(records):6d}  {reason}")
    lines += ["", "PARAMETER RANGE PER REASON", "-" * 78]
    for reason, records in ordered:
        lines.append(f"  {reason}")
        lines.append(f"      {_param_span(records, keys)}")
    lines += ["", "FAILED TRACKS", "-" * 78]

    for reason, records in ordered:
        lines += ["", f"### {reason}  ({len(records)})", ""]
        shown = records if max_detail_per_reason is None else records[:max_detail_per_reason]
        for record in shown:
            params = record.get("params", {})
            param_str = "  ".join(f"{k}={params[k]:g}" for k in keys if k in params)
            label = f"task {record['task_id']}  " if record["task_id"] is not None else ""
            lines.append(f"  {label}{record['folder']}")
            if param_str:
                lines.append(f"      {param_str}")
            if record.get("last_model"):
                lines.append(f"      last MESA model: {record['last_model']}")
            if record.get("log") is not None:
                lines.append(f"      log: {_relative(record['log'], parent_dir)}")
            if record.get("slurm_log") is not None:
                lines.append(f"      slurm: {_relative(record['slurm_log'], parent_dir)}")
        if max_detail_per_reason is not None and len(records) > max_detail_per_reason:
            lines.append(f"  ... and {len(records) - max_detail_per_reason} more")

    lines += ["", "=" * 78, ""]
    return "\n".join(lines)


def _relative(path: Path, parent_dir: Path) -> str:
    """Render path relative to parent_dir when it sits inside it."""
    try:
        return str(Path(path).relative_to(parent_dir))
    except ValueError:
        return str(path)


def write_failure_report(
    parent_dir: Union[str, Path],
    keys: Optional[list] = None,
    report_name: str = DEFAULT_REPORT_NAME,
    save_dir: str = DEFAULT_SAVE_DIR,
    save_prefix: str = DEFAULT_SAVE_PREFIX,
    save_suffix: str = DEFAULT_SAVE_SUFFIX,
    slurm_log_dir: str = DEFAULT_SLURM_LOG_DIR,
    max_detail_per_reason: Optional[int] = None,
    quiet: bool = False,
) -> Optional[Path]:
    """
    Collect every failure in a grid and write the report into the grid directory.

    Writing is unconditional once there are task logs to examine: a report
    stating that nothing failed is itself worth having next to the HDF5.

    Args:
        parent_dir: Grid run directory.
        keys: Parameter labels to extract from directory names.
        report_name: Filename to write inside parent_dir.
        save_dir, save_prefix, save_suffix: Completion save file location.
        slurm_log_dir: Subdirectory holding SLURM output files.
        max_detail_per_reason: Cap the per-track listing under each reason.
        quiet: Suppress the one-line stdout summary.

    Returns:
        Path to the report, or None if there were no task logs to examine.
    """
    parent_dir = Path(parent_dir)
    failures, n_total, n_complete = collect_failures(
        parent_dir, keys=keys, save_dir=save_dir, save_prefix=save_prefix,
        save_suffix=save_suffix, slurm_log_dir=slurm_log_dir,
    )
    if n_total == 0:
        if not quiet:
            print(f"No task logs in {parent_dir}/LOGS; no failure report written.")
        return None

    text = format_report(parent_dir, failures, n_total, n_complete, keys=keys,
                         max_detail_per_reason=max_detail_per_reason)
    out = parent_dir / report_name
    out.write_text(text)

    if not quiet:
        if failures:
            top = Counter(r["reason"] for r in failures).most_common(1)[0]
            print(f"{len(failures)} of {n_total} tasks failed "
                  f"(most common: {top[0]}, {top[1]}x). Report: {out}")
        else:
            print(f"All {n_total} tasks completed. Report: {out}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Collect every failed track in a grid into one failure report.",
    )
    parser.add_argument("--parent_dir", default=None,
                        help="Grid run directory (default: current directory).")
    parser.add_argument("--constants", nargs="*", default=["M", "Y", "Z", "alpha"],
                        help="Parameter labels to extract from directory names.")
    parser.add_argument("--report_name", default=DEFAULT_REPORT_NAME,
                        help=f"Filename written into the grid dir (default: {DEFAULT_REPORT_NAME}).")
    parser.add_argument("--save_dir", default=DEFAULT_SAVE_DIR,
                        help="Directory holding the completion save files "
                             f"(default: {DEFAULT_SAVE_DIR}; use grid_CONT for continuation runs).")
    parser.add_argument("--save_prefix", default=DEFAULT_SAVE_PREFIX,
                        help=f"Save filename prefix (default: {DEFAULT_SAVE_PREFIX}).")
    parser.add_argument("--save_suffix", default=DEFAULT_SAVE_SUFFIX,
                        help=f"Save filename suffix (default: {DEFAULT_SAVE_SUFFIX}).")
    parser.add_argument("--slurm_log_dir", default=DEFAULT_SLURM_LOG_DIR,
                        help="Subdirectory holding SLURM .out files.")
    parser.add_argument("--max_detail_per_reason", type=int, default=None,
                        help="Cap how many tracks are listed under each reason.")
    parser.add_argument("--stdout", action="store_true",
                        help="Print the report instead of writing it to a file.")
    args = parser.parse_args()

    _parent = Path(args.parent_dir) if args.parent_dir else Path.cwd()
    if args.stdout:
        _failures, _n_total, _n_complete = collect_failures(
            _parent, keys=args.constants, save_dir=args.save_dir,
            save_prefix=args.save_prefix, save_suffix=args.save_suffix,
            slurm_log_dir=args.slurm_log_dir,
        )
        print(format_report(_parent, _failures, _n_total, _n_complete, keys=args.constants,
                            max_detail_per_reason=args.max_detail_per_reason))
    else:
        write_failure_report(
            _parent, keys=args.constants, report_name=args.report_name,
            save_dir=args.save_dir, save_prefix=args.save_prefix,
            save_suffix=args.save_suffix, slurm_log_dir=args.slurm_log_dir,
            max_detail_per_reason=args.max_detail_per_reason,
        )
