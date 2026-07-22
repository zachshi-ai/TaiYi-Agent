"""太一 / The One (Taiyi) — a governed Agent harness evolving toward production.

Core thesis: governance authority and scheduling authority must be physically
separated. A module that both *does* the work and *signs off* on the work has a
built-in incentive to skip the sign-off. Taiyi removes that incentive by design.

This package is built module-by-module; see DEVELOPMENT_PLAN.md for the roadmap.
Module 1 (this release): the Governance Core — rules-as-data, deterministic
gates, fail-closed verdicts, and a tamper-evident audit log.
"""

__version__ = "0.1.0"
