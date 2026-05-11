---
type: index
status: shipped
last-validated: 2026-05-11
---

# Reference — Index

Design rationale, research synthesis, theory, and historical context. Specs own
module contracts; reports own dated empirical measurements. See
[../CONVENTIONS.md](../CONVENTIONS.md) for the category contract.

## Current Design References

| Document | Topic |
|---|---|
| [game-data-reference](game-data-reference.md) | Starsector file formats, CSV schemas, `.variant` and `.ship` structures. |
| [game-mechanics](game-mechanics.md) | Starsector combat, fitting, weapons, flux, armor, shields, and AI behavior. |
| [honest-evaluation-methodology](honest-evaluation-methodology.md) | Rationale for post-run transform-free oracle evaluation. |
| [optimization-methods](optimization-methods.md) | Optimizer method survey and implementation notes. |
| [optimization-theory](optimization-theory.md) | Bayesian optimization and surrogate theory background. |
| [problem-formulation](problem-formulation.md) | Formal build-optimization problem definition and constraints. |
| [skill-promotion-candidates](skill-promotion-candidates.md) | Repo-local workflows that can be extracted into portable packaged skills. |
| [throughput-optimization](throughput-optimization.md) | Throughput-improvement design notes and validation pointers. |

## Phase References

| Document | Topic |
|---|---|
| [phase4-research-findings](phase4-research-findings.md) | Phase 4 optimizer research findings. |
| [phase5-signal-quality](phase5-signal-quality.md) | Phase 5A/5B signal-quality design. |
| [phase5a-deconfounding-theory](phase5a-deconfounding-theory.md) | TWFE/deconfounding theory synthesis. |
| [phase5c-opponent-curriculum](phase5c-opponent-curriculum.md) | Anchor-first opponent curriculum and rejected alternatives. |
| [phase5d-covariate-adjustment](phase5d-covariate-adjustment.md) | EB shrinkage rationale and historical covariate-design variants. |
| [phase5e-shape-revision](phase5e-shape-revision.md) | Box-Cox objective-shaping rationale. |
| [phase5f-regime-segmented-optimization](phase5f-regime-segmented-optimization.md) | Regime-segmented optimization rationale. |
| [phase6-cloud-worker-federation](phase6-cloud-worker-federation.md) | AWS spot-worker federation design. |
| [phase7-featurized-matchup-surrogate](phase7-featurized-matchup-surrogate.md) | Draft contextual matchup-surrogate plan using non-atomic hull, weapon, hullmod, and opponent features; current dated roadmap checkpoint: [validation-to-Phase-7 roadmap](../reports/2026-05-11-validation-to-phase7-roadmap.md). |
| [phase7-search-space-compression](phase7-search-space-compression.md) | Planned structured search-space representation. |
| [phase7.5-infrastructure-reproducibility](phase7.5-infrastructure-reproducibility.md) | Planned reproducibility and infrastructure improvements. |

## Research And Historical Context

| Document | Topic |
|---|---|
| [cross-domain-optimization-research](cross-domain-optimization-research.md) | Cross-domain optimization analogues. |
| [implementation-roadmap](implementation-roadmap.md) | Historical phased build plan; current status is in the root workflow file. |
| [literature-review](literature-review.md) | Broad literature survey. |
| [multi-fidelity-strategy](multi-fidelity-strategy.md) | Historical evaluation-fidelity design notes; current contracts live in specs. |
| [phase5d-covariate-adjustment](phase5d-covariate-adjustment.md) | Includes rejected alternatives and historical validation notes. |
| [quality-diversity](quality-diversity.md) | Historical QD design notes; see banner before using for implementation. |
| [system-architecture](system-architecture.md) | Deprecated historical architecture tour; specs own implementation contracts. |
| [tech-debt](tech-debt.md) | Current technical-debt ledger. |
