# KB Builder — Technical Specification

> **Overview:** See `VISION.md` for purpose, scope, pipeline summary, two-database design brief, target user, and glossary.
> **Implementation patterns:** See `docs/development/ARCHITECTURE.md` for module layout, design principles, API patterns, and SQLite conventions.
> This document is the full technical reference — schemas, stage behaviour, normalization instruments, suggestion levels, entity tables, field map, review queues, onboarding, sync tracking, and outputs.

---

## Two-Database Design

```
knowledge.db    ← durable, portable, grows forever, primary import target
corpus.db       ← accumulated file corpus, durable across runs, rebuildable if needed
```

### `knowledge.db` — the domain knowledge being built

Never reset. Grows and improves with every run across any source. The primary portable output — directly importable into the main app's knowledge base.

```
vocabulary
  id, term, synonyms_json, source (accepted|new_terms|user), added_at

stoplist
  term, scope (global|filename), source (user|builtin|domain), added_at
  -- source='builtin': linguistic stopwords, pre-loaded, never exported
  -- source='domain':  user-maintained domain stopwords, included in KB export

corrections
  raw_term, canonical_term, type (exact|pattern), pattern_str,
  correction_kind TEXT,  -- 'typo' | 'alias' | 'abbreviation'
  added_at
  -- typo: raw form should never appear (TuckInleted → Tuck Inlet); treated as error
  -- alias: legitimate alternate name (Trans-Canada → Highway 1); may be kept as synonym
  -- abbreviation: short form of canonical (TCH → Highway 1); may be kept as synonym

capture_rules
  pattern, label, extract_as, format_str, keep_token, value_type, date_precision, added_at
  -- value_type: 'date' | 'time' | 'code' | 'text' | 'numeric'
  -- date_precision: 'day' | 'month' | 'year' | 'decade' | 'century' | NULL
  --   Only meaningful when value_type = 'date'. Declares the resolution of the extracted value.
  --   Drives precision-encoded storage (e.g. '2016xxxx' for year, '196xxxxx' for decade)
  --   and natural language prompt generation. NULL treated as 'day'.
  -- used by vision prompt builder to format captured values into natural language context

substitute_rules
  pattern, replacement, label, applies_to (filename|description|both), added_at

reject_tokens                -- tokens stripped from text entirely before any downstream tool receives them
  id INTEGER PRIMARY KEY,
  pattern TEXT,              -- exact token string or regex
  is_regex INTEGER,          -- 0 = exact match, 1 = regex pattern
  label TEXT,                -- human description of what this rejects
  scope TEXT,                -- 'filename' | 'path' | 'both'
  added_at DATETIME
  -- Use only for genuinely meaningless tokens (garbled codes, corrupted strings).
  -- Applied first in the Normalize pipeline, before Capture.

classify_rules             -- rule-based derivation: structured field value → semantic tag
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,            -- human-readable rule name: "Christmas Day"
  result_tag TEXT NOT NULL,       -- tag written to file_derived_tags: "Christmas Day"
  category TEXT NOT NULL,         -- 'calendar' | 'technical' | 'life_event' | 'geographic'
  source TEXT NOT NULL,           -- 'exif' | 'captured' | 'computed'
  field_name TEXT,                -- EXIF tag or captured field name to check (NULL for computed rules)
  match_type TEXT NOT NULL,       -- 'exact' | 'month_day' | 'month_range' | 'range' | 'comparison' | 'computed'
  match_config TEXT NOT NULL,     -- JSON: match parameters (see Classify Stage section)
  minimum_precision TEXT DEFAULT 'full',  -- 'full' | 'month' | 'year' | 'decade' | 'century'
  is_builtin INTEGER DEFAULT 0,   -- 1 = ships with tool; 0 = user-defined
  enabled INTEGER DEFAULT 1,
  added_at DATETIME DEFAULT (datetime('now'))
  -- match_type reference:
  --   month_day:    {"month": 12, "day": 25}                    fixed calendar event
  --   month_range:  {"months": [12, 1, 2]}                      season
  --   range:        {"min": 70} or {"max": 28} or both          numeric field bounds
  --   comparison:   {"field_a": "ImageWidth", "op": ">", "field_b": "ImageHeight"}
  --   exact:        {"value": 1}                                 exact field value
  --   computed:     {"algorithm": "easter"}                      variable-date algorithm

kb_version
  id INTEGER PRIMARY KEY,   -- increments on every KB-mutating operation
  changed_at DATETIME,
  change_type TEXT           -- 'vocabulary' | 'synonym' | 'corrections' | 'capture_rules' | 'substitute_rules' | 'reject_tokens' | 'stoplist' | 'field_map' | 'classify_rules' | 'people' | 'life_events'

ignored_fields             -- fields the user has decided to skip; never re-surfaced by scanner
  field_name TEXT,          -- full ExifTool tag name (e.g. XMP-bcmot:ContractID)
  namespace TEXT,           -- prefix only (e.g. XMP-bcmot)
  ignored_at DATETIME,
  reason TEXT               -- optional user note

entity_table_registry      -- one row per registered entity table
  id INTEGER PRIMARY KEY,
  table_name TEXT UNIQUE,   -- e.g. 'entity_bridge' (stored as entity_<name> in knowledge.db)
  display_name TEXT,        -- human label shown in UI
  trigger_word TEXT,        -- word-boundary scan trigger (e.g. 'bridge')
  trigger_aliases TEXT,     -- JSON array of variant forms (e.g. ["bridges","bridging"])
  key_column TEXT,          -- which column in the entity table to match against enrichment text
  match_type TEXT,          -- 'text' | 'gps'
  description TEXT,
  source_csv TEXT,          -- relative path to original source CSV (for re-import)
  created_at DATETIME,
  updated_at DATETIME

people                     -- human subjects registry; seeded from CSV import
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  preferred_name TEXT NOT NULL,   -- display name; resolved from name forms
  title TEXT,                     -- e.g. 'Mrs', 'Dr'
  first_name TEXT,
  middle_name TEXT,
  last_name TEXT,
  notes TEXT,
  family INTEGER NOT NULL DEFAULT 0,  -- 1 = family member
  -- Future identification slots; NULL until voice/face sprints run
  voice_centroid BLOB,            -- serialised float array; average of voice_samples embeddings
  voice_samples INTEGER NOT NULL DEFAULT 0,
  face_centroid BLOB,             -- serialised float array; average of face_samples embeddings
  face_samples INTEGER NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT (datetime('now'))

people_names               -- all known name forms for a person; enables multi-form NER matching
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES people(id),
  name TEXT NOT NULL,
  name_type TEXT NOT NULL,        -- 'given' | 'married' | 'nickname' | 'alias'
  UNIQUE(person_id, name)

life_events                -- significant dates associated with a person; used by Classify stage
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES people(id),
  event_type TEXT NOT NULL,       -- 'birth' | 'death' | 'marriage' | 'custom'
  event_date TEXT NOT NULL,       -- ISO date YYYY-MM-DD; partial OK: YYYY-MM for month-only
  label TEXT,                     -- e.g. "Wedding at St. Andrew's Cathedral"
  partner_id INTEGER REFERENCES people(id),  -- for 'marriage': links both parties; firing rule surfaces both names
  UNIQUE(person_id, event_type, event_date)

entity_table_links         -- parent→child relationships between entity tables
  id INTEGER PRIMARY KEY,
  parent_table TEXT,        -- e.g. 'entity_bridge'
  parent_column TEXT,       -- FK column in the parent row (e.g. 'structure_type_id')
  linked_table TEXT,        -- e.g. 'entity_structure_type'
  linked_key_column TEXT,   -- PK column in the linked table (e.g. 'id')
  label TEXT,               -- display label for the relationship (e.g. 'Structure Type')
  include_in_text_pool INTEGER DEFAULT 1,  -- linked data contributes to Suggest text pool
  added_at DATETIME
  -- Links form a directed graph. Traversal at enrichment time is recursive with cycle detection
  -- and a configurable max depth (default: 3). Cycles are detected via a visited-table set.
```

### `corpus.db` — the accumulated file corpus and description cache

Accumulates across runs for SHA-256 dedup (re-running over the same files skips already-processed work). **Treat as a durable asset, not a throwaway working set.** The Pass 1 descriptions stored here are the reproducible intermediate that makes downstream outputs stable — wiping corpus.db means re-running vision inference, which introduces LLM stochasticity. `knowledge.db` is unaffected by wiping corpus.db, but the description cache is lost.

```
sources                        ← one row per declared source directory
  id, path, added_at,
  file_type TEXT,              -- 'images' | 'video' | 'all'
  recursive INTEGER,           -- 1 = include subdirectories
  exclude_patterns TEXT,       -- JSON array of glob patterns to skip
  file_count_ingested INTEGER, -- updated after each ingest run
  last_ingested_at DATETIME,
  removed_at DATETIME          -- NULL = active; non-NULL = source removed from KB; files retained for re-ingest prompt
                               --   `enrich source remove <path>` sets this; `enrich ingest` warns if files only exist
                               --   under removed sources and prompts to re-add or delete from corpus

files                          ← anchor: identity + fast stat only; no file reads at ingest
  id, source_id, path, filename, filename_normalized, ext,
  file_type TEXT,              -- 'image' | 'video' | 'audio'; populated at ingest from extension
  file_size INTEGER,           -- from stat(), no file read required
  mtime DATETIME,              -- from stat(), used for re-ingest dedup (same path+size+mtime = skip)
  sha256 TEXT,                 -- NULL until Hash stage runs
  canonical_id INTEGER,        -- NULL = this file is canonical (or unique); non-NULL = duplicate,
                               --   points to the first-seen file with the same SHA-256
  ingested_at DATETIME,
  writeback_kb_version INTEGER -- NULL = never written; matches kb_version.id when in sync

file_captured_fields           ← 1:N; dynamic capture rule outputs (EAV); populated by Normalize (re-runnable)
  file_id INTEGER,
  field_name TEXT,             -- matches capture_rules.extract_as (e.g. 'file_date_full', 'contract_number')
                               -- also holds the resolved 'asset_date' row written by the date resolution sub-step
  value TEXT,                  -- formatted value after format_str applied
                               -- date fields (value_type='date') use precision-encoded format:
                               --   '20160929' = day | '201609xx' = month | '2016xxxx' = year
                               --   '196xxxxx' = decade (1960s) | '19xxxxxx' = century (1900–1999)
  captured_at DATETIME,
  UNIQUE(file_id, field_name)  -- enables UPSERT semantics on re-run; prevents duplicate rows

file_exif                      ← 1:1; raw ExifTool output; populated by Extract Metadata stage
  file_id INTEGER PRIMARY KEY,
  metadata_json TEXT,          -- full ExifTool -j output for this file
  extracted_at DATETIME

file_metadata_fields           ← 1:N; scalar fields declared in field_map.csv
  file_id INTEGER,
  canonical_name TEXT,         -- from field_map.csv canonical_name column
  raw_field_name TEXT,         -- original ExifTool tag (e.g. XMP-bcmot:ContractID)
  value TEXT,
  value_type TEXT,             -- text | code | date | numeric
  extracted_at DATETIME

file_metadata_keywords         ← 1:N; one row per keyword across all keyword-type fields
  file_id INTEGER,
  canonical_name TEXT,         -- which field sourced this (e.g. keywords, keywords_iptc)
  keyword TEXT,                -- raw value from file
  normalized_keyword TEXT,     -- NULL until Normalize stage runs
  extracted_at DATETIME

file_hashes                    ← 1:1 for images only (not applicable to video)
  file_id INTEGER PRIMARY KEY,
  sha256_content TEXT,         -- SHA-256 of decoded pixel data (metadata-stripped); detects same image with different EXIF
  phash TEXT,
  dhash TEXT,
  hashed_at DATETIME

file_aesthetic                 ← 1:N; one row per model per file
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  model_name TEXT,             -- 'nima_mobilenet' | 'clip_vit_b32' | 'combined_rank'
  score REAL,
  band TEXT,                   -- 'excellent' | 'good' | 'average' | 'poor'
  scored_at DATETIME

descriptions                   ← 1:1; aggregate/summary description per file
  file_id INTEGER PRIMARY KEY,
  description_raw TEXT,        -- direct vision model output
  description_normalized TEXT, -- after substitute rules with applies_to='description'|'both'
  model TEXT,
  processed_at DATETIME,
  pass1_status TEXT            -- 'pending' | 'done' | 'failed' | 'skipped'

video_frames                   ← 1:N per video; per-frame intermediates used to produce summary
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  frame_index INTEGER,
  timestamp_ms INTEGER,
  frame_phash TEXT,            -- for dedup/scene-change detection during describe
  description TEXT,
  model TEXT,
  processed_at DATETIME

candidates                     ← produced by Suggest; one row per candidate term
  id INTEGER PRIMARY KEY,
  file_id INTEGER,             -- Level A only: file that originated this term (provenance evidence)
                               -- Level B + C: NULL — these are corpus-level observations, not file-level facts
  term TEXT,
  source TEXT,                 -- 'level_a' | 'level_b' | 'level_c'
  cluster_id TEXT,             -- Level B/C cluster identifier; siblings share cluster_id
  notes TEXT,                  -- Level C LLM reasoning; shown in review queue
  status TEXT,                 -- 'pending' | 'accepted' | 'rejected' | 'corrected'
  corrected_to TEXT,
  created_at DATETIME

transcriptions                 ← produced by Transcribe (Stage 3b); 1:1 per eligible file
  file_id INTEGER PRIMARY KEY,
  transcript_text TEXT,        -- full transcript; NULL if no_audio or failed
  language TEXT,               -- ISO 639-1 code detected by Whisper ('en', 'fr', etc.)
  duration_ms INTEGER,         -- audio duration in ms (for progress estimation)
  model TEXT,                  -- Whisper model name used
  processed_at DATETIME,
  transcribe_status TEXT       -- 'pending' | 'done' | 'failed' | 'skipped' | 'no_audio'
                               --   skipped = image file; no_audio = video with no audio stream

transcript_segments            ← optional; 1:N per transcription; segment-level timestamps
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  start_ms INTEGER,
  end_ms INTEGER,
  text TEXT,
  avg_logprob REAL             -- Whisper's per-segment confidence proxy; low = uncertain

retag_output                   ← produced by Retag (Pass 2); 1:1 per file
  file_id INTEGER PRIMARY KEY,
  tags_json TEXT,
  refined_description TEXT,
  new_terms_proposed_json TEXT,
  model TEXT,
  processed_at DATETIME,
  retag_status TEXT            -- 'pending' | 'done' | 'failed' | 'skipped'

file_derived_tags              ← produced by Classify (Stage 1.8); 1:N per file
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL,
  tag TEXT NOT NULL,              -- e.g. 'Christmas Day', 'Landscape', 'Wide Angle', 'Autumn'
  category TEXT NOT NULL,         -- 'calendar' | 'technical' | 'life_event' | 'geographic'
  rule_id INTEGER,                -- FK to classify_rules.id; NULL for life_event rules (sourced from life_events)
  confidence TEXT DEFAULT 'certain',  -- 'certain' | 'inferred'
                                  --   certain: deterministic match (orientation, fixed date)
                                  --   inferred: partial date or estimated value
  derived_at DATETIME DEFAULT (datetime('now')),
  UNIQUE(file_id, tag)

file_entity_matches            ← produced by Entity Match (Stage 1.7); 1:N per file
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  table_name TEXT NOT NULL,    -- e.g. 'entity_bridge'
  matched_value TEXT NOT NULL, -- key column value that triggered the match
  match_source TEXT NOT NULL,  -- 'text_path' | 'text_metadata' | 'text_description' | 'gps'
  payload_json TEXT,           -- full resolved row as JSON, including linked table data under '_links'
  matched_at DATETIME,
  stale INTEGER DEFAULT 0,     -- set to 1 when the entity table is re-imported and key columns change;
                               --   stale matches are hidden from review queue and excluded from write-back;
                               --   `enrich entity-match` clears stale flag after re-running matching
  UNIQUE(file_id, table_name, matched_value)
  -- payload_json structure with linked data:
  -- {"bridge_name": "Coquihalla Summit", "year_built": 1986,
  --  "_links": {"structure_type": {"name": "Box Girder", "engineering_category": "Concrete"},
  --             "highway": {"name": "Coquihalla Hwy", "route_number": "BC-5"}}}

gps_proposals                  ← text-to-GPS proposals; workflow state independent of Classify re-runs
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id),
  location_id INTEGER NOT NULL,   -- FK to locations entity table row
  matched_text TEXT NOT NULL,     -- phrase that triggered the match (e.g. "Powell River")
  matched_field TEXT NOT NULL,    -- 'keyword' | 'caption' | 'title' | 'description'
  proposed_lat REAL NOT NULL,
  proposed_lon REAL NOT NULL,
  threshold_m INTEGER NOT NULL,   -- precision indicator from the register entry
  status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'accepted' | 'dismissed'
  created_at DATETIME DEFAULT (datetime('now')),
  decided_at DATETIME,
  UNIQUE(file_id, location_id)
  -- Re-run behaviour: Classify uses INSERT OR IGNORE so existing proposals
  -- (and their user decisions) survive a Classify re-run; new file/location
  -- pairs are added; accepted/dismissed rows are never overwritten.
  -- Accepted proposals are written to EXIF via ExifTool at write-back (Stage 6);
  -- writeback_log references gps_proposals.id for undo.

writeback_log                  ← audit of ExifTool write-back operations
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  field TEXT,
  value TEXT,
  written_at DATETIME,
  status TEXT                  -- 'success' | 'failed' | 'skipped'

pipeline_checkpoints           ← last-run metadata for each stage; one row per stage (UPSERT)
  stage TEXT PRIMARY KEY,      -- DAG key: 'ingest', 'analyse', 'normalize', 'describe', etc.
  last_run_at DATETIME,
  files_processed INTEGER,
  files_skipped INTEGER,
  errors INTEGER,
  duration_seconds REAL,
  kb_version_at_run INTEGER    -- knowledge.db kb_version.id at time of run; used for staleness detection

analyse_tokens                 ← produced by Analyse (Stage 0.5); one row per unique token/group entry
  id INTEGER PRIMARY KEY,
  token TEXT NOT NULL,          -- raw token string as it appears in filenames/paths
  pattern_class TEXT,           -- detected shape class: '6-digit numeric', 'CamelCase compound', etc.
  semantic_type TEXT,           -- 'date' | 'time' | 'sequential' | 'code' | 'compound' | 'unclassified'
  frequency INTEGER,            -- count of distinct files where this token appears
  file_count INTEGER,           -- same as frequency; alias for display convenience
  depth_position INTEGER,       -- modal path depth where this token appears (NULL if filename-only)
  is_cross_source INTEGER DEFAULT 0,  -- 1 if token also appears in file_metadata_keywords
  proposed_action TEXT,         -- system-inferred action: 'capture_date' | 'capture_time' | 'ignore' | NULL
  proposed_extract_as TEXT,     -- system-inferred extract_as field name (NULL if not proposed)
  status TEXT DEFAULT 'pending', -- 'pending' | 'decided'
  created_at DATETIME,
  UNIQUE(token)
  -- Updated (not replaced) on each Analyse re-run: frequency/file_count/depth are refreshed,
  -- but 'status' is intentionally NOT reset — decided tokens keep their status across re-runs.
  -- Tokens whose source files have been removed from the corpus are deleted at the end of each run.
  -- 'decided' rows are tokens for which a rule has been written to knowledge.db via the
  -- Normalization Review. Rows revert to 'pending' if the corresponding rule is removed (undo).
```

