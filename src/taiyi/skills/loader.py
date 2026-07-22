"""Loading a single Skill (SKILL.md + quality_gate.md)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from taiyi.core.markdown import split_frontmatter
from taiyi.skills.quality_gate import (
    LOCK_FILENAME,
    GateAttestation,
    QualityGate,
    artifact_digest,
    parse_gate,
)


@dataclass
class Skill:
    name: str
    category: str                       # bundled | managed | workspace | auto_generated
    body: str
    risk: str | None = None
    applicability: str | None = None
    triggers: tuple[str, ...] = ()
    scenario: str | None = None
    gate: QualityGate | None = None
    gate_problems: list[str] = field(default_factory=list)
    attestation: GateAttestation | None = None
    attestation_problems: list[str] = field(default_factory=list)
    artifact_digest: str = ""
    runtime_verification: object | None = field(default=None, repr=False)
    path: Path | None = None

    @property
    def production_eligible(self) -> bool:
        """True only with release evidence and a current-process passing rerun.

        This means eligible for Taiyi's governed runtime path.  It does *not*
        imply that deferred external connectors have been certified in a live
        environment; see :attr:`live_ready`.
        """
        return not self.production_problems

    @property
    def release_problems(self) -> list[str]:
        """Static publication problems: tier, declaration, or attestation."""

        problems = [*self.gate_problems, *self.attestation_problems]
        if self.category not in {"bundled", "managed", "workspace"}:
            problems.append(f"category {self.category!r} is not a production tier")
        return problems

    @property
    def release_eligible(self) -> bool:
        """Whether the packaged artifact may cross the publication boundary."""

        return not self.release_problems

    @property
    def production_problems(self) -> list[str]:
        problems = self.release_problems
        if not problems:
            report = self.runtime_verification
            if report is None:
                problems.append("quality gate has not been rerun in the current process")
            elif not getattr(report, "passes", False):
                failed = getattr(report, "failed_case_ids", [])
                suffix = f": {', '.join(failed)}" if failed else ""
                problems.append(f"current-runtime quality gate failed{suffix}")
        return problems

    @property
    def verification_environment(self) -> str | None:
        return self.attestation.environment if self.attestation else None

    @property
    def live_ready(self) -> bool:
        """Whether the evidence came from staging/production, not the mock harness."""

        return self.production_eligible and self.verification_environment in {
            "staging", "production"
        }


def load_skill(skill_dir: str | Path) -> Skill:
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"no SKILL.md in {skill_dir}")
    skill_text = skill_md.read_text(encoding="utf-8")
    meta, body = split_frontmatter(skill_text)
    name = meta.get("name") or skill_dir.name

    gate_path = skill_dir / "quality_gate.md"
    gate_text = ""
    gate: QualityGate | None = None
    problems: list[str]
    if gate_path.exists():
        try:
            gate_text = gate_path.read_text(encoding="utf-8")
            gate = parse_gate(gate_text)
            problems = gate.problems()
        except Exception as e:  # noqa: BLE001 — a malformed gate is a (reported) problem, not a crash
            problems = [f"unparseable quality_gate.md: {e}"]
    else:
        problems = ["missing quality_gate.md"]

    digest = artifact_digest(skill_text, gate_text)
    attestation: GateAttestation | None = None
    attestation_problems: list[str] = []
    lock_path = skill_dir / LOCK_FILENAME
    if not lock_path.exists():
        attestation_problems.append(f"missing {LOCK_FILENAME}")
    else:
        try:
            attestation = GateAttestation.read(lock_path)
            attestation_problems.extend(attestation.problems(
                skill=name,
                digest=digest,
                case_ids=gate.case_ids if gate else [],
            ))
        except Exception as e:  # noqa: BLE001 — invalid evidence is a refusal reason
            attestation_problems.append(f"unparseable {LOCK_FILENAME}: {e}")

    return Skill(
        name=name,
        category=meta.get("category", "sandbox"),
        body=body,
        risk=meta.get("risk"),
        applicability=meta.get("applicability"),
        triggers=tuple(meta.get("triggers", []) or []),
        scenario=meta.get("scenario"),
        gate=gate,
        gate_problems=problems,
        attestation=attestation,
        attestation_problems=attestation_problems,
        artifact_digest=digest,
        path=skill_dir,
    )
