# KB Builder — Vision

## Purpose

KB Builder extracts domain knowledge from a corpus of media files and syncs that knowledge back to file metadata. It is for practitioners managing large, domain-specific archives who want portable, reusable vocabulary built from the corpus itself — not assembled by hand.

The primary output is a **knowledge base**: vocabulary, corrections, and normalisation rules that outlast any single run and improve with every pass over new material. Enriched file metadata (XMP keywords, descriptions) is a derived output. The KB is the durable asset; the files are where it materialises.

## Bidirectional Flow

```
Files → (ingest, describe, suggest) → Knowledge Base
Knowledge Base → (normalise, retag, write-back) → Files
```

Pass 1 extracts signal from the corpus. Pass 2 applies accumulated knowledge back to it. When the KB improves, write-back is re-run to bring files into alignment. Files can go anywhere after enrichment — the main cataloguing app, Lightroom, any XMP-aware viewer.

## The Defining Difference

**Cross-collection scope.** This tool runs across multiple source directories in a single pass, sharing one `corpus.db`. Frequency signals only emerge at corpus scale — running across 12 project folders simultaneously produces qualitatively better vocabulary and corrections than running per-folder. This is the defining difference from the main app's Knowledge tab, which operates per-collection.

## What It Does

- Multi-source ingest with SHA-256 + pHash dedup across all sources
- ExifTool metadata extraction and field mapping
- Filename/path normalisation (capture, reject, substitute, correct) with guided review
- Vision describe — Pass 1: scene-aware, resumable, pHash-filtered frames
- Whisper transcription for audio and video
- Suggestion — three-level analysis: Level A (spaCy), Level B (NPMI co-occurrence graph), Level C (LLM cluster labelling)
- Vocabulary management: browse, edit, import/export, inline synonym editing
- Classification — Pass 2: text-only LLM tags descriptions against vocabulary
- Entity tables: structured reference data with linked-table resolution and entity match pipeline
- ExifTool write-back and portable KB export

## What It Deliberately Excludes

- Library / collection management
- Taxonomy browser
- Face recognition
- Map / geo UI
- Duplicate resolution UI
- Review / rating workflows
- Multi-user or permissions model

The exclusion list is as important as the inclusion list. Each exclusion keeps the tool lean and the mental model clear. These features live in the main cataloguing app.

## Pipeline at a Glance

```
[0]   Ingest
[0.5] Analyse
      ★ Normalisation Review  (human touchpoint)
[1]   Normalise
[1.5] Extract Metadata
[1.6] Extract Fields
[1.7] Entity Match
[2]   Hash
[3a]  Describe  (Pass 1 — vision model)
[3b]  Transcribe  (Whisper)
[4]   Suggest  (Level A → B → C)
      ★ Suggestion Review  (human touchpoint)
[5]   Retag  (Pass 2 — text model)
      ★ New Terms Review  (human touchpoint)
[6]   Write-back
[7]   Export
      Aesthetic  (optional, independent)
```

## Two-Database Design

```
knowledge.db    durable; never reset; the KB being built; importable into main app
corpus.db       accumulates across runs; rebuildable; description cache + file corpus state
```

`knowledge.db` outlasts any specific run. A KB from a first corpus carries forward immediately when a second corpus is added — vocabulary, corrections, and rules already built apply without re-derivation.

## Target User

A solo practitioner managing a large domain-specific media archive. Technically capable but not necessarily a developer. Uses the web UI for review and curation, and the CLI for overnight runs and automation across multiple KBs.

## Relationship to the Main App

Complementary, not competing. KB Builder is where domain knowledge is built at corpus scale — before or independently of any cataloguing decision. The main app is where the catalogue is managed once that knowledge exists. Natural workflow: run KB Builder across the full media library → import KB into main app → scan collections → files arrive pre-enriched.

## Future Consideration: Componentized Media Management

KB Builder was designed as a self-contained tool, but its architecture is compatible with a larger, modular media management program where it acts as the **extraction engine** — the heavy, model-dependent component that runs once (or periodically) to produce a rich facts database. Lighter, purpose-built components then consume those facts through dedicated management interfaces.

**The facts database is `corpus.db`.** It already holds everything a downstream component needs without running any models: file paths and hashes, GPS coordinates and geolabels, face regions and cluster assignments, voice embeddings, temporal fields, tags, transcriptions, and coverage flags. A People management component, for example, would read `face_clusters`, `voice_speaker_clusters`, and `people` directly — all the expensive inference already done.

**The lightweight scripts approach is the pragmatic starting point.** Rather than building a full modular framework, purpose-built scripts (or small CLIs) can read `corpus.db` and implement specific management workflows: deduplication review, people identification, location grouping, culling. These stay thin because the pipeline has already done the work.

**The schema stability problem is the design constraint to resolve before full modularization.** Downstream components that depend on specific table layouts break when KB Builder evolves. Three options in increasing decoupling order:

1. **Scripts in the same repo** — scripts migrate alongside the schema; zero extra infrastructure, tight coupling acceptable while the schema is still evolving.
2. **Stable SQL views** — KB Builder defines a versioned set of named views constituting a public interface; internal tables can change as long as views hold.
3. **Snapshot export (`facts.db`)** — KB Builder generates a read-only, clean-schema export on demand; external tools import that artifact; staleness between rebuilds is the tradeoff.

The components envisioned as natural consumers: People management (face/voice identification and labelling), Location management (GPS clustering, custom region assignment), Duplication management (pHash grouping and cull decisions), and a general-purpose media library browser. Each would replace one entry in the "What It Deliberately Excludes" list above — not by being built into KB Builder, but by reading its output.

## Glossary

| Term | Definition |
|---|---|
| **KB** | Knowledge base — `knowledge.db` + config files for one domain |
| **Corpus** | All files ingested into `corpus.db` across all sources for a KB |
| **Canonical term** | The single authoritative form of a vocabulary entry (e.g. "Highway 1") |
| **Synonym** | Alternate form stored alongside the canonical term; used for LLM recognition, not as a standalone entry |
| **Text pool** | Per-file assembly of path tokens, metadata fields, descriptions, and transcriptions fed to Suggest |
| **Touchpoint** | A human review step where the pipeline pauses; there are three: Normalisation, Suggestion, New Terms |
| **Pass 1** | Vision model describe stage — produces `description_raw` per file |
| **Pass 2** | Text-only LLM retag stage — applies vocabulary against descriptions; produces `tags_json` per file |
| **Dirty set** | Files whose `writeback_kb_version` is behind current KB version; candidates for write-back |
| **Canonical file** | The first-seen instance of a SHA-256-duplicated file; GPU stages run on canonical files only |
| **Capture rule** | A regex that extracts structured metadata (dates, codes) from filename tokens into `file_captured_fields` |
| **Enrichment text** | Metadata fields flagged `enrichment_text=true` in `field_map.csv`; contribute to the Suggest text pool |