**Why separate:** `knowledge.db` outlasts any specific enrichment run. A KB's corpus is not fixed to a single folder or a single ingest run. As new relevant material is discovered, running `enrich ingest` again adds those files to the same corpus.db. SHA-256 dedup ensures already-described files are skipped; only genuinely new files are processed. The KB grows in two independent dimensions: the corpus gets wider (more files, more sources) and the knowledge gets deeper (richer vocabulary, more corrections, better rules). Neither resets the other.

`enrich ingest` reports how many files in the new source were already known to corpus.db and how many are genuinely new.

**Reproducibility:** Given the same `knowledge.db` version and an intact `corpus.db` (descriptions cached), re-running write-back produces identical file metadata. Pass 1 descriptions are not perfectly reproducible if re-run from scratch (LLM floating-point non-determinism), but Pass 2 tags and corrections are fully stable given the same inputs.

### Schema Migrations

Both databases use numbered SQL migration files under `src/migrations/`:

```
src/migrations/
  corpus/
    0001_init.sql
    0002_add_retag_status.sql
    ...
  knowledge/
    0001_init.sql
    0002_add_synonym_change_type.sql
    ...
```

**Migration tracking uses a `_migrations` table, not `PRAGMA user_version`.** The table approach tracks applied migrations by name, not by count, and is safe for out-of-order application:

```sql
-- created in 0001_init.sql of both databases
CREATE TABLE IF NOT EXISTS _migrations (
    id TEXT PRIMARY KEY,   -- e.g. '0003_add_entity_links'
    applied_at DATETIME DEFAULT (datetime('now'))
);
```

`db/migrations.py` provides a shared runner used by both `corpus.py` and `kb.py`:

```python
def apply_migrations(conn, migrations_dir: Path) -> None:
    applied = {row[0] for row in conn.execute("SELECT id FROM _migrations")}
    pending = sorted(p for p in migrations_dir.glob("*.sql") if p.stem not in applied)
    for path in pending:
        sql = path.read_text()
        try:
            conn.executescript(sql)
            conn.execute("INSERT INTO _migrations (id) VALUES (?)", (path.stem,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Migration {path.name} failed: {e}") from e
```

A failed migration rolls back cleanly and surfaces a clear error rather than leaving the database in a partial-migration state.

---

## Full Pipeline (11 stages, 3 human touchpoints)

```
[0]    INGEST           stat walk → files table (file_size + mtime; no file reads, no LLM)
         ↓
[0.5]  ANALYSE          Tokenize filenames/paths → pattern-group → frequency rank
         ↓
     ★  NORMALIZATION REVIEW (optional human touchpoint)
         User reviews grouped token queue → writes rules to knowledge.db
         ↓
[1]    NORMALIZE        Re-runnable at any point after Ingest. Applies current rules from
                        knowledge.db to all text sources populated at the time of the run:
                        — After Ingest only: normalized_filename, file_captured_fields (UPSERT)
                        — After Extract Fields (1.6): also normalized_keyword in
                          file_metadata_keywords (UPSERT); also runs date resolution sub-step
                          (reads file_captured_fields + file_metadata_fields, resolves asset_date
                          per the date_resolution config, writes result to file_captured_fields)
                        — After Describe (3): also description_normalized in descriptions
                          (via substitute_rules with applies_to='description'|'both')
                        Each run is additive and idempotent.
         ↓
  ┌────────────────────────────────┐
  │  [1.5] EXTRACT METADATA       │  ExifTool → file_exif.metadata_json
  │  [1.6] EXTRACT FIELDS         │  parse JSON × field_map.csv →
  │                               │  file_metadata_fields + file_metadata_keywords
  │  [1.7] ENTITY MATCH           │  match file_metadata_fields against entity tables →
  │                               │  file_entity_matches (with linked table resolution)
  │                               │  match_type: 'text' | 'gps' | 'date'
  │  [1.8] CLASSIFY               │  apply classify_rules to captured fields + EXIF →
  │                               │  file_derived_tags (calendar, technical, life_event, geographic)
  │  [2]   HASH                   │  SHA-256 + pHash + dhash + sha256_content → file_hashes
  └────────────────────────────────┘
  Stages 1.5, 1.6, 1.7, 1.8, and 2 are independent after their prerequisites.
  Extract Fields (1.6) requires Extract Metadata (1.5). Entity Match (1.7) requires Extract Fields (1.6).
  Classify (1.8) requires Normalize (1) and Extract Metadata (1.5); runs after Entity Match (1.7) for life_event rules.
  Hash (2) has no dependency on the others and can run in parallel with 1.5–1.8.
         ↓
[3a]   DESCRIBE         Pass 1: vision model → descriptions + video_frames
                        (gated on Hash to avoid GPU work on duplicates)
[3b]   TRANSCRIBE       Whisper → transcriptions + transcript_segments
                        (gated on Hash; parallel with Describe; different model, no conflict)
         ↓
[4]    SUGGEST          KB-building pass: Level A (spaCy) → Level B (NPMI graph) → Level C (LLM cluster label)
                        → candidates table; primary output is vocabulary terms, synonym relationships,
                          and taxonomy hints — not per-file keyword assignments (that is Stage 5 Retag).
                        Reads file_metadata_fields + file_metadata_keywords + descriptions +
                        transcriptions for text pool (transcriptions omitted per-file if Transcribe
                        has not run for that file; Level A continues without error).
                        Level B/C candidates have file_id = NULL; they are corpus-level observations.
         ↓
     ★  SUGGESTION REVIEW (human touchpoint)
         User reviews candidates → builds vocabulary + extends shared resources in knowledge.db
         ↓
[5]    RETAG            Pass 2: text-only LLM re-tags descriptions against vocabulary
                        → retag_output table
         ↓
     ★  NEW_TERMS REVIEW (human touchpoint)
         Frequency-ranked queue of LLM-proposed terms; Accept merges into tags_json immediately.
         ↓
[6]    WRITE-BACK       ExifTool syncs descriptions + keyword tags to file XMP metadata
                        (dirty set only; always preceded by selective analysis pass)
         ↓
[7]    EXPORT           KB export (vocabulary.csv, corrections.yaml, patterns.yaml)
                        + CSV summaries importable into main app
```

**Early workflow:** Ingest → Analyse → Normalization Review → Normalize is a complete, self-contained first session that produces real KB value with no GPU, no hashing, and no LLM. Users can build out the normalization layer on filename/path tokens before any expensive stages run.

**Duplicate file routing:** Every ingested file gets its own `files` row regardless of whether it is a duplicate. Stage 2 (Hash) detects SHA-256 matches and sets `canonical_id` on the later-seen file. CPU-bound stages run on all rows including duplicates — each copy may carry different path tokens, filename patterns, or embedded metadata worth mining. GPU/LLM stages run on canonical files only; duplicates inherit the canonical description via a join:

```sql
JOIN descriptions d ON d.file_id = COALESCE(f.canonical_id, f.id)
```

Write-back (Stage 6) runs against all files: canonical and duplicates both receive the final metadata.

**Aesthetic scoring** is a standalone optional operation independent of the main pipeline. It can run at any point after Ingest. Running after Hash is recommended (SHA-256 dedup skips redundant scoring), but Hash is not a hard prerequisite.

```
enrich aesthetic --kb <name>           # score all unscored files
enrich aesthetic --kb <name> --force   # re-score all files
enrich aesthetic --kb <name> --writeback  # score + write XMP:Rating to files
```

Scores are stored in `file_aesthetic` (one row per model per file; `model_name` values: `nima_mobilenet`, `clip_vit_b32`, `combined_rank`). For XMP:Rating write-back, `combined_rank` maps to 1–5 stars via `max(1, ceil(combined_rank_score × 5))` (clamping ensures 0.0 maps to 1, not 0, which is outside the valid XMP:Rating range).

---

## Normalization Layer — Four Instruments

Processing order within a token stream:

```
1. REJECT     → strip token from text entirely (stored in reject_tokens; use for nonsense only)
2. CAPTURE    → extract structured value (date, serial, code); keep or discard token
3. SUBSTITUTE → transform token text via regex replacement
4. CORRECT    → exact-string replacement to canonical form
```

Order matters: Reject runs first so genuinely meaningless tokens are never even considered for capture. Substitutions run before corrections so a substitution can produce a string that a correction then refines.

### Capture instrument

Extracts structured metadata from tokens into named fields via the `file_captured_fields` EAV table. Has a `keep_token` flag:

- `keep_token: false` — discard token after extraction (dates, serials: noise in text after capture)
- `keep_token: true` — keep token in normalized text AND extract to field (project codes, meaningful IDs)

Single-group capture example:
```yaml
capture:
  - pattern: '^(\d{8})$'        # 8-digit date: YYYYMMDD
    label: date_yyyymmdd
    extract_as: file_date_full   # field_name in file_captured_fields; user-defined, no fixed set
    format_str: "{1:0:4}-{1:4:6}-{1:6:8}"
    value_type: date
    date_precision: day          # full date — stored as e.g. '20160929'
    keep_token: false
```

The `extract_as` name is user-defined — any string is valid. No column is added to `files`; the EAV table accommodates arbitrary capture targets without schema changes. One extraction target per rule.

**`format_str` spec:** A lightweight template applied to regex capture groups. `{0}` = full match, `{1}` = group 1, with optional slice notation `{1:4:6}` meaning characters at index 4 and 5 of group 1 (end index exclusive, matching Python slice convention). Literals outside braces pass through unchanged. Evaluated by a purpose-built parser — not Python `str.format()`, not `eval()`.

Captured values inject into the vision prompt as natural language context:
- `value_type=date` → *"The filename indicates this was filmed on September 29, 2016."*
- `value_type=code` → *"Project code: BC-2019-0042."*
- `value_type=time` → *"Recorded at 09:48:14."*

Multiple capture rules can fire per file. A filename like `160929_094814_BC-Hwy-97-C_clip001.mp4` might produce three `file_captured_fields` rows (file_date, file_time, highway_code).

**Pattern precision matters.** When multiple token types share a surface pattern (6-digit dates `160929` and 6-digit times `094814` look identical as shapes), the regex must use range constraints to distinguish them. The Normalization Review UI should show 3–5 sample values per group so users can verify patterns match the intended tokens before applying them.

### Reject instrument

Strips a token from normalized text entirely before any downstream tool receives it. Stored in `reject_tokens` in `knowledge.db` so the decision persists across re-runs. Applied first in the pipeline — before Capture — so rejected tokens are never captured.

