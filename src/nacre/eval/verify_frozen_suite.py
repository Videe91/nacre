"""
Functionality: Verify the frozen evaluation suite against its manifests, byte for byte.
Owns: hashing every copied file (MNEXA task sets, graders, results; the EXP-0001 and EXP-0003 records) against its
  manifest, and,
  when the external originals are present, every hash-only entry (MNEXA SQLite states, the 031 workspace, the
  EXP-0001 run folder). Reports every mismatch; never repairs.
Public entry: verify_frozen_suite(), SuiteReport
Decisions: D-0016
Assumptions: none
Notes: Phase 2 gate item 1. The manifests are the identity of the suite (D-0016 A): a frozen file that changes is a
  new version with an ADR, never an edit. Hash-only entries live outside the repo (~/Desktop/mnexa,
  ~/Desktop/nacre-runs); they are checked only when those roots are given and exist, and the report says which
  roots were checked, so "not checked" is never mistaken for "passed".
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SuiteReport:
    files_checked: int = 0
    external_checked: int = 0
    external_roots_checked: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(report: SuiteReport, path: Path, expected: str, external: bool = False) -> None:
    if not path.is_file():
        report.problems.append(f"missing: {path}")
    elif _sha(path) != expected:
        report.problems.append(f"sha256 mismatch: {path}")
    if external:
        report.external_checked += 1
    else:
        report.files_checked += 1


def verify_frozen_suite(regression_root: Path, *, mnexa_root: Path | None = None,
                        runs_root: Path | None = None) -> SuiteReport:
    """Check tests/regression/{mnexa,exp0001} against their manifests (and external originals when given)."""
    report = SuiteReport()
    mnexa = Path(regression_root) / "mnexa"
    m = json.loads((mnexa / "MANIFEST.json").read_text())
    for name, entry in m["task_sets"].items():
        if entry["copied"]:
            _check(report, mnexa / entry["location"], entry["sha256"])
            if entry["sidecar_sha256"] is not None and entry["sidecar_sha256"] != entry["sha256"]:
                report.problems.append(f"sidecar disagrees with content: {name}")
    for name, entry in m["graders"].items():
        _check(report, mnexa / "graders" / name, entry["sha256"])
    for run_dir, entry in m["runs"].items():
        if "result_sha256" in entry:
            _check(report, mnexa / "results" / f"{run_dir}.result.json", entry["result_sha256"])
        for regrade, digest in entry.get("regrades", {}).items():
            _check(report, mnexa / "results" / f"{run_dir}.{regrade}", digest)
    for entry in m["other_results"].values():
        if entry["copied"]:
            _check(report, mnexa / "results" / Path(entry["file"]).name, entry["sha256"])
    if mnexa_root is not None and Path(mnexa_root).is_dir():
        results = Path(mnexa_root) / "experiments" / "results"
        report.external_roots_checked.append(str(mnexa_root))
        for name, entry in m["task_sets"].items():
            _check(report, Path(mnexa_root) / "experiments" / f"{name}.json", entry["sha256"], external=True)
        for run_dir, entry in m["runs"].items():
            for f in entry.get("state_files", []):
                _check(report, results / run_dir / "state" / f["file"], f["sha256"], external=True)
        for f in m["not_copied"].get("workspace_031", []):
            _check(report, results / f["file"], f["sha256"], external=True)

    exp = Path(regression_root) / "exp0001"
    e = json.loads((exp / "MANIFEST.json").read_text())
    for f in e["files"]:
        rel = Path(*f["path"])
        if f["copied"]:
            _check(report, exp / rel, f["sha256"])
    for name in ("exp0003", "exp0003_item8", "exp0003_replay_8601ee6"):                 # EXP-0003 gate run; its gate-item-8 replay evidence
        d = Path(regression_root) / name
        if (d / "MANIFEST.json").exists():
            for f in json.loads((d / "MANIFEST.json").read_text())["files"]:
                _check(report, d / Path(*f["path"]), f["sha256"])
    if runs_root is not None and (Path(runs_root) / e["run_dir"]).is_dir():
        report.external_roots_checked.append(str(Path(runs_root) / e["run_dir"]))
        for f in e["files"]:
            _check(report, Path(runs_root) / e["run_dir"] / Path(*f["path"]), f["sha256"], external=True)
    return report
