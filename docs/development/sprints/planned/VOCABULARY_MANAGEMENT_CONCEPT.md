# Vocabulary Management Concept

Design notes from a session exploring unified capture rules and vocabulary
authoring. Not a scheduled sprint. Intended as input for future sprint planning.

---

## Background

Vocabulary terms currently enter the system via two paths:

1. **Suggest Review** — candidates surface through NPMI/Louvain clustering and
   the user accepts or rejects them. No synonym data is recorded at this point.
2. **Seed CSV** (`seed/vocabulary.csv`) — terms and synonyms imported at KB
   creation time. The only current way to set `synonyms_json`.

Once terms are accepted there is no UI for managing them: no way to add
synonyms, merge duplicate terms, set canonical forms, or review what the
vocabulary looks like as a whole. The `synonyms_json` column is effectively
write-only after KB creation.

---

## The Correction / Synonym Distinction

Both corrections and synonyms look like "replace X with Y" but serve different
purposes and should be handled differently by the system.

### Correction
The source term is *wrong* in some form:
- Typo: `occassion → occasion`
- Regional variant (by KB convention): `colour → color`
- Abbreviation: `mt → mount`
- Colloquial form: `Xmas → Christmas`

The source term has no independent semantic value. Replace it and discard it.
No vocabulary impact.

### Synonym
The source term is *correctly spelled* and semantically valid, but the KB
normalises to a canonical form:
- `shore → beach`
- `automobile → car`
- `hiking trail → trail`

The source term carries real meaning. It should be recorded as a synonym of the
canonical vocabulary term, so that downstream consumers (write-back, model
context) can see the full synonym group.

### Rule
A source term should only be treated as a synonym if it is spelled correctly.
The distinction is user-declared — the system cannot derive it automatically.

---

## The Pre- / Post-Normalisation Split

Synonyms serve two different consumers and the mechanism differs:

| Level | Mechanism | Purpose |
|---|---|---|
| Pre-normalisation | Replace rule (`shore → beach`) | Ensures variant terms fold to canonical form before vocabulary lookup |
| Post-normalisation | `vocabulary.synonyms_json` | Records the synonym group for write-back to file metadata |

Both are needed. A Replace rule handles the pipeline; `synonyms_json` handles
the output. When a user declares a synonym Replace rule (source = correctly
spelled, canonical = vocabulary term), the system should populate both.

---

## Proposed Vocabulary Manager

A **Knowledge / Vocabulary** page, consistent with the existing Location
Registry and People Registry pattern.

### Core capabilities

1. **Browse** — paginated list of all accepted vocabulary terms, sortable by
   source (seeded / accepted / domain) and synonym count.

2. **Edit** — click a term to open an edit panel:
   - Rename canonical form
   - Add / remove synonyms manually
   - Set `write_synonyms` flag
   - View Replace rules that map to this term (derived synonym links)

3. **Merge** — combine two terms: one becomes the canonical, the other becomes
   a synonym. Generates a Replace rule automatically.

4. **Stem / lemma suggestions** — using spaCy (already in stack), surface
   morphological variants of accepted terms:
   - `beach` → suggest `beaches` as a plural variant
   - `hiking` → suggest `hike`, `hiked`, `hiker`
   - User reviews and accepts as synonyms or Replace rules.

5. **Semantic grouping** — surface clusters of accepted terms that are
   semantically close and may be synonyms. Two implementation tiers:
   - **spaCy word vectors** (cheap, always available): cosine similarity between
     term vectors; works for common English words but degrades for
     domain-specific vocabulary.
   - **LLM-assisted grouping** (optional, uses configured LLM): send a batch of
     accepted terms; ask the model to identify synonym groups and flag likely
     misspellings that survived review. Present suggestions for user approval.
     Same workbench pattern as other LLM stages — model proposes, user decides.

6. **Misspelling detection** — within the semantic grouping pass, ask the LLM
   or use edit-distance to flag terms that look like misspellings of accepted
   vocabulary. Prompt the user to convert them to corrections rather than
   synonyms.

### Connection to the Suggest stage

The Suggest stage already clusters *candidate terms* using NPMI + Louvain to
identify which ones should be accepted together. The vocabulary manager applies
the same conceptual problem to *accepted terms*: find the ones that belong
together and let the user declare the canonical form. The infrastructure
(spaCy, clustering, LLM session) is already present.

---

## Export format

| Content | File | Format |
|---|---|---|
| Accepted vocabulary terms + synonyms | `vocabulary.csv` | CSV (existing) |
| Exact replacements (corrections + synonyms) | `corrections.csv` | CSV (replaces `corrections.yaml`) |
| Regex rules (all actions) | `patterns.yaml` | YAML (action field per rule) |
| Exact rejected terms | `reject_tokens.csv` | CSV (existing) |
| Exact ignored terms (stopwords) | `stopwords.txt` | Flat text (existing) |

`corrections.csv` columns: `raw`, `canonical`, `type` (`correction` | `synonym`)

When `type=synonym` and `canonical` is present in `vocabulary.csv`, the system
should ensure `canonical.synonyms_json` includes `raw`.

---

## Relationship to Unified Capture Rules

The vocabulary manager is a companion sprint to the unified capture rules work,
not part of it. The unified rules sprint should:

- Include `type` (`correction` | `synonym`) on Replace rules
- When a synonym Replace rule is saved and the canonical term exists in
  vocabulary, update `synonyms_json` automatically
- This is the minimal synonym hook; full vocabulary management is deferred

The vocabulary manager sprint follows and builds on the unified rules schema.