Use only for tokens that are provably meaningless: garbled character sequences, camera firmware codes, corrupted strings. If uncertain whether a token has context value, use Ignore (stoplist) instead — it suppresses the token from review queues while leaving it in the text flow.

### Substitute instrument

Regex pattern → replacement string. Applied after Reject and Capture.

```yaml
substitute:
  - pattern: '\bH(\d+)\b'
    replacement: 'Highway \1'
    label: bc_highway_codes
    applies_to: both    # filename | description | both
```

`applies_to` lets substitution rules run on description text (abbreviation expansion) while Reject and Capture are scoped to filename/path tokens only.

### Correct instrument

Exact string → canonical string. Case-insensitive. Applied after substitutions.

```yaml
# corrections.yaml
TuckInleted: "Tuck Inlet"
BurnabyNorth: "Burnaby North"
```

---

## Normalization Analysis Pass (Stage 0.5)

Pure string analysis — no LLM, runs in seconds across thousands of files. Scans all filenames/paths, tokenizes, classifies by detected pattern type, groups by class, ranks by frequency.

### Review queue format

```
GROUP: 6-digit numeric  ─────────────────────── 1,694 tokens across 847 files

  ▸ Likely dates (month+day-constrained) — 248 tokens
    160929   124 files   [Capture as date_yymmdd]  [Ignore]  [Correct to: ___]  [Reject]
    151203    98 files   ...
    ──────────────────────────────────────────────────────────────────────────────────────
    [Capture all as date_yymmdd]  [Ignore all]

  ▸ Likely times (hour+minute-constrained) — 124 tokens
    094814   124 files   [Capture as time_hhmmss]  [Ignore]  [Correct to: ___]  [Reject]
    ──────────────────────────────────────────────────────────────────────────────────────
    [Capture all as time_hhmmss]  [Ignore all]

  ▸ Unclassified — 47 tokens
    ⚠ Mixed meanings likely — review individually
    123456    87 files   [Ignore]  [Correct to: ___]  [Reject]

GROUP: Short alphanumeric codes  ──────────────── 143 tokens across 89 files
  ⚠ Mixed meanings detected — bulk actions disabled
  H6    89 files   [Ignore]  [Correct to: Highway 6]  [Reject]
  [Create regex rule: H(\d+) → Highway \1]   ← inferred from group pattern

GROUP: CamelCase compound terms  ─────────────── 67 tokens across 234 files
  TuckInleted   47 files   [Ignore]  [Correct to: Tuck Inlet]  [Reject]
```

**Decisions Made panel** (below pending queue):
```
IGNORED        construction (domain stoplist)        [Remove]
REJECTED       ©2019Govbc (garbled watermark)        [Remove]
CAPTURED       160929 → file_date (date_yymmdd)      [Remove]
CORRECTED      TuckInleted → Tuck Inlet              [Remove]
```
[Remove] deletes the entry from `knowledge.db` and returns the term to the pending queue on next refresh.

**Mixed-semantic groups:** When a pattern group contains tokens with different likely meanings, the system runs a value-range classification pass before rendering the group. Well-defined numeric ranges produce reliable sub-groups with their own bulk actions. The unclassified remainder shows a ⚠ warning and disables bulk actions — requiring individual token review.

**Frequency ranking** is the key ergonomic feature: three bulk actions on the top groups can clean 80%+ of noise in under a minute on a large corpus.

### Path Hierarchy Analysis

**Common prefix stripping.** If all ingested files share a common path prefix, that prefix is administrative noise. The Analyse stage detects the longest common prefix across all `files.path` values and strips it before tokenization. The stripped prefix is shown in the UI; the user can override if the stripping was incorrect.

**Depth-position analysis.** After prefix stripping, the Analyse stage counts the frequency distribution of tokens at each path depth. The review queue surfaces this as a structural summary before the token groups:

```
PATH STRUCTURE (from 1,847 files after prefix stripping)

  Depth 1:  23 unique values — likely project identifiers
            BC-Highways (1,204 files)   Bridge-Projects (643 files)

  Depth 2:  87 unique values — likely contract or route identifiers
            Contract-123 (234 files)   BC-5 (198 files)   BC-1 (156 files) ...

  Depth 4:  date-like pattern (YYYY-MM-DD) — 847 files
            [Extract as file_date for all depth-4 date tokens?]  [Skip]
```

### Semantic Pattern Type Detection

The pattern classifier detects semantic type using value-range analysis and structural heuristics, then proposes a specific `extract_as` field name and action:

| Detected type | Example tokens | Proposed action |
|---|---|---|
| Date (YYMMDD) | 160929, 241115 | *"Extract as `file_date` — found in 847 files"* |
| Time (HHMMSS) | 094814, 143022 | *"Extract as `file_time` — found in 847 files"* |
| Sequential counter | _001, _002, _003 | *"Likely auto-generated sequence — recommend IGNORE"* |
| Alphanumeric code (N-NNN) | Contract-123 | *"Extract as `contract_id` — 87 unique values"* |
| Route code (prefix+digits) | BC-1, BC-5, BC-97 | *"Extract as `route_number` — 23 unique values"* |
| CamelCase compound | TuckInleted, BurnabyNorth | *"Likely place name — correct individually or create split rule"* |

The proposed `extract_as` name is editable before accepting. Confidence is shown (high / medium / low). Low-confidence proposals show individually without bulk actions.

### Cross-Source Correlation

When a path token also appears in embedded metadata fields (from `file_metadata_keywords` after Extract Metadata runs), the token is flagged:

```
  BC-5    198 files   ★ also appears in metadata keywords (34 files)
          [Capture as route_number]  [Ignore]  [Correct to: BC Highway 5]  [Reject]
```

The ★ badge signals that this token has been independently validated by embedded metadata. Cross-source tokens should be prioritised for vocabulary promotion.

### Entity Table Seeding from Patterns

When the Analyse stage detects a pattern class with many unique values (default threshold: 5+), it surfaces an entity table proposal:

```
GROUP: Route codes (BC-N pattern)  ──────── 23 unique values across 198 files

  BC-5    198 files
  BC-1    156 files
  ...

  [Capture all as route_number]
  [Scaffold Highway Routes entity table from these 23 values]
  [Ignore all]
```

*"Scaffold entity table"* creates a pre-populated entity table template in knowledge.db with the detected values as seed rows and a `route_number` key column.

### LLM-Assisted Capture Rule Naming

When the user creates a capture rule for a pattern the system cannot automatically classify, the text model (lazy import — only called if the user clicks *"Suggest field name"*) proposes an `extract_as` name with brief reasoning:

```
  Pattern: [A-Z]{2,4}-\d{3,5}   (47 tokens, e.g. AB-1234, XY-567)
  [Suggest field name ▾]
      → contract_id   "These look like alphanumeric contract or project codes
                       with a 2-4 letter prefix and numeric suffix."
      [Accept: contract_id]  [Edit]  [Ignore]
```

This is a low-cost LLM call on a small token sample (5–10 examples). It fires only on demand, not automatically.

---

## Classify Stage (Stage 1.8)

Rule-based derivation pass. Takes already-extracted structured values — captured fields, EXIF fields, entity matches — and applies rules to produce higher-level semantic tags. No ML, no text analysis: fully deterministic. Runs after Normalize (1) and Extract Metadata (1.5); re-runnable at any point.

### Inputs

| Source | Available when | Examples |
|---|---|---|
| `file_captured_fields` | After Normalize | `file_date`, `file_time` |
| `file_metadata_fields` | After Extract Fields (1.6) | `CreateDate`, `FocalLengthIn35mmFormat`, `ImageWidth`, `ImageHeight`, `Flash` |
| `file_entity_matches` | After Entity Match (1.7) | Matched person records for life_event rules |

### Rule categories

**Calendar rules** — derive temporal context from any known date:
- Fixed events: Christmas Day, Christmas Eve, Halloween, Remembrance Day, New Year's Day, Canada Day, etc. (locale-configurable)
- Variable events: Easter, Canadian Thanksgiving, US Thanksgiving, Mother's Day, Father's Day (algorithm-based; `match_type='computed'`)
- Season: Spring/Summer/Autumn/Winter (hemisphere-aware; GPS can inform hemisphere if available)
- Time of day: Morning/Afternoon/Evening/Night (from EXIF capture time)
- Decade: "1970s", "1980s" (from year component of any date)

**Technical rules** — derive photographic characteristics from EXIF:
- Orientation: Landscape / Portrait (ImageWidth vs ImageHeight comparison)
- Shot type: Ultra-wide (≤18mm), Wide angle (≤28mm), Normal (28–70mm), Telephoto (≥70mm) — from FocalLengthIn35mmFormat
- Depth of field: Shallow (aperture < f/2.8), Deep (aperture > f/8)
- Motion: Long exposure (shutter speed > 1/30s); Flash fired (EXIF flash flag = 1)
- Panoramic: aspect ratio > 2:1

**Life event rules** — correlate file date against people registry:
- Birthday: file month-day matches a person's birth_date month-day → "James Penner's birthday (age 22)"
- Wedding: file date matches a marriage life_event → surfaces both partners
- Anniversary: file month-day matches a marriage event in a different year → "Donald & Cathie Penner — 10th anniversary"
- Memorial: file date near a death life_event

**Geographic rules** — derive location context beyond entity matching:
- Hemisphere from GPS latitude (affects season calculation)
- Country from matched location entity (selects holiday locale)

### Date precision and rule firing

Classify rules carry a `minimum_precision` field. A rule only fires if the file's date value meets the required precision level:

| precision | Example value | Notes |
|---|---|---|
| `century` | `"1900-1999"` | Range notation; unambiguous at this level |
| `decade` | `"1970s"` | "s" suffix convention; unambiguous |
| `year` | `"1978"` | ISO 8601 partial date |
| `month` | `"1978-10"` | ISO 8601 partial date |
| `full` | `"1978-10-13"` | Complete date |

Precision is carried as a companion captured field `file_date_precision` alongside `file_date` in `file_captured_fields`. Rules requiring `full` precision skip files with `month` or lower. A season rule requiring `month` fires for both `month` and `full` dates.

### Uncertain date tokens from filenames

Some corpora use explicit uncertainty markers in filename dates. The following token patterns are recognised by the Analyse stage and produce the corresponding precision levels after Normalize:

| Token pattern | Example | Precision | Captured value |
|---|---|---|---|
| `^\d{8}$` | `19781013` | `full` | `"1978-10-13"` |
| `^\d{6}xx$` | `197810xx` | `month` | `"1978-10"` |
| `^\d{4}xxxx$` | `1978xxxx` | `year` | `"1978"` |
| `^\d{3}xxxxx$` | `197xxxxx` | `decade` | `"1970s"` |
| `^\d{2}xxxxxx$` | `19xxxxxx` | `century` | `"1900-1999"` |

### Built-in rules vs user-defined rules

Built-in rules (`is_builtin=1`) ship with the tool and cover orientation, focal length ranges, seasons, and common holidays. They are defined at the tool level but toggled and configured per KB — a family archive KB can enable Canadian holidays and life event rules while a transportation KB has those disabled. Locale selection (which holiday calendar applies) is a per-KB config setting.

User-defined rules (`is_builtin=0`) live in `knowledge.db` and cover domain-specific or personal cases not covered by built-ins.

### Output

Results are written to `file_derived_tags`. Tags are available to downstream stages:
- **Describe (3a):** derived tags injected into the vision prompt as context ("This photo was taken on Christmas Day 1986")
- **Suggest (4):** derived tags included in the text pool for vocabulary building
- **Retag (5):** derived tags available as confirmed context for keyword assignment
- **Write-back (6):** derived tags written as XMP keywords alongside vocabulary terms

---

## People & Locations Registers

The people and locations registers are pre-populated entity tables in `knowledge.db` that enable name-based identification, GPS location resolution, and date-event inference without requiring any ML or biometric identification.

### People register

Seeded from a CSV template matching the `people` + `people_names` + `life_events` schema. The tool provides a downloadable CSV template; the user fills it in at whatever level of detail is available and imports via `enrich people import <file.csv>`.

**CSV template columns:** `preferred_name`, `title`, `first_name`, `middle_name`, `last_name`, `family`, `notes`, `birth_date`, `death_date`, and one or more married name / nickname columns. Each row maps to one `people` row plus multiple `people_names` rows. Marriage events are expressed as paired rows with matching `date_marriage` values.

**Name matching in Entity Match (Stage 1.7):** Every entry in `people_names` acts as a trigger word for text-based entity matching. When a known name form is found in a transcript segment, description, or metadata keyword, a `file_entity_matches` row is written linking the file to the person. The `match_type='text'` mechanism in the entity table registry handles this — the people register is registered as a special-purpose entity table with all name forms as trigger aliases.

**Life event inference in Classify (Stage 1.8):** Once a file has a known date (from captured fields or EXIF), the Classify stage cross-references it against `life_events`. Match types:
- Exact date match → strong signal ("this is the event itself")
- Month-day match in a different year → anniversary inference ("10th wedding anniversary", "age 22 birthday")

When a marriage event fires, both `person_id` and `partner_id` are surfaced — the tag reads "Donald & Cathie Penner — Wedding Day" rather than just one name.

**Future identification pathways** (infrastructure in place; implementation deferred):
- `voice_centroid` / `voice_samples` — populated by a future voice enrolment sprint; enables speaker identification in transcripts
- `face_centroid` / `face_samples` — populated by a future face recognition sprint; enables person identification in images and video frames

### Locations register

Seeded from a CSV template. Columns match the existing `Index_of_Locations.csv` convention: `location_name`, `city`, `state_province`, `country`, `country_code`, `locality_general`, `locality_specific`, `locality_type`, `latitude`, `longitude`, `threshold_m`.

The `threshold_m` column controls GPS proximity matching precision: 50m for a specific building or park, 500m for a neighbourhood, 5000m for a city or region. Registered in the entity table registry with `match_type='gps'`.

Location matching is **bidirectional**:

**GPS → location descriptors** (forward direction):
File has GPS coordinates → match within `threshold_m` of a registered location → apply location name, city, province, country, and locality type as derived tags via `file_derived_tags`. Confidence is `'certain'`. Matched location data (city, country, hemisphere) feeds the Classify stage's geographic rules — enabling correct season and holiday resolution.

**Location descriptors → GPS** (reverse direction):
File has location text in metadata (keyword, caption, description) but no GPS coordinates → match location name against the register → propose GPS coordinates for write-back. Confidence is `'inferred'`. The proposed coordinates are the register's reference lat/long; the precision of the proposal reflects the register entry's `threshold_m` (a 5000m city entry proposes city-centre coordinates, not a precise point).

The reverse direction writes a row to `gps_proposals` (corpus.db) rather than writing coordinates immediately. `gps_proposals` is workflow state — separate from `file_derived_tags` so that user decisions (accepted / dismissed) survive Classify re-runs. Whether pending proposals are applied automatically or require user confirmation is a per-KB config setting:

