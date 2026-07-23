"""
Discover the ordered save files a MESA run produces, and decide completion from
the last one.

A grid does not always stop at the main sequence. A run may continue past TAMS
and write several models along the way (ZAMS, TAMS, RGB, ...), or it may stop
somewhere else entirely and write a single model that is not named TAMS. In both
cases the question "did this track finish?" has the same answer: **did it write
its last save file?** A track that produced ZAMS and TAMS but never RGB has
failed, and every artifact it left behind is worth keeping.

This module turns a grid directory's inlists into an ordered list of Stages, one
per save file, so the rest of the pipeline can ask that question without knowing
anything about TAMS.

Discovery reads three things, because which mechanism a user reaches for is up
to them::

    rn                   the sequence of MESA invocations -- 'do_one <header>',
                         './star [<inlist>]', or 'cp <x> inlist' then './star'.
                         This fixes the stage ORDER.
    the inlist chain     each invocation's inlist, plus anything it pulls in via
                         extra_star_job_inlist_name. Within one invocation the
                         deepest file that declares a save wins, matching how
                         MESA overrides a base inlist with an extra one.
    save declarations    every save_model_filename in that file, in file order.
                         Several in one file means several stages.

Each save name yields a stem (``TAMS_0.70.mod`` -> ``TAMS``), which names both
the archive directory and the per-model file: ``grid_TAMS/TAMS_<run_dir>.mod``.
That is exactly the layout the pipeline has always used for a single-stage run,
so a template declaring one ``save_model_filename = 'TAMS_0.70.mod'`` resolves to
today's behaviour unchanged.

Resolution order (see resolve_stages)::

    1. an explicit --stages spec
    2. stages.json in the grid directory, written at setup -- it outlives the
       inlists, which cleanup deletes
    3. discovery, as above
    4. the legacy single TAMS stage

Inspect what a grid directory resolves to::

    python -m generate_star_grid.stages --parent_dir /path/to/my_grid
"""
import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Union

DEFAULT_SAVE_SUFFIX = ".mod"
STAGES_FILENAME = "stages.json"

# The single stage every grid built before multi-stage support used, and the
# fallback when nothing can be discovered.
LEGACY_STEM = "TAMS"

