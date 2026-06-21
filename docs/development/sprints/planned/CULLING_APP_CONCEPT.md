# Concept: Smart Culling Application

## Summary

A standalone culling application that consumes technical quality metrics produced by KB Builder to assist practitioners in selecting the best image from near-duplicate groups and removing technically poor files from a corpus.

## Relationship to KB Builder

KB Builder's role is to produce the signal — sharpness scores, exposure scores, aesthetic scores, pHash-based duplicate groupings — and store them in `corpus.db`. The culling app is a downstream consumer of that data. It does not belong inside KB Builder, which deliberately excludes duplicate resolution UI and review/rating workflows (see VISION.md).

## Core Concept

When a corpus contains many near-duplicates (burst sequences, bracketed exposures, similar shots from a session), the user needs a way to select the keeper and discard the rest. The culling app would:

1. Read quality metrics and duplicate groups from `corpus.db` (or a KB Builder export)
2. Surface near-duplicate groups ranked by combined quality signal (sharpness + exposure + aesthetic score)
3. Present a review UI: show the group, pre-select the highest-ranked image, allow the user to confirm or override
4. Write decisions back (flag for deletion, move to archive folder, mark as rejected) without KB Builder needing to know

## Quality Signals Available from KB Builder

- **pHash groupings** — near-duplicate file clusters already computed in Stage 2 (Hash)
- **Sharpness score** — Laplacian variance; planned in KB.11 (Quality stage)
- **Exposure score** — mean luminance + highlight/shadow clipping; planned in KB.11
- **Aesthetic score** — NIMA + CLIP combined rank; implemented in KB.9
- **quality_rank** — combined 1..N rank across sharpness + exposure; planned in KB.11

## Why It's Separate

- File operations (delete, move, archive) are destructive and irreversible — out of scope for a knowledge-building tool
- Culling decisions are per-collection, not per-corpus; the main cataloguing app is the right authority for what stays in a collection
- A dedicated culling UI warrants its own data model, undo history, and workflow design

## When to Revisit

After KB.11 (Quality stage) is complete and quality metrics are proven useful in practice. The culling app can be scoped as a thin consumer of KB Builder's export bundle, requiring no changes to KB Builder itself.