```yaml
classify:
  gps_writeback_from_text: confirm   # 'confirm' | 'auto'
```

- `confirm` — inferred GPS proposals queue in a **GPS Proposals review panel** before write-back; user approves or dismisses each one individually or in bulk. Approved proposals are written via ExifTool at write-back (Stage 6).
- `auto` — GPS coordinates are written automatically during write-back; the `writeback_log` table provides the undo path.

**GPS Proposals review panel** (shown when `confirm` mode is active and proposals exist):
```
GPS PROPOSALS FROM TEXT MATCHES  ──────────────── 23 files

  Butchart Gardens    3 files   → 48.5650°N 123.4700°W  (±50m)   [Accept]  [Dismiss]
  Powell River        8 files   → 49.8600°N 124.5500°W  (±5000m) [Accept]  [Dismiss]
  Sooke               12 files  → 48.3800°N 123.7200°W  (±5000m) [Accept]  [Dismiss]
  ──────────────────────────────────────────────────────────────────────────────────
  [Accept All]  [Dismiss All]
```

The source text that triggered each match is shown on expand (the keyword or caption phrase containing the location name).

### CSV import behaviour

- **First import:** seeds the table; all rows inserted
- **Re-import:** rows matched by a stable canonical ID column (`preferred_name` for people; `location_name` + `latitude` + `longitude` for locations); existing rows updated, new rows inserted, no deletions
- **Merge conflicts:** surfaced as warnings, not errors; user resolves manually

---

## Normalization vs Suggestion — Sequential, Not Parallel

Both stages scan filename/path/metadata text AND produce candidate terms for review. The distinction is extraction method, not text source:

| | Normalization (Stage 0.5) | Suggestion (Stage 4) |
|---|---|---|
| **Extractor** | Pattern-based token classifier | spaCy POS / noun chunks |
| **Source text** | Filename tokens, path segments, metadata | ALL text including descriptions |
| **Extra capabilities** | Capture (dates, codes), Reject (strip), Ignore (stoplist), bulk regex rules | NEW_TERMS feedback slot |
| **Shared** | Accept/Ignore/Reject/Correct actions, vocabulary, stoplist, corrections dict |

They are **sequential refinements**: Normalization runs first → cleaner text → Suggestion gets better noun chunk extraction → cleaner candidate queue.

**Filename context in Stage 3:** The normalization layer processes filename tokens into `normalized_filename` and captured fields before the vision model runs. The aggregation focus should use these normalized outputs rather than calling an LLM gate on the raw filename.

---

## Normalization by Source — Tiered Depth

| Source | Available when | Reject/Capture | Substitute | Correct |
|---|---|---|---|---|
| Path segments | always | yes | yes | yes |
| Filename tokens | always | yes | yes | yes |
| Existing keyword tags | after Extract Metadata | — | yes | yes |
| Metadata text fields (title, caption) | after Extract Metadata | — | yes | yes |
| Describe text | after Describe | — | (optional) | — |

**Existing keyword tags** are the highest-value metadata target: if 200 files were previously tagged `tuck-inleted`, one correction rule standardizes all 200 before the Suggest pass runs.

**Description text normalisation** runs as a dedicated post-Describe pass (between stages 3 and 4). After the vision model populates `description_raw`, any `substitute_rules` with `applies_to='description'` or `applies_to='both'` are applied to produce `description_normalized`. `description_raw` is preserved unchanged. If no description substitute rules exist, `description_normalized` is set equal to `description_raw`.

**Media type routing at Describe:** Media type for describe routing is determined per-file by extension at describe time using the `ext` column on `files`. Images route to `describe.py`; video files route to `video.py` → `video_frames` table.

---

## Three Shared Resources (in knowledge.db)

All three are read and written by both the Normalization review and the Suggestion review:

```
vocabulary      — accepted terms (the whitelist / keyword universe)
stoplist        — ignored terms (suppressed from review queues; still passed to downstream tools)
corrections     — raw → canonical mappings
```

`capture_rules`, `substitute_rules`, and `reject_tokens` are Normalization-specific (structural rules for token-level processing only).

### Vocabulary = whitelist = keyword universe

Terms can only become file keywords if they're in the vocabulary:

```
Vocabulary (whitelist)  →  Pass 2 classification  →  per-file keyword tags  →  ExifTool write-back
```

Each vocabulary entry has exactly one canonical term — the form that gets written as a keyword tag. Synonyms are equivalent names stored alongside the canonical term; they are never standalone vocabulary entries.

```
vocabulary
  id
  term TEXT           ← canonical term: "Highway 1"
  synonyms_json TEXT  ← ["Trans-Canada Highway", "TCH", "Trans Canada"]
  source TEXT
  added_at
  write_synonyms INTEGER  ← NULL = follow global KB setting; 0 = canonical only; 1 = include synonyms
```

The corrections table maps all surface forms to the canonical term:
```
TCH              → Highway 1   (correction_kind: abbreviation)
Trans-Canada     → Highway 1   (correction_kind: alias)
```

### Synonym promotion from corrections

Aliases and abbreviations often deserve a second role: appearing in `vocabulary.synonyms_json` so the LLM recognises them in Pass 2 description text. The corrections editor offers an opt-in toggle:

| correction_kind | Toggle shown? | Default |
|---|---|---|
| typo | No | n/a — typos have no synonym value |
| abbreviation | Yes | **ON** — abbreviations almost always remain useful as recognition terms |
| alias | Yes | **OFF** — aliases are ambiguous; some are deprecated names, not permanent synonyms |

### How synonyms reach Pass 2

Pass 2 handles synonym forms in description text by receiving the full vocabulary with synonyms as recognition context in its prompt:

```
Known vocabulary terms (always tag with the canonical form):
  Highway 1  (also known as: Trans-Canada Highway, TCH, Trans Canada)
```

The LLM recognises "Trans-Canada Highway" in the description and tags the file with "Highway 1." Synonyms are recognition hints; they never appear as output tags unless write-back is configured to include them.

### Write-back: canonical only vs. canonical + synonyms

**Global KB setting** (default: canonical only) with **per-entry override** via `vocabulary.write_synonyms`:

- `NULL` → use global `write_back.include_synonyms` setting
- `0` → canonical only (even if global is `true`)
- `1` → canonical + all synonyms from `vocabulary.synonyms_json`

**Resolution order** when assembling the keyword list for a file:
1. Start with all canonical terms that apply (from `retag_output.tags_json`)
2. For each term, look up `vocabulary.write_synonyms` and apply the resolution above
3. Deduplicate: if a synonym of one term is the canonical of another, include it once
4. Sort alphabetically before writing to ExifTool

---

## Suggestion Analysis — Three Levels (Stage 4)

The Suggest stage is primarily a **KB-building pass**, not a file-tagging pass. Its job is to grow the vocabulary, surface synonym relationships, and hint at taxonomy structure. Per-file keyword assignment happens downstream in Stage 5 (Retag).

### Level A — Linguistic (spaCy POS)

Fast, no GPU. Loads `en_core_web_sm`, assembles per-file text from all available sources (path components, filename stem, canonical metadata fields flagged `enrichment_text=true`, vision descriptions, and transcript text where available), and extracts lemmatised nouns and noun chunks. If Describe has not yet run for a file, `descriptions` is omitted without error. Produces a frequency table — term → file count — filtered by a configurable minimum file threshold (default: 3 files). Terms already in vocabulary or stoplist are excluded. Runs in seconds across thousands of files.

### Level B — Co-occurrence Graph (NPMI)

Moderate cost, CPU only. Computes pairwise NPMI (Normalised Pointwise Mutual Information) across all per-file term sets from Level A.

**Memory-safe accumulation:** Level B uses streaming co-occurrence counting with `collections.defaultdict(int)` — never builds a full n×n term matrix.

```python
from collections import defaultdict
from itertools import combinations

term_counts = defaultdict(int)
pair_counts = defaultdict(int)
doc_count = 0

for term_set in iter_file_term_sets(conn):   # streaming; no full load into RAM
    doc_count += 1
    for t in term_set:
        term_counts[t] += 1
    for pair in combinations(sorted(term_set), 2):
        pair_counts[pair] += 1

# NPMI for pair (a, b):
# npmi = log(p_ab / (p_a * p_b)) / -log(p_ab)
# where p_x = count_x / doc_count, p_ab = count_ab / doc_count
```

Builds a NetworkX undirected graph with NPMI as edge weight; runs Louvain community detection to find term clusters. Hub terms (high betweenness centrality) are flagged. Edges below `thresholds.npmi_min_weight` (default: `0.1`) are excluded. Prerequisite for Level C.

**Progress during Level B:** Two phases — "Counting co-occurrences: N/M files" while streaming, then "Building graph: K terms" while constructing NetworkX graph and running Louvain. SSE stream emits separate progress events for each phase.

**Synonym surface:** Terms with very high NPMI and similar cluster membership often turn out to be interchangeable domain synonyms. The review queue offers "Accept as synonym of [term]" alongside plain Accept.

**Taxonomy hints:** Clusters suggest vocabulary groupings informally. The cluster label (from Level C) and membership list give the user raw material for structuring their taxonomy manually.

### Level C — LLM Cluster Labelling

Uses the existing text model (no additional model needed). For each Level B cluster, assembles representative file texts and submits them to the LLM with existing vocabulary as "already known" context. LLM proposes 3–8 new canonical terms per cluster with reasoning. Stores reasoning in the `notes` field on each suggestion row — visible in the review queue so users can evaluate the LLM's logic before accepting.

Requires Level B to have run. Gated in the UI: `[Run Level C]` disabled with "Run Level B first" until clusters exist.

### Shared output

All three levels write to the same `candidates` table in corpus.db with a `source` column (`level_a` / `level_b` / `level_c`). The Suggestion Review queue shows all levels together, filterable by source. Level B rows with shared `cluster_id` render as a collapsible disclosure. Level C `notes` render as inline expandable text below each Level C candidate row.

---

## New Terms Review (third human touchpoint)

After Retag (Stage 5), the LLM may propose terms in `retag_output.new_terms_proposed_json` that weren't in the vocabulary at the time it ran. The New Terms Review surfaces these for the user to evaluate and promote.

**Interface:** Same structural pattern as the Normalization Review — frequency-ranked list, per-row action buttons, bulk actions, Decisions Made panel with undo. Simpler in scope: no token grouping, no regex inference, no Capture instrument.

### Queue format

Flat list sorted by term frequency — how many files' `new_terms_proposed_json` contain this term:

```
"embankment"     47 files   [Accept]  [Ignore]  [Correct to: ___]  [Reject]
"abutment"       23 files   [Accept]  [Ignore]  [Correct to: ___]  [Reject]
"soffit"          8 files   [Accept]  [Ignore]  [Correct to: ___]  [Reject]
─────────────────────────────────────────────────────────────────────────────
[Accept all above threshold]   [Ignore all below threshold]
```

### Actions

| Action | Stored in | Notes |
|---|---|---|
| Accept | `vocabulary` | source=`new_terms`; merged into tags immediately |
| Ignore | `stoplist` | Suppressed from future New Terms queues; term still passes to models |
| Reject | `reject_tokens` | Strips term from text entirely; use only for nonsense proposals |
| Correct to | `corrections` | Proposed term was close but not canonical |

### Post-accept behaviour — merge at accept time, no automatic LLM re-run

When the user accepts a term, it is immediately merged into `tags_json` for every `retag_output` row whose `new_terms_proposed_json` contains it — no second LLM call required. `retag_output.retag_status` remains `'done'` — the row is not reset. A full Retag re-run via `enrich retag --force` is always available if the user wants the LLM to re-run with the expanded vocabulary.

### Evidence panel

Clicking any term opens a side panel showing 3–5 sample files with the sentence from the description where the term appeared. Not available in the Normalization Review (raw tokens have no sentence context); unique to this queue.

---

## Field Map (per-KB)

The field map (`field_map.csv`) defines which metadata fields exist in the corpus and how the KB builder should treat them. It is stored inside the KB folder — it is KB-specific, not global.

### Three roles

**Suggestion source (text pool):** Only fields flagged `enrichment_text=true` contribute. Without a field map, falls back to three baseline fields: `XMP-dc:Description`, `XMP-dc:Subject`, `IPTC:Keywords`.

**Normalisation target:** The normalisation stage applies corrections to existing keyword tags, but only for fields it knows about.

**Write-back target:** When syncing vocabulary tags and descriptions back to files, the builder needs to know which XMP fields to write to.

### field_map.csv columns

```
field_name        full ExifTool tag (e.g. XMP-bcmot:ContractID, IPTC:Keywords)
canonical_name    internal name used throughout the KB builder
priority          integer — when multiple rows share the same canonical_name, lower number wins (1 = highest)
                  matches the main app's field_map.csv convention; NULL treated as 1
enrichment_text   true/false — include values in spaCy text pool
write_back        true/false — KB builder writes to this field on sync
value_type        text | code | date | numeric | keyword_list — informs normalisation behaviour
                  keyword_list: ExifTool returns an array; Stage 1.6 writes one row per keyword to
                  file_metadata_keywords instead of a single scalar row in file_metadata_fields
notes             optional human description of the field's purpose
```

Multiple ExifTool tags can map to the same `canonical_name` with different `priority` values — the first non-null value in priority order is used. This is how standard date fields are handled (e.g., `date_taken` maps to `EXIF:DateTimeOriginal` at priority 1, `QuickTime:CreateDate` at priority 2, etc.), matching the main app's field resolution pattern.

### Write-back field selection — precedence

**`field_map.csv` always takes precedence** once it exists. `config.yaml write_back.fields` serves as the **fallback** only before `field_map.csv` has been generated, or for any field in the config list but absent from `field_map.csv`. If a field is in `config.yaml write_back.fields` but has `write_back=false` in `field_map.csv`, it is **not** written.

### field_map.csv is system-generated, not hand-authored

**Built-in field registry (ships with the tool):** An internal `default_fields.csv` maps ~50 well-known ExifTool field names (IPTC, XMP-dc, EXIF, QuickTime, GPS) to canonical names and pre-configured defaults. Representative entries:

```
field_name                  canonical_name      enrichment_text  write_back  value_type
XMP-dc:Description          description         true             true        text
IPTC:Caption-Abstract       caption             true             false       text
XMP-dc:Subject              keywords            true             true        keyword_list
IPTC:Keywords               keywords_iptc       true             true        keyword_list
XMP-dc:Title                title               true             false       text
EXIF:DateTimeOriginal       date_taken          false            false       date
QuickTime:CreateDate        video_create_date   false            false       date
EXIF:Make                   camera_make         false            false       text
EXIF:GPSLatitude            gps_latitude        false            false       numeric
EXIF:GPSLongitude           gps_longitude       false            false       numeric
```

**Corpus-aware auto-generation:** After Extract Metadata runs, the tool:
1. Scans which fields actually appear in `file_exif.metadata_json` across the corpus
2. Matches against the built-in registry
3. Writes `field_map.csv` containing only matched fields present in the corpus
4. Surfaces unmatched fields (custom namespaces) via the Unknown Field Scanner