_SAVE_RE = re.compile(r"save_model_filename\s*=\s*['\"]([^'\"]+)['\"]")
# MESA has spelled this both extra_star_job_inlist_name(1) (r15140+) and
# extra_star_job_inlist1_name (older); accept either.
_EXTRA_INLIST_RE = re.compile(
    r"extra_star_job_inlist\d*_name\s*(?:\(\s*\d+\s*\))?\s*=\s*['\"]([^'\"]+)['\"]"
)
_DO_ONE_RE = re.compile(r"^\s*do_one\s+(\S+)")
_STAR_RE = re.compile(r"^\s*(?:\./)?star\b\s*(\S+)?")
_CP_INLIST_RE = re.compile(r"^\s*cp\s+(?:-\S+\s+)*(\S+)\s+inlist\s*$")
# A trailing '_<number>' is a mass or similar tag, not part of the stage name:
# TAMS_0.70 -> TAMS, but 15M_at_TAMS and final are left alone.
_TRAILING_NUMBER_RE = re.compile(r"_\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class Stage:
    """One save file a run is expected to produce."""

    #: Stage name, e.g. 'TAMS'. Names both the archive directory and the
    #: per-model filename prefix.
    stem: str
    #: Subdirectory of the grid directory holding this stage's saves.
    save_dir: str
    #: Filename prefix before the run directory name.
    prefix: str
    #: Filename suffix (extension).
    suffix: str = DEFAULT_SAVE_SUFFIX
    #: Inlist file that declared it, relative to the grid directory.
    inlist: Optional[str] = None
    #: '<file>:<line>' where it was found, for diagnostics.
    source: Optional[str] = None

    @classmethod
    def from_stem(cls, stem: str, save_dir: Optional[str] = None,
                  suffix: str = DEFAULT_SAVE_SUFFIX, inlist: Optional[str] = None,
                  source: Optional[str] = None) -> "Stage":
        """Build a Stage from a stem using the standard grid_<stem>/<stem>_<dir> layout."""
        return cls(stem=stem, save_dir=save_dir or f"grid_{stem}", prefix=f"{stem}_",
                   suffix=suffix, inlist=inlist, source=source)

    def filename(self, folder: str) -> str:
        """The save filename for one model directory, e.g. ``TAMS_M_1.000_Y_0.270.mod``."""
        return f"{self.prefix}{folder}{self.suffix}"


def legacy_stages() -> list:
    """The single-TAMS stage list matching every grid built before this module."""
    return [Stage.from_stem(LEGACY_STEM, source="legacy default")]


def stem_for_save_name(name: str) -> str:
    """
    Derive a stage stem from a MESA save_model_filename value.

    Strips any directory part, the extension, and a trailing '_<number>' tag
    (a mass, typically), then replaces anything that is not alphanumeric so the
    stem is safe as both a directory name and a filename prefix::

        TAMS_0.70.mod  -> TAMS
        final.mod      -> final
        RGB_tip.mod    -> RGB_tip
        15M_at_TAMS.mod -> 15M_at_TAMS

    Args:
        name: The value of a save_model_filename declaration.

    Returns:
        The stage stem.
    """
    stem = Path(name.strip()).name
    stem = re.sub(r"\.mod$", "", stem, flags=re.IGNORECASE)
    trimmed = _TRAILING_NUMBER_RE.sub("", stem)
    if trimmed:
        stem = trimmed
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_")
    return stem or LEGACY_STEM


def stage_save_path(parent_dir: Union[str, Path], stage: Stage, folder: str) -> Path:
    """Full path to one model's save file for a given stage."""
    return Path(parent_dir) / stage.save_dir / stage.filename(folder)


def completion_stage(stages: list) -> Stage:
    """The stage whose save file marks a finished track: the last one."""
    return stages[-1]


def reached_stage_index(parent_dir: Union[str, Path], folder: str, stages: list) -> int:
    """
    Index of the furthest stage this model reached, or -1 if it reached none.

    Stages are checked in order and the highest index with a save file on disk
    is returned, so a run whose middle save was cleaned up by hand still reports
    the furthest evidence that survives.

    Args:
        parent_dir: Grid run directory.
        folder: Model run directory name.
        stages: Ordered stage list.

    Returns:
        Index into stages, or -1.
    """
    reached = -1
    for i, stage in enumerate(stages):
        if stage_save_path(parent_dir, stage, folder).exists():
            reached = i
    return reached


def reached_stem(parent_dir: Union[str, Path], folder: str, stages: list) -> Optional[str]:
    """Stem of the furthest stage this model reached, or None if it reached none."""
    idx = reached_stage_index(parent_dir, folder, stages)
    return stages[idx].stem if idx >= 0 else None


def is_complete(parent_dir: Union[str, Path], folder: str, stages: list) -> bool:
    """True if this model produced its last stage's save file."""
    return stage_save_path(parent_dir, completion_stage(stages), folder).exists()


def parse_stage_spec(spec: Union[str, list]) -> list:
    """
    Parse a --stages value into an ordered stage list.

    Accepts a comma-separated string or an already-split list. Each entry is a
    stem, optionally with an archive directory after a colon::

        ZAMS,TAMS,RGB          -> grid_ZAMS/, grid_TAMS/, grid_RGB/
        TAMS,RGB:grid_giants   -> grid_TAMS/, grid_giants/

    Args:
        spec: The --stages value.

    Returns:
        Ordered list of Stages.

    Raises:
        ValueError: If the spec is empty or names the same stem twice.
    """
    if isinstance(spec, str):
        items = [s.strip() for s in spec.split(",")]
    else:
        items = [s.strip() for item in spec for s in str(item).split(",")]
    items = [s for s in items if s]
    if not items:
        raise ValueError("--stages is empty; expected e.g. --stages ZAMS,TAMS,RGB")

    stages = []
    for item in items:
        stem, _, save_dir = item.partition(":")
        stem = stem.strip()
        if not stem:
            raise ValueError(f"Bad --stages entry '{item}': missing stage name.")
        stages.append(Stage.from_stem(stem, save_dir=save_dir.strip() or None,
                                      source="--stages"))
    _check_unique(stages)
    return stages


def _check_unique(stages: list) -> None:
    """Raise if two stages share a stem, which would make them overwrite each other."""
    seen = {}
    for stage in stages:
        if stage.stem in seen:
            raise ValueError(
                f"Two stages resolve to the same name '{stage.stem}' "
                f"({seen[stage.stem]} and {stage.source}). Their save files would "
                f"overwrite each other. Name them explicitly, e.g. "
                f"--stages ZAMS,TAMS,RGB"
            )
        seen[stage.stem] = stage.source


def _parse_rn(rn_path: Path) -> list:
    """
    Return the ordered inlists the rn script runs MESA on.

    Recognizes the MESA test_suite helper ('do_one <header> ...'), a direct
    invocation ('./star' or './star <inlist>'), and the hand-rolled pattern of
    copying a stage inlist over 'inlist' before each run.

    Args:
        rn_path: Path to the grid directory's rn script.

    Returns:
        One entry per MESA invocation: the inlist name, or None for whichever
        file MESA reads by default ('inlist'). Empty if rn runs MESA never.
    """
    invocations = []
    pending = None
    for raw in rn_path.read_text(errors="replace").splitlines():
        line = raw.split("#", 1)[0]
        do_one = _DO_ONE_RE.match(line)
        if do_one:
            invocations.append(do_one.group(1))
            pending = None
            continue
        cp = _CP_INLIST_RE.match(line)
        if cp:
            pending = cp.group(1)
            continue
        star = _STAR_RE.match(line)
        if star:
            invocations.append(star.group(1) or pending)
            pending = None
    return invocations


def _resolve_inlist(name: Optional[str], mesa_dir: Path,
                    template_file: Optional[Path]) -> Optional[Path]:
    """
    Locate an inlist referenced by rn or by another inlist.

    'inlist_project' resolves to the template file, because inlist_project is
    what the pipeline *writes* into each run directory -- in the grid directory
    the corresponding source is inlist_template.

    Args:
        name: Referenced filename, or None for MESA's default 'inlist'.
        mesa_dir: Grid directory to resolve against.
        template_file: The grid's inlist template, if known.

    Returns:
        An existing path, or None if the reference cannot be resolved.
    """
    if name is None:
        name = "inlist"
    if name == "inlist_project" and template_file and Path(template_file).is_file():
        return Path(template_file)
    candidate = mesa_dir / name
    if candidate.is_file():
        return candidate
    if template_file and Path(template_file).is_file():
        return Path(template_file)
    return None


def _saves_in_file(path: Path) -> list:
    """Every save_model_filename in one file, in file order, as (name, 'file:line')."""
    found = []
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        if line.lstrip().startswith("!"):
            continue
        for match in _SAVE_RE.finditer(line.split("!", 1)[0]):
            found.append((match.group(1), f"{path.name}:{lineno}"))
    return found


def _inlist_chain(start: Path, mesa_dir: Path, template_file: Optional[Path]) -> list:
    """
    The files MESA reads for one invocation, in read order: base, then extras.

    Follows extra_star_job_inlist_name references, guarding against cycles.
    """
    chain, seen, queue = [], set(), [start]
    while queue:
        path = queue.pop(0)
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        chain.append(path)
        text = path.read_text(errors="replace")
        for name in _EXTRA_INLIST_RE.findall(text):
            nxt = _resolve_inlist(name, mesa_dir, template_file)
            if nxt is not None and nxt.resolve() not in seen:
                queue.append(nxt)
    return chain


def _saves_for_invocation(start: Path, mesa_dir: Path,
                          template_file: Optional[Path]) -> list:
    """
    The save declarations in effect for one MESA invocation.

    An extra inlist overrides the base it was pulled into, so the deepest file
    in the chain that declares any save wins. Several declarations *within* that
    one file are several stages.
    """
    effective = []
    for path in _inlist_chain(start, mesa_dir, template_file):
        saves = _saves_in_file(path)
        if saves:
            effective = [(name, src, path) for name, src in saves]
    return effective


def discover_stages(mesa_dir: Union[str, Path],
                    template_file: Optional[Union[str, Path]] = None) -> list:
    """
    Work out the ordered save files a grid directory's run will produce.

    Args:
        mesa_dir: Grid directory holding rn, inlist and inlist_template.
        template_file: The inlist template. Defaults to mesa_dir/inlist_template.

    Returns:
        Ordered list of Stages, empty if nothing could be discovered.

    Raises:
        ValueError: If two discovered stages resolve to the same stem.
    """
    mesa_dir = Path(mesa_dir)
    if template_file is None:
        candidate = mesa_dir / "inlist_template"
        template_file = candidate if candidate.is_file() else None
    template_file = Path(template_file) if template_file else None

    rn = mesa_dir / "rn"
    invocations = _parse_rn(rn) if rn.is_file() else []
    if not invocations:
        # No rn, or an rn that never calls MESA: assume the single default run.
        invocations = [None]

    stages = []
    for name in invocations:
        start = _resolve_inlist(name, mesa_dir, template_file)
        if start is None:
            continue
        for save_name, source, path in _saves_for_invocation(start, mesa_dir, template_file):
            stages.append(Stage.from_stem(
                stem_for_save_name(save_name),
                inlist=path.name,
                source=source,
            ))

    _check_unique(stages)
    return stages


def rename_save_declarations(text: str, folder: str, stages: list, offset: int = 0) -> tuple:
    """
    Point each save_model_filename in an inlist at its stage's per-model file.

    The n-th uncommented declaration becomes stages[offset + n]'s filename, so a
    run writes ``grid_TAMS/TAMS_<folder>.mod``-style names that carry the model
    directory in them. Declarations past the end of the stage list are left
    alone rather than silently collapsed onto one name.

    Args:
        text: Inlist text.
        folder: Run directory name to embed in each save filename.
        stages: Ordered stage list.
        offset: How many stages earlier files in this run already consumed.

    Returns:
        (new_text, n_renamed).
    """
    idx = offset

    def _replace(match):
        nonlocal idx
        current = idx
        idx += 1
        if current >= len(stages):
            return match.group(0)
        return f"save_model_filename = '{stages[current].filename(folder)}'"

    out = []
    for line in text.splitlines(keepends=True):
        # A '!' starts a MESA comment; a commented-out declaration is not a
        # stage, and must not consume one.
        code, sep, comment = line.partition("!")
        if _SAVE_RE.search(code):
            code = _SAVE_RE.sub(_replace, code)
        out.append(code + sep + comment)
    return "".join(out), idx - offset


def stage_inlist_order(stages: list) -> list:
    """Unique inlist filenames that declare stages, in stage order."""
    order = []
    for stage in stages:
        if stage.inlist and stage.inlist not in order:
            order.append(stage.inlist)
    return order


def is_single_template_stage(stages: list, template_name: str) -> bool:
    """
    True when the stage list is the classic one-save-in-the-template layout.

    Used to keep single-stage runs writing exactly the files they always have:
    only when this is False does a run need every inlist copied into its run
    directory.
    """
    if len(stages) != 1:
        return False
    declared = stages[0].inlist
    return declared is None or declared == template_name


def unreachable_stage_inlists(mesa_dir: Union[str, Path], stages: list,
                              template_file: Optional[Union[str, Path]] = None) -> list:
    """
    Find inlists that declare a save no discovered stage covers.

    Discovery follows what ``rn`` actually runs, so a stage inlist that ``rn``
    never references is invisible to it. That failure mode is quiet and
    expensive: the grid runs fewer stages than intended and calls every track
    finished one stage early. This spots it so the caller can say so.

    Args:
        mesa_dir: Grid directory to scan.
        stages: The stages discovery did find.
        template_file: The grid's inlist template, if not mesa_dir/inlist_template.

    Returns:
        (filename, save name) pairs for each unclaimed declaration, sorted.
    """
    mesa_dir = Path(mesa_dir)
    if template_file is None:
        candidate = mesa_dir / "inlist_template"
        template_file = candidate if candidate.is_file() else None

    known_stems = {s.stem for s in stages}
    # 'inlist' is the chain root and 'inlist_project' is the per-run copy the
    # pipeline generates from the template -- neither is a stage inlist someone
    # forgot to wire up.
    skip = {"inlist", "inlist_pgstar", "inlist_project"}
    skip.update(s.inlist for s in stages if s.inlist)
    if template_file:
        skip.add(Path(template_file).name)

    found = []
    for path in sorted(mesa_dir.glob("inlist*")):
        if not path.is_file() or path.name in skip:
            continue
        try:
            saves = _saves_in_file(path)
        except OSError:
            continue
        for name, _source in saves:
            if stem_for_save_name(name) not in known_stems:
                found.append((path.name, name))
    return found


def warn_unreachable(mesa_dir: Union[str, Path], stages: list,
                     template_file: Optional[Union[str, Path]] = None) -> list:
    """Print a warning for each unclaimed stage inlist; returns what it found."""
    found = unreachable_stage_inlists(mesa_dir, stages, template_file)
    if not found:
        return found
    print(f"WARNING: {len(found)} inlist declaration(s) in {mesa_dir} are not reached by rn "
          f"and will NOT become stages:")
    for filename, save_name in found:
        print(f"  {filename}: save_model_filename = '{save_name}'")
    print("  Discovery follows the runs rn actually performs. Either reference these "
          "inlists from rn, or name the stages explicitly, e.g.")
    print(f"    --stages {','.join([s.stem for s in stages] + [stem_for_save_name(n) for _, n in found])}")
    return found


def save_stages(parent_dir: Union[str, Path], stages: list) -> Path:
    """
    Write the resolved stages to parent_dir/stages.json.

    Persisted at grid setup so downstream tools agree with what the array tasks
    actually did, including after cleanup has deleted the inlists discovery reads.

    Args:
        parent_dir: Grid run directory.
        stages: Ordered stage list.

    Returns:
        Path to the written file.
    """
    path = Path(parent_dir) / STAGES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "stages": [asdict(s) for s in stages]}, indent=2))
    return path


