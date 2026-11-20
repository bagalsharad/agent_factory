"""Agentic Continuity Engineering (ACE): recovery that preserves intent, not just execution state.

Components (see docs/architecture/agentic-continuity.md):
  Intent Ledger (ledger) · Execution State Graph + Semantic Checkpoints (dependencies, recorder) ·
  Change Impact Analyzer, Continuity Validator, Semantic Continuity Score and Recovery Decision Engine
  (assessment) · Continuity Auditor (the platform audit log + continuity_assessments) · orchestration (service).
"""