**Staleness:** if `field_map.csv` changes, files whose `file_metadata_fields.extracted_at` predates the change need re-extraction. Re-extraction reads the already-stored JSON blob — no ExifTool call required.

---

## Date Resolution

Files can have dates from multiple sources — normalization capture rules (e.g. a date encoded in the filename), ExifTool metadata fields (e.g. `EXIF:DateTimeOriginal`), or custom domain fields. The "authoritative date" for a file is a domain decision: for a corpus where operators systematically named files with shoot dates, the filename date is more reliable than the camera's internal clock. For a corpus of scanned archival material, a metadata field set during digitisation may be the only date source. For historical material, a precise date may not be knowable at all — only a decade or century.

Date resolution is a **per-KB setting** configured through the UI (KB Settings → Date Resolution) and stored in per-KB `config.yaml`.

### Precision levels

Each date source declares a precision level, from finest to coarsest:

| Precision | Meaning | Encoded value | Example natural language |
|---|---|---|---|
| `day` | Specific date | `20160929` | "filmed on September 29, 2016" |
| `month` | Year and month only | `201609xx` | "filmed in September 2016" |
| `year` | Year only | `2016xxxx` | "filmed in 2016" |
| `decade` | Decade only | `196xxxxx` | "filmed in the 1960s" |
| `century` | Century only | `19xxxxxx` | "filmed in the 20th century (1900–1999)" |

The precision-encoded value format uses `x` to mark unknown digits, matching the main app's AssetDate convention. The first N significant digits are filled; trailing digits are `x`. For a decade, the first 3 digits are known (`196` = 1960s); for a century, the first 2 (`19` = 1900–1999).

Precision is declared at two points:

**Capture rules:** the user declares `date_precision` when defining a capture rule with `value_type: date`. A YYYYMMDD filename token produces `day` precision; a bare 4-digit year token produces `year` precision; a path segment like "1960s" matched by a custom rule produces `decade` precision.

**Metadata fields:** precision is inferred from the ExifTool field type. `EXIF:DateTimeOriginal` (a full datetime) infers `day`. A field containing only a year value infers `year`. The built-in field registry includes inferred precision for all standard date fields.

### Configuration

The date resolution config in per-KB `config.yaml`:

```yaml
date_resolution:
  canonical_name: asset_date    # stored in file_captured_fields; flows into vision prompt + text pool
  sources:
    - capture:file_date_full    # day precision — YYYYMMDD from filename (authoritative if present)
    - capture:file_date_short   # day precision — YYMMDD from filename
    - metadata:date_taken       # day precision — EXIF:DateTimeOriginal (fallback)
    - capture:file_date_year    # year precision — bare 4-digit year from filename
    - capture:file_date_decade  # decade precision — e.g. "1960s" captured from path segment
    - capture:file_date_century # century precision — e.g. "19th century" captured from path
```

Each entry uses a prefix:
- `capture:<extract_as_name>` — references a capture rule by its `extract_as` field name (`file_captured_fields.field_name`)
- `metadata:<canonical_name>` — references a field_map.csv canonical name (`file_metadata_fields.canonical_name`); itself resolved from multiple ExifTool tags via the `priority` column

**Resolution is strict priority:** the first source in the list with a non-null value wins, regardless of its precision. The user controls the authority hierarchy by ordering the list. If a filename with a year-only date should lose to a full EXIF date, put `metadata:date_taken` above `capture:file_date_year`. If all filename dates should win regardless of precision, put all `capture:` sources first.

**Capture rule `extract_as` names should be unique per precision level.** Two capture rules that both produce day-precision dates should have distinct `extract_as` names (e.g. `file_date_full` and `file_date_short`) so they appear as separate sources in the resolution list. If a file has tokens matching both rules, both rows appear in `file_captured_fields` and the date resolution config selects between them by priority.

### UI

KB Settings → Date Resolution shows:

- All capture rules with `value_type: date`, labeled with `extract_as` name, declared `date_precision`, and example values from the corpus
- All field_map.csv fields with `value_type: date`, labeled with `canonical_name` and inferred precision
- A drag-reorderable priority list where the user assembles the authoritative source order
- A canonical name input (default: `asset_date`)

The UI surfaces available sources automatically — the user does not need to know internal field names. When a capture rule is created or a metadata field added to the field map, it appears as an available source in the Date Resolution panel.

### Storage

The resolved date is materialized in `file_captured_fields` as the configured `canonical_name` (default: `asset_date`), using the precision-encoded value format. This reuses existing EAV infrastructure and integrates naturally with the vision prompt builder, which already reads captured date fields and selects the appropriate natural language template based on the encoded precision:

```
'20160929'  →  "The filename indicates this was filmed on September 29, 2016."
'201609xx'  →  "The filename indicates this was filmed in September 2016."
'2016xxxx'  →  "The filename indicates this was filmed in 2016."
'196xxxxx'  →  "The filename indicates this was filmed in the 1960s."
'19xxxxxx'  →  "The filename indicates this was filmed in the 20th century (1900–1999)."
```

### When it runs

Date resolution runs as a sub-step of the Normalize stage, after Stage 1.6 (Extract Fields) has populated `file_metadata_fields`. Since Normalize is re-runnable and idempotent (UPSERT semantics on `file_captured_fields`), date resolution can be re-run at any time — after changing the priority order in the UI, or after adding a new capture rule that changes which source wins for a set of files.

If no `date_resolution` config is present for a KB, the sub-step is skipped and no `asset_date` row is written. Individual capture rule date fields (e.g. `file_date_full`) are still written to `file_captured_fields` as normal.

---

## Unknown Field Scanner

Surfaces custom XMP fields present in ingested files that are not yet in the field map. Prevents silent gaps where meaningful metadata goes unnoticed.

### When it runs

**After first ingest** — runs automatically once corpus.db has file metadata. **As a persistent health check** — when new sources are added and re-ingest runs, the scanner checks again.

### What it surfaces per unknown field

```
XMP-bcmot:ContractID    ── 847 files   type: short code   samples: "BC-2019-0042", "BC-2021-0118"
XMP-bcmot:Inspector     ── 612 files   type: text         samples: "J. Penner", "M. Singh"
XMP-bcmot:ProjectPhase  ── 391 files   type: text         samples: "Design", "Construction", "Closeout"
```

Fields are grouped by namespace. File count and sample values (3–5) are shown alongside an inferred value type.

### Decision per field

```
[Add to field map]      → prompts for canonical_name, enrichment_text flag, write_back flag
[Create entity table]   → opens entity table wizard pre-configured with this field as the match trigger
[Ignore]                → adds to ignored_fields in knowledge.db; never re-surfaced
[Defer]                 → stays in scanner queue for later review
```

**`[Create entity table]`** is offered when the scanner infers a field is likely a structured reference (short code or ID type). The wizard pre-fills the trigger word and match field from the field name.

The **known-but-ignored list** persists decisions across runs via the `ignored_fields` table in `knowledge.db`.

---

## Entity Tables

Structured reference tables stored in `knowledge.db` alongside vocabulary and field map. They enrich files by matching metadata values or GPS coordinates against reference data.

### Table definition and naming

Tables are stored in `knowledge.db` as `entity_<name>` (e.g. `entity_bridge`, `entity_highway`). The `entity_table_registry` tracks all registered tables with their trigger configuration. Unlike the main app, the standalone tool imposes no naming constraint on the key column — any column can be designated as the match key during import.

### Match types

**`text`** — two-step trigger mechanism:
1. Word-boundary scan: check whether the trigger word (or any alias) appears in the file's enrichment text (`\bbridge\b`). If not found, skip this table entirely for this file.
2. Key column lookup: search the key column values for any that appear as a substring of the enrichment text. Return the full row for each match.

**`gps`** — compare file GPS coordinates against table `latitude`/`longitude` columns within a `threshold_m` radius. No trigger word needed; runs on all files with GPS data.

### Linked tables

Entity tables can reference other entity tables through `entity_table_links`. Traversal is recursive with cycle detection (visited-table set) and a configurable max depth (default: 3). Linked data appears under `_links` in `file_entity_matches.payload_json`:

```json
{
  "bridge_id": "B-1234",
  "bridge_name": "Coquihalla Summit Bridge",
  "year_built": 1986,
  "_links": {
    "highway":         {"name": "Coquihalla Highway", "route_number": "BC-5"},
    "structure_type":  {"name": "Box Girder", "engineering_category": "Concrete"}
  }
}
```

Links are defined in the UI after table import: select parent column → select linked table → select key column. The `include_in_text_pool` flag on each link controls whether linked data contributes to the Suggest stage's text pool (default: yes).

### Corpus-derived entity seeds

After Extract Fields (Stage 1.6), for fields inferred as entity references (short codes, consistent format), the tool surfaces:

*"Found 34 unique contract IDs across 847 files (e.g. BC-2019-0042, BC-2021-0118). Create a Contracts entity table pre-seeded with these values?"*

### Vocabulary linkage

When importing an entity table, an optional step offers vocabulary promotion:

- **Entity names → vocabulary**: key column values are added to `vocabulary` with `source='entity'`
- **Alias columns → synonyms**: if the CSV has an aliases column, those values become `synonyms_json` entries

### Trigger alias auto-suggestion

After Stage 0.5 (Analyse), the tool scans for morphological variants of the trigger word that actually appear in the corpus (e.g. "bridge" → "bridges", "bridging", "bridge-deck") and surfaces them as suggested aliases.

### CSV import

- **Format**: CSV only; one file per table
- **Column schema**: auto-derived from CSV headers
- **Key column**: user selects during import wizard (any column; no naming constraint)
- **Idempotent**: upsert rows by `_external_id` (row number or natural key)
- **Re-import**: drag a new CSV over an existing table to refresh records

### Entity Match re-runs automatically when entity tables are modified — re-running is always safe (upsert semantics on `file_entity_matches`).

---

## Review Actions — Ignore vs Reject

**Ignore** — adds the term to the shared stoplist in `knowledge.db`. Suppresses it from future review queues. The term still passes through in the text sent to spaCy and LLMs. Use for terms that don't need managing but carry legitimate context value.

**Reject** — strips the token from normalized text entirely before any downstream tool receives it. Use sparingly and only for genuinely meaningless tokens.

**Decision storage — quick reference:**

| Action | Stored in | Notes |
|---|---|---|
| Accept | `vocabulary` | Term enters the keyword universe |
| Ignore | `stoplist` | Suppressed from queues; still passes to downstream tools |
| Reject | `reject_tokens` | Stripped from text before any downstream tool receives it |
| Correct to | `corrections` | raw_term → canonical_term mapping |
| Capture | `capture_rules` | Regex extraction rule (dates, codes, serials) |
| Substitute | `substitute_rules` | Regex pattern → replacement string |

Accept only appears in the Suggestion Review (accepting a candidate term into the vocabulary). In the Normalization Review, every action is structural — tokens don't go to `vocabulary` directly.

**Undo:** Every decision is reversible. A "Decisions Made" panel below the pending queue lists all current entries with a [Remove] button per entry. Removing a decision deletes the record from `knowledge.db` and returns the term to the pending queue on next refresh. There is no session-level undo stack — undo is simply removing a stored decision.

**Rule of thumb:** If uncertain whether a term has any value, use Ignore — the term stays in the text flow and Suggestion can evaluate it with full semantic context. Reserve Reject for tokens that are provably nonsense.

---

## Stopwords

Standard linguistic stopwords (a, the, and, I, of, etc.) are **pre-loaded into `knowledge.db`** with `source='builtin'`, never surfaced in any review queue, and **never stripped from text sent to models**.

spaCy needs full sentences for accurate POS tagging; LLMs need grammatical prose. Filtering happens after NLP extraction, not before.

| Type | source value | Exported in KB? | User action needed |
|---|---|---|---|
| Linguistic stopwords | `builtin` | No | None — transparent |
| Generic media stopwords | `seeded` | Yes | None — loaded from seed file at KB creation |
| Domain stopwords | `domain` | Yes | Discovered through Normalization or Suggestion review; added via Ignore |

The tool ships a `seed/stopwords.txt` containing common media/photography noise that is universally low-signal: *photo, image, video, clip, frame, file, copy, edit, version, backup, original, export, import, folder, album, scan, raw, jpeg, jpg, mov, mp4, dsc, img, screenshot, untitled*, and similar. This loads automatically on KB creation with `source='seeded'`.

---

## KB Seed Data

Every table in `knowledge.db` with `source='domain'` is included in the **KB export bundle**. That same bundle is valid as seed data for a new KB — the importer reads it on creation and populates the relevant tables with `source='seeded'`.

**Seed files shipped with the tool** (`seed/` directory):
```
seed/
  stopwords.txt       — one term per line; loads into stoplist with source='seeded'
  corrections.csv     — raw_term, canonical_term, correction_kind; loads into corrections
```

**KB export bundle format** (output of `enrich export`):
```
<kb-name>/
  vocabulary.csv          — term, synonyms_json, write_synonyms, source
  stopwords.txt           — domain + seeded terms only (builtin excluded)
  corrections.yaml        — exact-string corrections (corrections WHERE type='exact')
  patterns.yaml           — capture_rules + substitute_rules + pattern-based corrections
  reject_tokens.csv       — pattern, is_regex, label, scope
  field_map.csv           — canonical_name, exif_tag, enrichment_text, write_back, rename_token
  entities/
    _registry.csv         — one row per entity table
    _links.csv            — one row per link
    bridge.csv            — entity records for entity_bridge (one CSV per table)
    highway.csv
```

Note: `corrections.yaml` contains exact-string corrections only. Pattern-based corrections (regex `type='pattern'` rows) are exported in `patterns.yaml` under the `pattern_corrections` key alongside capture and substitute rules:

```yaml
# patterns.yaml
capture_rules:
  - pattern: '^([0-9]{2})(0[1-9]|1[0-2])([0-3][0-9])$'
    label: date_yymmdd
    extract_as: file_date
    value_type: date
    format_str: "20{1}-{2}-{3}"
    keep_token: false

substitute_rules:
  - pattern: '(?:Hwy?|Hiway|Highway)\s*(\d+)'
    replacement: 'Highway \1'
    label: bc_highway_numbers
    applies_to: both

pattern_corrections:
  - pattern: 'H(\d+)'
    canonical: 'Highway \1'
    correction_kind: abbreviation
```

---

## Performance Design

### VRAM Management — Expose the Knob, Handle Failure Gracefully

The app does not attempt to manage VRAM automatically. `n_gpu_layers` determines how many model layers run on GPU vs. CPU RAM. The app exposes this directly in `config.yaml` (`vision_gpu_layers`, `text_gpu_layers`, `audio_gpu_layers`) and passes it through to the model load call.