def load_stages(parent_dir: Union[str, Path]) -> Optional[list]:
    """Read parent_dir/stages.json, or None if it is absent or unreadable."""
    path = Path(parent_dir) / STAGES_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    entries = data.get("stages") if isinstance(data, dict) else None
    if not entries:
        return None
    return [Stage(**{k: v for k, v in e.items() if k in Stage.__dataclass_fields__})
            for e in entries]


def resolve_stages(parent_dir: Union[str, Path],
                   template_file: Optional[Union[str, Path]] = None,
                   explicit: Optional[Union[str, list]] = None,
                   mesa_dir: Optional[Union[str, Path]] = None,
                   quiet: bool = True) -> list:
    """
    Resolve the stage list for a grid directory.

    Tries, in order: an explicit --stages spec, a persisted stages.json,
    discovery from the inlists, and finally the legacy single TAMS stage. The
    fallback means a grid with no inlists left (or none that declare a save)
    behaves exactly as it did before multi-stage support existed.

    Args:
        parent_dir: Grid run directory (where stages.json and grid_<stem>/ live).
        template_file: Inlist template, if it is not parent_dir/inlist_template.
        explicit: A --stages value, which wins over everything else.
        mesa_dir: Directory holding rn/inlist, if not parent_dir.
        quiet: Suppress the notes printed when the legacy guard fires or an
            inlist declares a save that rn never reaches. Callers that set this
            up (the grid submitters, the stages CLI) pass False so the user sees
            them once, rather than once per array task.

    Returns:
        Ordered list of Stages, never empty.
    """
    if explicit:
        return parse_stage_spec(explicit)

    stored = load_stages(parent_dir)
    if stored:
        return stored

    parent_dir = Path(parent_dir)
    try:
        stages = discover_stages(mesa_dir or parent_dir, template_file)
    except ValueError:
        raise
    except OSError:
        stages = []

    if not stages:
        return legacy_stages()

    # A grid built before this module already archives into grid_TAMS/. Renaming
    # its stage mid-flight would strand every save file it has written, so the
    # existing directory wins and the user is told why.
    if (len(stages) == 1 and stages[0].stem != LEGACY_STEM
            and (parent_dir / "grid_TAMS").is_dir()):
        if not quiet:
            print(f"Note: inlists name the save '{stages[0].stem}', but {parent_dir}/grid_TAMS/ "
                  f"already exists; keeping the existing '{LEGACY_STEM}' layout. "
                  f"Pass --stages {stages[0].stem} to override.")
        return legacy_stages()

    if not quiet:
        warn_unreachable(mesa_dir or parent_dir, stages, template_file)

    return stages