```python
def run_describe(corpus_path, kb_path, config, progress, cancel_event):
    from llama_cpp import Llama
    try:
        llm = Llama(
            model_path=config.models.vision,
            n_gpu_layers=config.models.vision_gpu_layers,
            ...
        )
    except Exception as e:
        raise ModelLoadError(
            f"Vision model failed to load: {e}\n"
            f"This is usually caused by insufficient VRAM.\n"
            f"Try reducing 'vision_gpu_layers' in config.yaml, "
            f"or set it to 0 to run on CPU (slower but works on any machine)."
        ) from e
```

**No silent CPU fallback.** If a model fails to load, the stage fails with a clear `ModelLoadError`. Silent fallback from GPU to CPU would turn a 10-minute job into an 8-hour job with no explanation.

**Health checker pre-flight warning.** Before a GPU stage runs, the health checker estimates VRAM requirement from model file size (~0.5 GB per billion parameters for Q4 quantisation) and surfaces a non-blocking warning if the estimate approaches 16 GB.

**Partial offload.** On a machine with 8 GB VRAM trying to run a 12B model, setting `vision_gpu_layers: 20` (out of ~40 total layers) offloads 20 layers to GPU and runs the rest on CPU RAM. Inference is slower than full GPU but significantly faster than full CPU.

### GPU/LLM Stages (3a, 3b, 5) — Keep the GPU Saturated

**Producer-consumer prefetch:** While the GPU is running inference on file N, a background thread preprocesses file N+1. Two-slot pipeline: one GPU slot, one CPU prefetch slot (depth of 2 — hardcoded, not user-configurable).

**Batch DB commits:** Write descriptions/retag output to corpus.db in batches (e.g. every 10 files), not after each inference call.

**Resume via queue:** Load all pending file IDs at stage start with a single bulk query. Work through the queue without per-file DB checks during inference.

### CPU Stages (0, 0.5, 1, 1.5, 1.6, 1.7, 2, 4A+B) — Fully Parallelisable

All use `ThreadPoolExecutor` with `--workers N` (default: `os.cpu_count() - 1`). Collect worker results and commit to corpus.db in transaction batches rather than per-row.

### Write-Back (Stage 6) — ExifTool Batch Mode

Use ExifTool's `-stay_open True` persistent process mode. One ExifTool session handles all files in the dirty set. **Batch size:** 50 files per batch. The `writeback_log` records each batch's outcome; partial-batch failures are file-by-file, not batch-level.

ExifTool lifecycle in `run_writeback()`:
- Spawned once at stage start, not at server startup
- Terminated cleanly after the dirty set is processed
- `writeback.py` retries once if the process exits unexpectedly, then marks the file as `failed`

### Progress Reporting — In-Memory State + SSE

Progress indicators must never touch corpus.db or knowledge.db. Because the FastAPI server and all worker threads run in the same process, in-memory state is the natural solution.

```python
_progress = {}          # { stage_name: {current, total, rate, eta, status} }
_progress_lock = threading.Lock()
```

The preferred transport is **Server-Sent Events** rather than client-side polling. The worker emits an event whenever meaningful progress occurs (every N files); the client receives updates instantly. The endpoint emits the current `_progress` state immediately on connect so a client that reconnects receives the current state instantly.

```
GET /api/stages/{stage}/stream  →  text/event-stream

data: {"current": 431, "total": 1847, "rate": 2.3, "eta": 617, "status": "running"}
data: {"current": 1847, "total": 1847, "rate": 2.4, "eta": 0, "status": "done"}
```

If the server restarts mid-run, in-memory progress state is lost but the active job also stops. The pipeline resumes from where corpus.db left off when restarted.

---

## CLI Structure

Each stage is independently invokable. `--kb <name>` specifies which KB to operate on (defaults to the active KB in registry.db).

**Pipeline stage commands:**
```
enrich ingest         --kb bc-transportation --sources ./a ./b ./c
enrich analyse        --kb bc-transportation
enrich normalize      --kb bc-transportation
enrich extract-meta   --kb bc-transportation   # Stage 1.5: ExifTool extraction
enrich extract-fields --kb bc-transportation   # Stage 1.6: field_map.csv parse
enrich entity-match   --kb bc-transportation   # Stage 1.7: entity table matching
enrich hash           --kb bc-transportation   # Stage 2: SHA-256 + pHash + dHash
enrich describe       --kb bc-transportation   # Stage 3a: vision model (images + video frames)
enrich transcribe     --kb bc-transportation   # Stage 3b: Whisper audio transcription
                      --retranscribe-model <name>  # re-transcribe only files previously processed with <name>
enrich suggest        --kb bc-transportation --level a --level b --level c
enrich retag          --kb bc-transportation   # Stage 5: text-only LLM re-tag
enrich writeback      --kb bc-transportation   # Stage 6: ExifTool write-back
enrich export         --kb bc-transportation   # Stage 7: KB export
enrich run            --kb bc-transportation   # chains all non-review stages in order
                                               # NOTE: aesthetic is NOT included in enrich run
```

**Aesthetic scoring** (optional; independent of main pipeline):
```
enrich aesthetic      --kb bc-transportation
                      --writeback              # also write scores to XMP via ExifTool
                      --export                 # also include scores in export/ CSV
                      --force                  # re-score already-scored files
```

**KB management:**
```
enrich kb list
enrich kb create <name>
enrich kb create <name> --template general-media
enrich kb create <name> --import-kb path/to/other
enrich kb delete <name>                        # remove from registry (never deletes disk files)
enrich kb set-active <name>
enrich kb health <name>
```

**Source management:**
```
enrich source list    --kb bc-transportation
enrich source add     --kb bc-transportation ./new-folder --type video --recursive
enrich source remove  --kb bc-transportation ./old-folder   # marks removed_at; retains file rows
enrich source purge   --kb bc-transportation ./old-folder   # hard-delete file rows for this source
```

**Flags available on all stage commands:**
- `--kb <name>` — target KB (defaults to active KB from registry.db)
- `--workers N` — thread pool size for CPU-parallel stages
- `--force` — re-run stage even if files are marked complete (see table below)
- `--dry-run` — print what would be processed without writing anything
- `--config PATH` — override default config.yaml location
- `--quiet` / `--verbose`

**Default re-run behaviour and `--force` semantics:**

| Stage | Default (no flag) | `--force` behaviour |
|---|---|---|
| 0 Ingest | skip same path+size+mtime | re-stat all paths; update rows where stat has changed |
| 0.5 Analyse | always reruns — stateless read pass | n/a |
| 1 Normalize | always reruns — UPSERT is idempotent | n/a |
| 1.5 Extract Metadata | skip files with existing `metadata_json` | re-run ExifTool on all files |
| 1.6 Extract Fields | skip files with existing `file_metadata_fields` rows | re-parse all stored JSON through current `field_map.csv` |
| 1.7 Entity Match | skip files already matched for current entity table versions | re-run all matches; upsert semantics |
| 2 Hash | skip files where `sha256` is not NULL | re-hash all files; rebuild `canonical_id` relationships |
| 3a Describe | skip `done`; retry `failed` + `skipped` | reset all canonical files to `pending`; re-run |
| 3b Transcribe | skip `done` + `no_audio`; retry `failed` | reset all eligible files to `pending`; re-run. `--retranscribe-model <name>` limits reset to files where `transcriptions.model = <name>` |
| 4 Suggest | regenerate `pending` candidates only | delete all `pending` candidates; regenerate from current text pool |
| 5 Retag | skip `done`; retry `failed` + `skipped` | reset all to `pending`; re-run |
| 6 Write-back | dirty set only (`writeback_kb_version` mismatch) | write to all files regardless |
| 7 Export | always reruns — read-only snapshot | n/a |

**Three cases that print an explicit warning before proceeding:**

- `--force describe` — *"Re-describing will not automatically clear Suggest or Retag results. Run `enrich suggest --force` afterward to refresh candidates if descriptions changed."*
- `--force hash` — *"Re-hashing will rebuild duplicate relationships. `canonical_id` assignments may change if files were added, moved, or replaced since last hash run."*
- `--force suggest` — only deletes `pending` candidates; `accepted`, `rejected`, and `corrected` candidates are never touched. **Exception when re-running Level B specifically:** Level B cluster IDs are unstable across re-runs (Louvain community detection is non-deterministic). If `enrich suggest --level b --force` is run, all Level C candidates are also deleted (since their `cluster_id` references are now invalid) — a warning is printed: *"Re-running Level B will delete all existing Level C candidates. Level A candidates are unaffected."*

**Human touchpoint behaviour:** `enrich run` exits cleanly when it reaches a review touchpoint. Pipeline stage completion is recorded in `corpus.db`, so re-running `enrich run` after completing a review automatically continues from the next pending stage — no flags, no resume commands needed.

```
$ enrich run

[✓] Ingest      1,847 files
[✓] Analyse     token groups ready for review
[→] Paused: Normalisation Review required

    Open http://localhost:7700 to review filename patterns.
    Run 'enrich run' again after review to continue.
```

**Review navigation shortcuts** — open the persistent server in the browser at the relevant review tab:
```
enrich review normalise   # opens browser to Normalisation Review tab
enrich review suggest     # opens browser to Suggestion Review tab
enrich review new-terms   # opens browser to New Terms Review tab
```

Reviews always require the web UI. The CLI handles all automated stages; the UI handles all human decision stages. Both drive the same underlying functions and share the same `corpus.db` state.

---

## Quick Commands — No KB Required

`enrich quick-describe` and `enrich quick-transcribe` are stateless commands that process files directly without a KB, corpus.db, or any pipeline setup.

```
enrich quick-describe  <path>              # single file or entire folder
                       --focus "..."       # optional domain guidance string
                       --model PATH        # override configured vision model
                       --output PATH       # write to file (CSV or JSON); default: print to terminal
                       --format csv|json
                       --recursive

enrich quick-transcribe <path>             # single audio/video file or folder
                        --model PATH
                        --output PATH
                        --format csv|json
                        --recursive
```

**Output columns:**
- `quick-describe` CSV: `path, description, model, processed_at`
- `quick-transcribe` CSV: `path, transcript, language, duration_ms, model, processed_at`

**Behaviour:**
- No dedup — every file is processed regardless of whether it has been seen before
- No write-back — results go to the output destination only; files on disk are never modified
- `--focus` is injected into the frame prompt and aggregation call, identical to the KB pipeline's FOCUS string
- Both commands respect `n_gpu_layers` from `config.yaml` and the `ModelLoadError` failure path

**Primary use cases:**
1. **Try before you commit** — run `quick-describe` on a sample of files to evaluate model output quality and tune the FOCUS string before creating a KB
2. **Spot-check a new source** — before running `enrich describe` against a new source folder, quick-describe a few representative files
3. **One-off analysis** — produce a description or transcript CSV for files that don't belong in any KB

**Implementation:** `src/cli/quick.py` calls the same underlying functions as the pipeline stages — `run_describe_file()` from `stages/describe.py` and `run_transcribe_file()` from `stages/transcribe.py`. These functions accept an optional `db` parameter: `None` for quick mode (stateless), a live connection for pipeline mode (writes to corpus.db). No duplicated logic.

---

## UI Design

The web UI is a **persistent server** covering all tool functions — not just the three review touchpoints.

**What the UI provides:**
- Pipeline dashboard: per-stage status (complete / pending / running / never run), file counts
- Per-stage cards with Run / Cancel / progress bars
- All three review interfaces as integrated tabs
- Settings panel for config.yaml fields
- KB sync panel
- Documentation tab explaining CLI equivalents and when to prefer them

**UI is the primary design target.** The CLI handles automation, multi-KB batch ops, and tool integration; the UI handles everything else. Both surfaces drive the same underlying functions.

### Pipeline Dependency Manager

The UI's primary workflow feature is a stage DAG where each node knows its prerequisites. When a user requests a stage that has unsatisfied dependencies, the engine walks the graph backward, identifies what needs to run first, presents an execution plan, and runs stages in order — pausing at human review touchpoints.

```
User clicks [Run Suggest]

Dependencies not yet satisfied:
  ✗ Describe (not run)
    ✗ Normalise (not run)
      ✓ Ingest (complete — 1,847 files)

Proposed plan:  Normalise → Describe → Suggest (Level A → B → C)
                ⚠ Will pause for Normalisation Review before Normalise runs
[Run plan]  [Cancel]
```

**Dependency graph:**
```
Ingest (0) → Analyse (0.5) → [Normalisation Review*] → Normalise (1)
                                                               ↓
                                          ┌────────────────────┼─────────────────────┐
                                   Extract Meta (1.5)    Extract Fields (1.6)    Hash (2)
                                          ↓                    ↑
                                   (1.6 requires 1.5)─────────┘
                                          └────────────────────┼─────────────────────┘
                                                               ↓
                                              Describe (3a) + Transcribe (3b) [parallel]
                                                               ↓
                                                     Suggest A → B → C (4)
                                                               ↓
                                                    [Suggestion Review*]
                                                               ↓
                                                           Retag (5)
                                                               ↓
                                                    [New Terms Review*]
                                                               ↓
                                                        Write-back (6)
                                                               ↓
                                                          Export (7)

* = human touchpoint; pipeline pauses and shows review URL / opens review tab
```

**Persistent pipeline status strip:** A sticky status bar visible across all tabs shows the current pipeline state: `Pipeline paused at: Suggestion Review — [Open Review] [View Progress]`. The strip clears when no stage is running and no touchpoint is pending.

**Launch:** `enrich serve` starts the persistent server (default port 7700). `run.bat` launches the server and opens a browser tab.

### UI Patterns — Borrow from Main App Knowledge Tab

**Vocabulary curation browser** — paginated table with domain/category filters, coverage stats, CSV import/export.

**Inline synonym chip editor** — directly in the vocabulary row:
```
Highway 1    [Trans-Canada Highway ×]  [TCH ×]  [Trans Canada ×]  [+ Add]   write_synonyms [○]
```
- Chips are dismissible (×). Enter/Tab commits the typed synonym as a chip. Backspace on empty input removes the last chip.
- Input is **free-text** — the user types any synonym from domain expertise, regardless of whether the pipeline surfaced it.
- Changes auto-save on blur.
- For terms with many synonyms (10+), an `[expand]` link widens the row into a larger inline editor section — still no modal.
- `synonyms_json` stays a plain string array. Per-synonym provenance metadata is not stored.

**Suggestions queue** — paginated list with source badges (level_a / level_b / level_c), file count per term, bulk Accept All / Reject All, sort by novelty or file count.

**Pipeline card + SSE progress** — `stage_card` macro pattern, SSE progress stream, rate/ETA display, cooperative cancel. Model for every stage in the pipeline.

**Health checker** — expandable component rows with status dots (green/yellow/red) and inline guidance:

| Check | Severity | Condition | Fix action |
|---|---|---|---|
| ExifTool present | Error | Not found in `tools/exiftool/` or PATH | "Place exiftool.exe in tools/exiftool/" |
| ffmpeg present | Error | Not found in `tools/ffmpeg/` or PATH | "Place ffmpeg.exe in tools/ffmpeg/" |
| Vision model present | Warning | No GGUF in `tools/models/vision/` | "Place a vision GGUF in tools/models/vision/" |
| Text model present | Warning | No GGUF in `tools/models/text/` | "Place a text GGUF in tools/models/text/" |
| spaCy en_core_web_sm | Warning | Not importable | One-click: `python -m spacy download en_core_web_sm` |
| Source directories | Info | `sources` empty for this KB | "Add a source folder to start ingesting" |
| Corpus non-empty | Info | `files` table empty | "Run Ingest to add files" |
| Vocabulary non-empty | Info | `vocabulary` table empty | "Run Suggest and review candidates to build vocabulary" |
| FOCUS string set | Info | `focus` absent in per-KB config | "Recommended — improves description quality for your domain" |
| Unknown fields pending | Info | Scanner has unreviewed fields | "N unrecognised fields found — [Review in Field Scanner]" |
| Capture rules set | Info | No capture rules; corpus has date/serial tokens | "N filename tokens look like dates — set up a capture rule" |

**Pipeline status dots per KB** — on the KB list page, each KB shows a dot row covering all pipeline stages. Users see at a glance which KBs have completed which stages.

**Vocabulary gap badge** — a persistent badge on the Suggestion Review tab showing the count of terms that appear in the text pool but are not yet in `vocabulary`. Implementation:
```sql
SELECT COUNT(DISTINCT c.term) FROM candidates c
WHERE c.status = 'pending'
AND c.term NOT IN (SELECT term FROM vocabulary)
```
Badge is muted-grey when zero, amber when 1–9, red when 10+. Tooltip shows top-3 gap terms by frequency.

---

## Onboarding and Getting Started

### Principle: Artifacts as Outputs, Not Inputs

Users should never need to understand artifact file formats to build a useful KB. The UI is the authoring tool; vocabulary, corrections, field map, and normalisation rules are serialized records of decisions the user has made through the UI — byproducts, not prerequisites.

### Two-Tier Config

Global config (`kb-builder/config.yaml`) sets tool-wide defaults. Per-KB config (`knowledge-bases/<name>/config.yaml`) overrides specific keys. Merge is deep leaf-level: per-KB wins on any key it specifies; absent keys inherit from global.

```
per-KB value  →  (if invalid)  →  global value  →  (if absent/invalid)  →  built-in default  →  warning + stage disabled
```

| Tier | Keys |
|---|---|
| Global-only | `server.port`, `server.host`, `tools.exiftool`, `tools.ffmpeg`, `tools.ffprobe`, `workers.default` |
| Per-KB-only | `sources`, `focus`, `exiftool_config` |
| Both (per-KB overrides) | `models.*`, `write_back.include_synonyms`, `thresholds.*`, `workers.count` |

**Complete annotated example — global `config.yaml`:**

```yaml
server:
  host: 127.0.0.1
  port: 7700

tools:
  exiftool: tools/exiftool/exiftool.exe
  ffmpeg:   tools/ffmpeg/ffmpeg.exe
  ffprobe:  tools/ffmpeg/ffprobe.exe

models:                                    # (override) auto-discovered from tools/models/ if omitted
  vision: ""                               # path to vision GGUF, or "" for auto-discovery
  vision_gpu_layers: -1                    # -1 = all layers on GPU; 0 = CPU only; N = partial offload
  text:   ""
  text_gpu_layers: -1
  audio:  ""
  audio_gpu_layers: -1

workers:
  default: 4                               # (override)

thresholds:                                # (override)
  npmi_min_weight:   0.1
  suggest_min_files: 3
  phash_threshold:   10
  describe_frames:   9
  scene_threshold:   0.4
  deep_seek:         true
  deep_seek_max_iter: 2

write_back:                                # (override)
  include_synonyms:  false
  confirm_above:     200                   # prompt before write-back when dirty set exceeds this count
  fields:
    - IPTC:Keywords
    - XMP:Subject
    - XMP:Description

# date_resolution is per-KB-only — no global default; omit if not needed
# Configure via UI: KB Settings → Date Resolution
# date_resolution:
#   canonical_name: asset_date
#   sources:
#     - capture:file_date_full     # day precision — YYYYMMDD from filename
#     - metadata:date_taken        # day precision — EXIF:DateTimeOriginal
#     - capture:file_date_year     # year precision — bare YYYY from filename
#     - capture:file_date_decade   # decade precision — e.g. "1960s" from path segment
#     - capture:file_date_century  # century precision — e.g. "19th century"
```

**Complete annotated example — per-KB `config.yaml`:**

```yaml
# Only specify keys that differ from global defaults.

sources:
  - path: D:/Projects/BC-Highways/footage
    file_type: video
    recursive: true
  - path: D:/Projects/BC-Highways/site-photos
    file_type: images
    recursive: true

focus: >
  Transportation infrastructure construction and maintenance projects
  in British Columbia, Canada. Pay attention to: infrastructure type
  (road, bridge, tunnel), construction stage, equipment, and any
  visible contractor names, equipment brands, or location signage.

exiftool_config: .ExifTool_config   # relative to KB folder

models:
  vision:            ../../tools/models/vision/gemma-4-12B-it-QAT-Q4_0.gguf
  vision_gpu_layers: -1
  text:              ../../tools/models/text/Qwen3.5-9B.Q4_K_S.gguf
  text_gpu_layers:   -1
  audio:             ../../tools/models/audio/ggml-medium.bin
  audio_gpu_layers:  -1

thresholds:
  suggest_min_files: 5

date_resolution:
  canonical_name: asset_date    # resolved date written to file_captured_fields; flows into vision prompt
  sources:
    - capture:file_date_full    # day precision — YYYYMMDD from filename (authoritative)
    - capture:file_date_short   # day precision — YYMMDD from filename
    - metadata:date_taken       # day precision — EXIF:DateTimeOriginal fallback
    - capture:file_date_year    # year precision — bare YYYY from filename
```

### Model Auto-Discovery

On startup the tool scans `tools/models/vision/` and `tools/models/text/` for GGUF files.
- One model found → selected automatically
- Multiple found → UI prompts the user to choose
- None found → describe stage card shows *"Place a GGUF file in tools/models/vision/ to enable this stage"*

spaCy model check: on startup the tool checks whether `en_core_web_sm` is importable. If not found, the Suggest stage card shows the command with a one-click fix in the health checker.

### UI as Guide, Not Gatekeeper

Stages are not hard-blocked by configuration gaps — they show soft prompts:

| Situation | Soft prompt |
|---|---|
| No sources configured | Empty state + Add Folder button |
| No FOCUS string | Health item: "Recommended — improves description quality" |
| No vision model | Stage card disabled + "Place model in tools/models/vision/" |
| Level B not run | Stage card disabled + "Run Level B first" |

### KB Creation Wizard

```
Step 1: Name + description     → what domain is this KB for?
Step 2: Add source directories → folder browser (skippable; add later)
Step 3: FOCUS string           → with domain examples (skippable; add later)
Step 4: Seed data              → Blank | General Media | Import from bundle
Step 5: Review + create
```

Steps 2 and 3 are explicitly skippable. After creation the tool immediately offers to run ingest.

### Starter Templates

**Blank** — empty KB, no seed data.

**General Media** — the recommended default. Pre-loads `seed/general-media/stopwords.txt` (media file naming noise: photo, image, video, clip, frame, file, copy, edit, version, etc.) and `seed/general-media/capture_rules.yaml` (8-digit YYYYMMDD dates, 6-digit YYMMDD dates, HHMMSS times, clip numbering, scene numbering, camera auto-naming like DSC/IMG/DJI).

**Import from bundle** — point to a prior KB export directory. Loads vocabulary, stopwords, corrections, capture rules, substitute rules, and reject tokens from the bundle with `source='seeded'`.

### In-UI Editors for All Mutable Artifacts

| Artifact | UI surface |
|---|---|
| Vocabulary | Vocabulary browser — add, edit, delete, import CSV, export CSV; inline chip editor for synonyms |
| Corrections | Corrections table — add/edit/delete rows, bulk import; alias/abbreviation rows offer "Also add as synonym" toggle |
| Capture rules | Rule editor — form fields for pattern, label, extract_as, keep_token |
| Substitute rules | Rule editor — pattern, replacement, applies_to selector |
| field_map.csv | Field map editor — inline table with add/edit/delete per field |
| Entity tables | Entity table browser — import CSV, browse records, define links, edit trigger aliases, toggle vocabulary linkage |
| config.yaml | Settings form — labelled inputs, not raw YAML |
| Ignored fields | Ignored fields list — remove entries to allow re-surfacing by scanner |

---

## KB Sync Tracking

Write-back is a KB→files sync. Tracking which files are in sync with the current KB requires two pieces of state: the current KB version and the KB version at the time each file was last written.

### Version Stamping

Every KB-mutating operation inserts a row into `kb_version`. Current version is always `MAX(id)`.

A file is **in sync** when:
```sql
files.writeback_kb_version = (SELECT MAX(id) FROM knowledge.kb_version)
```

`sync.py` opens `corpus.db` as the primary SQLite connection and executes `ATTACH DATABASE 'knowledge.db' AS knowledge` on startup. The `knowledge.` prefix in all cross-DB queries depends on this ATTACH being active for the connection's lifetime.

A file is **stale** when `writeback_kb_version` is NULL (never written) or less than the current KB version.

### Staleness Detection — Two Approaches

**Conservative (automatic, zero compute cost):** Any KB mutation increments the version. All previously-synced files immediately become stale relative to the new version.

**Selective (on-demand, CPU-only, no LLM):** A targeted analysis pass that uses the `change_type` recorded in each `kb_version` row to run a specific cheap check per change type.

| Change type | Check | Method |
|---|---|---|
| Vocabulary term added | Does new term appear in file's text? | FTS5/LIKE on `description_normalized` + `file_metadata_fields` values |
| Vocabulary term removed | Is term currently in file's tags? | JSON scan of `retag_output.tags_json` |
| Correction added/changed | Does raw_term appear in description or keywords? | Text search on `description_normalized` + `file_metadata_keywords.keyword` |
| Synonym added/changed | Is canonical term tagged? Does synonym set need updating? | Tag set comparison on `retag_output.tags_json` |
| Substitute rule changed | Would re-applying rule to `description_raw` produce different `description_normalized`? | Re-run substitution in Python, string compare |
| Field map change | Does file have data for new `canonical_name` in `file_metadata_fields`? | Simple JOIN query |

```
[Analyse stale files]  →  291 stale files inspected

  247 files: no change to written metadata
   38 files: +1–3 new tags (vocabulary additions)
    6 files: description refined (correction rule applied)

[Write-back 44 changed files]
```

The selective pass is triggered by the user, not automatic. It is the gating step before write-back on large corpora — it prevents unnecessary file churn.

### Write-Back Flow

```
conservative version check   →   identifies stale files (cheap, automatic)
        ↓
selective analysis pass      →   identifies dirty subset (on-demand, moderate cost)
        ↓
write-back                   →   ExifTool batch mode, dirty set only
        ↓
update writeback_kb_version  →   mark written files in sync with current KB version
```

---

## File Structure

```
kb-builder/
  run.bat                        ← first-launch setup + server start:
                                 --   1. create .venv if absent (python -m venv .venv)
                                 --   2. pip install -r requirements.txt
                                 --   3. copy config.example.yaml → config.yaml if config.yaml absent
                                 --   4. start uvicorn; open browser
  requirements.txt               ← pinned pip dependencies; committed to git
  config.example.yaml            ← committed template: all keys documented with defaults and comments
  config.yaml                    ← user's local config; gitignored; created from example on first launch
  registry.db                    ← KB registry: name, path, active status (written by tool; gitignored)

  src/                           ← all Python source
    config.py
    exiftool.py
    cli/
      __init__.py
      pipeline.py                # ingest, analyse, normalize, extract, hash, describe, suggest, retag, writeback, export, run
      kb.py                      # kb create, kb list, kb delete, kb set-active
      review.py                  # review normalise, review suggest, review new-terms
      aesthetic.py
      quick.py                   # quick-describe, quick-transcribe — stateless, no KB required
    api/
      __init__.py
      pipeline.py                # /api/stages/* routes
      kb.py                      # /api/kb/* routes
      review.py                  # /api/review/* routes
      vocabulary.py              # /api/vocabulary/* routes
      progress.py                # /api/progress/* SSE routes
      settings.py                # /api/settings/* routes (config.yaml read/write)
      sources.py                 # /api/sources/* routes
      field_map.py               # /api/field-map/* routes
      aesthetic.py               # /api/aesthetic/* routes
      ui.py                      # page routes
    stages/
      ingest.py                  # Stage 0
      analyse.py                 # Stage 0.5
      normalize.py               # Stage 1
      extract_meta.py            # Stage 1.5
      extract_fields.py          # Stage 1.6
      field_registry.py          # built-in default_fields registry; corpus-aware field_map.csv generation
      entity_match.py            # Stage 1.7
      hash.py                    # Stage 2
      describe.py                # Stage 3a (adapted from VD.1–VD.4)
      transcribe.py              # Stage 3b: Whisper
      video.py                   # Frame pipeline → video_frames
      aesthetic.py               # Optional: NIMA + CLIP scoring
      suggest.py                 # Stage 4
      retag.py                   # Stage 5
      writeback.py               # Stage 6
      sync.py                    # KB sync: version stamps, selective analysis pass, dirty set
      export.py                  # Stage 7
    db/
      corpus.py                  # corpus.db: connection, schema init, named query functions
      kb.py                      # knowledge.db: connection, schema init, CRUD helpers
      migrations.py              # shared migration runner (_migrations table approach)
    pipeline/
      dag.py                     # DEPENDENCIES + INVALIDATES dicts; resolve_plan(); TOUCHPOINTS set
      progress.py                # SseProgressReporter + NullProgressReporter; _progress dict + lock
      cancel.py                  # threading.Event factory; cooperative cancellation pattern
    migrations/                  ← inside src/ so SQL files bundle correctly with the package
      corpus/
        0001_init.sql
      knowledge/
        0001_init.sql

  templates/                     ← Jinja2 HTML templates
    base.html
    components/
      stage_card.html
      review_queue.html
      progress_bar.html
      health_check.html
    pages/
      pipeline.html
      review_normalise.html
      review_suggest.html
      review_new_terms.html
      vocabulary.html
      settings.html
      kb_list.html
  static/
    js/
      htmx.min.js
      browse.js
      review.js
    css/
      main.css

  tests/
    conftest.py                  # shared pytest fixtures: tmp corpus.db + knowledge.db via tmp_path
    unit/
      test_normalize.py
      test_suggest_ab.py
      test_config.py
      test_format_str_parser.py
    integration/
      test_ingest.py
      test_extract_meta.py
      test_extract_fields.py
      test_hash.py
      test_entity_match.py
      test_sync.py
      test_writeback.py
    fixtures/
      corpus/                    ← gitignored; place real files here for local manual testing only
      seeds/                     ← SQL files that seed knowledge.db fixtures for integration tests

  tools/                         ← executables and models (mostly gitignored)
    exiftool/
      exiftool.exe
    ffmpeg/
      ffmpeg.exe
      ffprobe.exe
    models/
      vision/                    ← user-provided vision GGUFs (gitignored)
        .gitkeep
        MODELS.md
      text/                      ← user-provided text GGUFs (gitignored)
        .gitkeep
        MODELS.md
      audio/                     ← Whisper GGUFs (gitignored)
        .gitkeep
        MODELS.md

  docs/
    development/
      ARCHITECTURE.md
      TESTING.md
      API.md
      sprints/
        planned/
        active/
        complete/
    guides/
      getting-started.md
      cli-reference.md
      building-a-knowledgebase.md
      why-use-the-cli.md

  tmp/                           ← gitignored; intermediate files, debug output, test artefacts
    .gitkeep

  knowledge-bases/               ← gitignored; one subfolder per KB; discovered dynamically
    .gitkeep
    bc-transportation/
      knowledge.db               ← vocabulary, corrections, rules, version stamps (never reset)
      corpus.db                  ← accumulated file corpus and description cache (durable)
      config.yaml                ← source dirs, FOCUS string, thresholds, model overrides (optional)
      field_map.csv              ← per-KB field definitions
      .ExifTool_config           ← optional: per-KB ExifTool custom tag definitions
      export/                    ← generated by `enrich export`; safe to delete and regenerate
        vocabulary.csv
        corrections.yaml
        patterns.yaml
        field_map.csv
        descriptions.csv
        tags.csv
        entities/
          _registry.csv
          _links.csv
          bridge.csv
          highway.csv
```