def format_stages(stages: list, parent_dir: Optional[Union[str, Path]] = None) -> str:
    """Render a stage list for printing, marking which one decides completion."""
    lines = []
    for i, stage in enumerate(stages):
        marker = "  <- completion marker" if i == len(stages) - 1 else ""
        where = f"{stage.save_dir}/{stage.prefix}<run_dir>{stage.suffix}"
        src = f"   [{stage.source}]" if stage.source else ""
        lines.append(f"  {i + 1}. {stage.stem:<12} {where}{src}{marker}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show the ordered save files a grid directory's runs produce, "
                    "and which one marks a track as finished."
    )
    parser.add_argument("--parent_dir", required=True,
                        help="Grid run directory to inspect.")
    parser.add_argument("--template", default=None,
                        help="Inlist template, if not <parent_dir>/inlist_template.")
    parser.add_argument("--stages", default=None,
                        help="Override discovery with an ordered stage list, "
                             "e.g. --stages ZAMS,TAMS,RGB.")
    parser.add_argument("--write", action="store_true",
                        help="Write the resolved stages to <parent_dir>/stages.json.")
    args = parser.parse_args()

    parent = Path(args.parent_dir).expanduser()
    # Resolved quietly so the resolved stages are printed first and any warning
    # reads as a comment on them, rather than arriving before the header.
    stages = resolve_stages(parent, template_file=args.template, explicit=args.stages)
    print(f"Grid directory: {parent}")
    print(f"Stages ({len(stages)}):")
    print(format_stages(stages, parent))
    print()
    # Checked even when --stages was given: an explicit list that misses an
    # inlist's save is the same mistake, just made deliberately.
    if not warn_unreachable(parent, stages, args.template):
        print("Every save declaration in this directory is accounted for.")
    if args.write:
        print(f"Wrote {save_stages(parent, stages)}")


if __name__ == "__main__":
    main()