**KB discovery is dynamic + registry-backed.** The CLI and UI scan `knowledge-bases/` for any subfolder containing `knowledge.db` — unregistered KBs are discovered automatically and offered for registration. `registry.db` at the tool root tracks active status, creation date, and component inventory for registered KBs.

**Platform target:** Windows 11 is the primary target. `run.bat` and the bundled binary layout are Windows-specific. The Python source is cross-platform. GGUF inference uses `llama-cpp-python` — on Windows the build must include Vulkan support (`CMAKE_ARGS="-DGGML_VULKAN=ON"`).

**Intentional difference from the main app:** The main app commits its entire `python/` environment for true portability. The KB Builder instead recreates the environment on first launch via `run.bat`. This keeps the git repository small and GitHub-friendly — committing a Python environment with packages like spaCy and onnxruntime would push the repo into multi-GB territory.

---

## Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| FastAPI + uvicorn | pip | HTTP server, SSE streaming | |
| Typer | pip | CLI argument parsing | |
| spaCy | pip | Level A/B text analysis | |
| en_core_web_sm | spaCy model | POS tagging, noun chunks | `python -m spacy download en_core_web_sm` |
| NetworkX | pip | Level B co-occurrence graph | |
| python-louvain | pip | Level B community detection | imports as `community` |
| llama-cpp-python | pip | GGUF inference (vision + text + audio) | Windows: build with `-DGGML_VULKAN=ON` |
| pywhispercpp | pip | Whisper transcription via llama.cpp | Stage 3b |
| Pillow | pip | Image resize / preprocessing for Describe | |
| ExifTool | binary | Metadata extraction + write-back | Bundled in `tools/exiftool/` |
| ffmpeg + ffprobe | binary | Video frame extraction | Bundled in `tools/ffmpeg/` |
| ruff | dev | Import hygiene, unused name detection | Runs in test suite; failures are build failures |
| pytest + pytest-cov | dev | Test runner | |

`aesthetic.py` (optional standalone) additionally requires onnxruntime-directml and model weights for NIMA and CLIP. Mark as optional in `requirements.txt`:

```
# Optional: aesthetic scoring — comment out if not needed (~100 MB)
# onnxruntime-directml
```

---

## Outputs

### Primary: knowledge base (knowledge.db)
- Vocabulary, corrections, normalization rules — the durable domain knowledge
- Directly importable into the main app's knowledge base
- Grows and improves across multiple runs over different material

### Secondary: file metadata (written in place via ExifTool)
- XMP:Description — enriched description
- IPTC:Keywords / XMP:Subject — vocabulary keyword tags
- Portable — files carry their enrichment to any XMP-aware tool

### Tertiary: portable KB export

```
vocabulary.csv      term, synonyms_json, write_synonyms, source
stopwords.txt       domain + seeded terms only (builtin excluded)
corrections.yaml    exact-string corrections only
patterns.yaml       capture rules + substitute rules + pattern-based corrections
reject_tokens.csv   pattern, is_regex, label, scope
field_map.csv       field definitions, text-pool flags, write-back targets
entities/
  _registry.csv     entity table definitions
  _links.csv        parent→child link definitions
  <table>.csv       one CSV per entity table
```

`enrich export --section vocabulary` exports only `vocabulary.csv` without rebuilding the entire export/ folder. Supported sections: `vocabulary`, `corrections`, `patterns`, `field-map`, `entities`. No `--section` flag = full export.

### Quaternary: CSV summaries

```
descriptions.csv    file_path, description, model, processed_at
tags.csv            file_path, tags, refined_description, new_terms_proposed
aesthetic.csv       file_path, nima_score, clip_score, combined_rank, band, scored_at
```

`aesthetic.csv` is generated by `enrich aesthetic --export` independently of `enrich export`.

**Main app import commands:**
```
catalogue import-vocabulary vocabulary.csv
catalogue import-corrections corrections.yaml
catalogue import-patterns patterns.yaml
catalogue import-field-map field_map.csv
catalogue import-entity-tables entities/
catalogue import-descriptions descriptions.csv
```

**`enrich import-to-app`** wraps the full round-trip for users running both tools on the same machine:
```
enrich import-to-app --kb bc-transportation --app-kb bc-transportation
```
Steps: refresh export/ → locate main app KB at registered path → run `catalogue import-kb export/` → print summary. `--dry-run` prints steps without executing. App path is registered once via `enrich import-to-app --register-app-path <path>` and stored in the tool-level `registry.db`.

---

## Testing Strategy

pytest with real SQLite fixtures and a small fixture corpus. A full `docs/development/TESTING.md` is written before implementation begins; this section establishes guiding principles.

### Fixture corpus

Integration tests use **programmatically generated synthetic media** created in `conftest.py` — not committed real files. PIL generates minimal valid JPEGs and PNGs. ffmpeg's `testsrc` filter generates short valid videos. ExifTool writes any test metadata needed onto generated files in `tmp_path`.

`tests/fixtures/corpus/` exists but is gitignored. Users may place real files there for local manual spot-checking; nothing in that directory is required by the test suite.

### Coverage by component

| Component | Approach | Notes |
|---|---|---|
| `normalize.py` | Unit tests | Regex edge cases, processing order, format_str parser, UPSERT semantics — all need explicit coverage |
| `suggest.py` Level A+B | Unit tests | NPMI computation, graph construction, Louvain clustering — verify against known fixture outputs |
| `ingest.py`, `extract_meta.py`, `extract_fields.py`, `hash.py` | Integration tests | Run against fixture corpus; verify corpus.db state after each stage |
| `entity_match.py` | Integration tests | Two-step trigger, linked table traversal, cycle detection, GPS match |
| `sync.py` | Integration tests | KB version bumps, staleness detection, dirty-set computation — exercise all change_type paths |
| `writeback.py` | Integration tests | Run against fixture files in a temp copy; verify ExifTool output via re-read |
| `describe.py`, `video.py` | Manual only | GPU/LLM stages; too expensive for CI; validated by VD.1–VD.4 tests in main app |
| `retag.py` | Manual only | Text LLM stage; same reasoning |
| `aesthetic.py` | Manual only | ONNX model dependency; optional feature |

### Definition of done per stage

A stage is considered test-complete when: happy path passes against fixture corpus, resume-on-restart is verified (run half the corpus, interrupt, re-run, confirm only remaining files processed), and at least one failure mode is covered (corrupted file, missing prerequisite row, invalid rule).

---

## Design Clarifications (2026-06-18)

Resolved during pre-implementation design review.

### Corrupted corpus.db handling

If `corpus.db` cannot be opened on startup, the tool surfaces a health panel alert with the exact SQLite error message and offers three recovery paths:
1. **Retry** — re-attempt open (for transient locks)
2. **Reset corpus** — delete and recreate corpus.db (stages 0–3 re-run; knowledge.db unaffected)
3. **Restore from backup** — open file browser pointing at the KB folder

The same three options apply to `knowledge.db` corruption, but Reset requires typing the KB name to confirm. Stage cards that depend on the failed database show a disabled state with "Database unavailable — see health panel."

### Files changed on disk during run

Files that change on disk between `enrich ingest` and `enrich hash` are detected by comparing `file_size` and `mtime` stored at ingest time against the current stat at hash time. A stat mismatch causes the file's corpus.db row to be updated and the hash to be marked `pending` for re-run. A warning is logged.

### UX: Count badges on touchpoints

Each touchpoint in the pipeline planner shows a badge with the pending item count:
- **Normalization Review**: count of `analyse_tokens` rows with `status='pending'`
- **Suggestion Review**: count of `candidates` rows with `status='pending'`
- **New Terms Review**: count of distinct new terms in `retag_output.new_terms_proposed_json` not yet in vocabulary

Badges update after each stage run without requiring a page refresh (HTMX `hx-trigger="load"` swap on the stage card component). A zero badge is shown in muted colour to signal the touchpoint is clear — not absent.

### UX: Level B evidence panel

Each Level B/C suggestion in the review queue has an expandable evidence panel showing:
- **Co-occurrence partners**: top-N terms this term was observed with, sorted by NPMI weight
- **Cluster siblings**: other terms in the same Louvain cluster
- **File sample**: up to 3 filenames/paths where this term appeared (Level A only)
- **LLM notes** (Level C only): the model's reasoning as plain text

Panel is collapsed by default; triggered by a `▶ Evidence` disclosure button per row.

### UX: Bulk undo in review queues

Each review queue's "Decisions Made" panel shows the last 20 decisions (paginated). Each row has a [Remove] button that immediately undoes the decision — reverts the `candidates` / `analyse_tokens` row back to `pending` status and removes the corresponding knowledge.db entry.

### UX: Two-step write-back threshold

The Write-back stage card shows two numbers: **Dirty files** and **Total files**. A file count above `write_back.confirm_above` (default: 200) shows an additional confirmation step before running write-back: *"This will write metadata to N files on disk. Continue?"*

### Keyboard shortcuts in Normalization Review

| Key | Action |
|---|---|
| `J` / `↓` | Move focus to next row |
| `K` / `↑` | Move focus to previous row |
| `A` | Accept focused row |
| `R` | Reject focused row |
| `C` | Open Capture instrument on focused row |
| `I` | Ignore focused row |
| `Z` | Undo last decision |
| `?` | Toggle shortcut legend overlay |

Shortcuts are active whenever focus is not inside a text input.

### Vocabulary-only export button

The Vocabulary browser header includes a `↓ Export vocabulary.csv` button that downloads only the vocabulary file without triggering a full `enrich export` run. Implementation: `GET /api/vocabulary/export-csv` returns `Content-Disposition: attachment; filename="vocabulary.csv"` with the current `vocabulary` table contents serialised as CSV. No `export/` folder write; the download is streamed directly to the browser.

### Progressive Normalization Review

The Normalization Review queue does not wait for Analyse to finish before showing results. As `analyse.py` inserts rows into `analyse_tokens`, the review queue page polls `GET /api/review/normalise/pending?limit=50&offset=N` at a 2-second interval while the Analyse stage is running. New rows appear at the bottom of the table as they arrive. A status line shows: *"Analysing… 432 patterns found so far."* When Analyse completes, the status line changes to *"Analysis complete — 1,204 patterns total."* and polling stops.

### Corpus % in review queues

Every suggestion row shows file count and corpus percentage:
```
  Asphalt Paver    127 files (6.9%)    [Accept] [Reject] [Ignore] [Correct →]
```

The denominator is `pipeline_checkpoints.files_processed` for stage `suggest`. Percentage is computed client-side from the file count and the stored denominator — no extra SQL per row.

### Scroll position persistence in Normalization Review

`sessionStorage` is used to persist the scroll position between decisions. If a full reload occurs (e.g. after a filter change), the page scrolls back to the last-seen row by matching the row's `data-token-id` attribute against the stored value in `sessionStorage['normalise_review_scroll_id']`.

### UX: Model load failure

If a stage that requires a model starts and the model fails to load, the stage immediately transitions to `failed` state. The stage card shows:
- The `ModelLoadError` message
- Estimated VRAM requirement from model file size
- Specific actionable suggestion based on the error type (VRAM-related / missing file / corrupted file)
- A link to the health checker's model section

No silent fallback to CPU — the failure is explicit and the user chooses the resolution. The stage is re-runnable after the user corrects the issue.

---

## Status

**Design complete as of 2026-06-18. Implementation begins with sprint planning.**

Sessions completed:
- 2026-06-16: Initial conception and architecture
- 2026-06-17: Full design review — all 34+ issues resolved (E1–E6, T1–T6, F1–F4, M1–M8, U1–U8, P1–P4, O1–O9)
- 2026-06-18: Extended — Whisper Stage 3b, quick-describe/quick-transcribe, VRAM n_gpu_layers + ModelLoadError, GitHub structure, import/dependency discipline, Analyse enhancements (path hierarchy, pattern-type detection, entity seeding, cross-source correlation, LLM-assisted capture rule naming)

**VD.1–VD.4** describe pipeline functions are complete and tested in the main app — adapt for Stage 3 when implementing.

### Enhancement placement strategy

- **XS and S enhancements** are woven into the sprint that builds their corresponding feature.
- **M enhancements** (corpus statistics panel, export diff, round-trip validation, analyse stage diff view, corpus.db incremental re-ingest) are planned as dedicated polish sprints (KB.P1, KB.P2, etc.) after the core pipeline (KB.0–KB.9) is working.

### Suggested sprint groupings

- **KB.0** — project scaffold: file structure, config loader, table-based migrations, corpus.db schema, FastAPI shell, Typer CLI shell
- **KB.1** — Ingest + Analyse: file walk, sources table, Stage 0 + Stage 0.5 with Normalization Review UI (including path hierarchy, pattern-type detection, entity seeding, cross-source correlation)
- **KB.2** — Normalize: capture/reject/substitute/correct rules, Stage 1, Normalization Review
- **KB.3** — Hash + Describe: SHA-256 + pHash dedup, Stage 3a (adapt VD.1–VD.4)
- **KB.4** — Transcribe: Stage 3b, pywhispercpp, transcriptions schema, audio routing
- **KB.5** — Suggest: Level A (spaCy), Level B (NPMI streaming), Suggest Review touchpoint
- **KB.6** — Retag + Write-back: Stage 5 text-only LLM, ExifTool write-back, field_map.csv
- **KB.7** — Export + KB management: knowledge.db CRUD, KB browser UI, export bundle
- **KB.8** — Quick commands: enrich quick-describe, enrich quick-transcribe
- **KB.9** — Aesthetic: NIMA + CLIP (adapt from main app AQ.1–AQ.3)
