import sqlite3
from pathlib import Path

from src.db.migrations import apply_migrations

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations" / "knowledge"

_BUILTIN_STOPWORDS = [
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "aren't", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can't",
    "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't",
    "doing", "don't", "down", "during", "each", "few", "for", "from", "further",
    "get", "got", "had", "hadn't", "has", "hasn't", "have", "haven't", "having",
    "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself",
    "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've",
    "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's",
    "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of",
    "off", "on", "once", "only", "or", "other", "ought", "our", "ours",
    "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd",
    "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than",
    "that", "that's", "the", "their", "theirs", "them", "themselves", "then",
    "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "will", "with", "won't",
    "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your",
    "yours", "yourself", "yourselves",
]


def _configure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA cache_size = -32000;
        PRAGMA temp_store = MEMORY;
    """)


def _seed_builtin_stopwords(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO stoplist (term, scope, source) VALUES (?, 'global', 'builtin')",
        [(term,) for term in _BUILTIN_STOPWORDS],
    )
    conn.commit()


def _seed_builtin_classify_rules(conn: sqlite3.Connection) -> None:
    from src.stages.classify_rules import BUILTIN_RULES
    for rule in BUILTIN_RULES:
        conn.execute(
            """
            INSERT OR IGNORE INTO classify_rules
                (label, result_tag, category, source, field_name, match_type,
                 match_config, minimum_precision, is_builtin, enabled)
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, 1, 1
            WHERE NOT EXISTS (
                SELECT 1 FROM classify_rules WHERE label = ? AND is_builtin = 1
            )
            """,
            (
                rule["label"], rule["result_tag"], rule["category"], rule["source"],
                rule.get("field_name"), rule["match_type"],
                rule["match_config"], rule.get("minimum_precision"),
                rule["label"],
            ),
        )
    conn.commit()


def open_kb(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _configure(conn)
    apply_migrations(conn, _MIGRATIONS_DIR)
    _seed_builtin_stopwords(conn)
    _seed_builtin_classify_rules(conn)
    return conn


# ---------------------------------------------------------------------------
# Normalization rule writers
# ---------------------------------------------------------------------------

def add_capture_rule(
    conn: sqlite3.Connection,
    pattern: str,
    label: str,
    extract_as: str,
    format_str: str = "",
    value_type: str = "",
    keep_token: bool = False,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO capture_rules (pattern, label, extract_as, format_str, value_type, keep_token)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (pattern, label, extract_as, format_str or None, value_type or None, int(keep_token)),
    )
    conn.commit()
    return cur.lastrowid


def add_to_stoplist(
    conn: sqlite3.Connection,
    term: str,
    scope: str = "global",
    source: str = "domain",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO stoplist (term, scope, source) VALUES (?, ?, ?)",
        (term, scope, source),
    )
    conn.commit()


def add_correction(
    conn: sqlite3.Connection,
    raw_term: str,
    canonical_term: str,
    correction_kind: str = "typo",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO corrections (raw_term, canonical_term, type, correction_kind)
        VALUES (?, ?, 'exact', ?)
        ON CONFLICT(raw_term, type) DO UPDATE SET
            canonical_term  = excluded.canonical_term,
            correction_kind = excluded.correction_kind
        """,
        (raw_term, canonical_term, correction_kind),
    )
    conn.commit()
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute(
        "SELECT id FROM corrections WHERE raw_term=? AND type='exact'", (raw_term,)
    ).fetchone()
    return row["id"]


def add_reject_token(
    conn: sqlite3.Connection,
    pattern: str,
    is_regex: bool = False,
    label: str = "",
) -> int:
    cur = conn.execute(
        "INSERT INTO reject_tokens (pattern, is_regex, label) VALUES (?, ?, ?)",
        (pattern, int(is_regex), label or None),
    )
    conn.commit()
    return cur.lastrowid


def delete_decision(conn: sqlite3.Connection, table: str, row_id: int) -> None:
    _ALLOWED = {"capture_rules", "stoplist", "corrections", "reject_tokens"}
    if table not in _ALLOWED:
        raise ValueError(f"Unknown decision table: {table!r}")
    if table == "stoplist":
        conn.execute("DELETE FROM stoplist WHERE rowid=?", (row_id,))
    else:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))  # noqa: S608
    conn.commit()


def get_decisions(conn: sqlite3.Connection) -> list[dict]:
    decisions: list[dict] = []

    for row in conn.execute(
        "SELECT rowid, term, scope FROM stoplist WHERE source='domain' ORDER BY rowid"
    ).fetchall():
        decisions.append({
            "id": f"stoplist:{row['rowid']}",
            "token": row["term"],
            "action": "ignore",
            "detail": f"scope={row['scope']}",
        })

    for row in conn.execute(
        "SELECT id, raw_term, canonical_term, correction_kind FROM corrections ORDER BY id"
    ).fetchall():
        decisions.append({
            "id": f"corrections:{row['id']}",
            "token": row["raw_term"],
            "action": "correct",
            "detail": row["canonical_term"],
        })

    for row in conn.execute(
        "SELECT id, pattern, extract_as, label FROM capture_rules ORDER BY id"
    ).fetchall():
        decisions.append({
            "id": f"capture_rules:{row['id']}",
            "token": row["label"] or row["pattern"],
            "action": "capture",
            "detail": row["extract_as"],
        })

    for row in conn.execute(
        "SELECT id, pattern, label FROM reject_tokens ORDER BY id"
    ).fetchall():
        decisions.append({
            "id": f"reject_tokens:{row['id']}",
            "token": row["label"] or row["pattern"],
            "action": "reject",
            "detail": "",
        })

    return decisions


def bump_kb_version(conn: sqlite3.Connection, change_type: str) -> None:
    conn.execute("INSERT INTO kb_version (change_type) VALUES (?)", (change_type,))
    conn.commit()


# ---------------------------------------------------------------------------
# Normalize rule readers
# ---------------------------------------------------------------------------

def get_capture_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT pattern, extract_as, format_str, keep_token, value_type, date_precision"
        " FROM capture_rules ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_reject_tokens(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT pattern, is_regex FROM reject_tokens ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_substitute_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT pattern, replacement, applies_to FROM substitute_rules ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_corrections_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT raw_term, canonical_term FROM corrections WHERE type = 'exact'"
    ).fetchall()
    return {r["raw_term"]: r["canonical_term"] for r in rows}


def get_stoplist_terms(conn: sqlite3.Connection, scope: str = "global") -> set[str]:
    rows = conn.execute(
        "SELECT term FROM stoplist WHERE scope = ?", (scope,)
    ).fetchall()
    return {r["term"] for r in rows}


# ---------------------------------------------------------------------------
# Entity table registry
# ---------------------------------------------------------------------------

def register_entity_table(
    conn: sqlite3.Connection,
    table_name: str,
    display_name: str,
    trigger_word: str,
    trigger_aliases_json: str,
    key_column: str,
    match_type: str,
    source_csv: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO entity_table_registry
            (table_name, display_name, trigger_word, trigger_aliases,
             key_column, match_type, source_csv, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(table_name) DO UPDATE SET
            display_name    = excluded.display_name,
            trigger_word    = excluded.trigger_word,
            trigger_aliases = excluded.trigger_aliases,
            key_column      = excluded.key_column,
            match_type      = excluded.match_type,
            source_csv      = excluded.source_csv,
            updated_at      = datetime('now')
        """,
        (table_name, display_name, trigger_word, trigger_aliases_json,
         key_column, match_type, source_csv),
    )
    conn.commit()


def get_entity_tables(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entity_table_registry ORDER BY table_name"
    ).fetchall()


def create_entity_table(
    conn: sqlite3.Connection,
    table_name: str,
    columns: list[str],
    key_column: str,
) -> None:
    import re as _re
    safe_name = _re.sub(r"[^A-Za-z0-9_]", "_", table_name)
    col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
    conn.executescript(
        f'CREATE TABLE IF NOT EXISTS "entity_{safe_name}" '
        f'({col_defs}, UNIQUE ("{key_column}"));'
    )
    conn.commit()


def upsert_entity_row(conn: sqlite3.Connection, table_name: str, row: dict) -> None:
    import re as _re
    safe_name = _re.sub(r"[^A-Za-z0-9_]", "_", table_name)
    cols = list(row.keys())
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(f'"{c}"' for c in cols)
    updates = ", ".join(f'"{c}" = excluded."{c}"' for c in cols)
    conn.execute(
        f'INSERT INTO "entity_{safe_name}" ({col_list}) VALUES ({placeholders})'
        f' ON CONFLICT DO UPDATE SET {updates}',
        [row[c] for c in cols],
    )


def get_entity_table_rows(conn: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    import re as _re
    safe_name = _re.sub(r"[^A-Za-z0-9_]", "_", table_name)
    return conn.execute(f'SELECT * FROM "entity_{safe_name}"').fetchall()


def get_entity_table_keys(
    conn: sqlite3.Connection, table_name: str, key_col: str
) -> list[str]:
    import re as _re
    safe_name = _re.sub(r"[^A-Za-z0-9_]", "_", table_name)
    try:
        rows = conn.execute(
            f'SELECT "{key_col}" FROM "entity_{safe_name}"'
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Classify rules
# ---------------------------------------------------------------------------

def get_classify_rules(
    conn: sqlite3.Connection, enabled_only: bool = True
) -> list[sqlite3.Row]:
    where = "WHERE enabled = 1" if enabled_only else ""
    return conn.execute(
        f"SELECT * FROM classify_rules {where} ORDER BY id"
    ).fetchall()


# ---------------------------------------------------------------------------
# People register
# ---------------------------------------------------------------------------

def upsert_person(
    conn: sqlite3.Connection,
    preferred_name: str,
    title: str = "",
    first_name: str = "",
    middle_name: str = "",
    last_name: str = "",
    family: bool = False,
    notes: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO people (preferred_name, title, first_name, middle_name, last_name, family, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (preferred_name, title or None, first_name or None,
         middle_name or None, last_name or None, int(family), notes or None),
    )
    conn.commit()
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute(
        "SELECT id FROM people WHERE preferred_name = ?", (preferred_name,)
    ).fetchone()
    return row["id"]


def add_person_name(
    conn: sqlite3.Connection,
    person_id: int,
    name: str,
    is_metadata_form: bool = False,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO people_names (person_id, name, is_metadata_form)
        VALUES (?, ?, ?)
        """,
        (person_id, name, int(is_metadata_form)),
    )
    conn.commit()


def add_life_event(
    conn: sqlite3.Connection,
    person_id: int,
    event_type: str,
    event_date: str = "",
    partner_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO life_events (person_id, event_type, event_date, partner_id)
        VALUES (?, ?, ?, ?)
        """,
        (person_id, event_type, event_date or None, partner_id),
    )
    conn.commit()


def get_people_names(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT person_id, name, is_metadata_form FROM people_names ORDER BY person_id, id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_life_events(
    conn: sqlite3.Connection, person_ids: list[int]
) -> list[sqlite3.Row]:
    if not person_ids:
        return []
    placeholders = ",".join("?" * len(person_ids))
    return conn.execute(
        f"SELECT le.*, p.preferred_name FROM life_events le"
        f" JOIN people p ON p.id = le.person_id"
        f" WHERE le.person_id IN ({placeholders}) ORDER BY le.id",
        person_ids,
    ).fetchall()


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def add_vocabulary_term(
    conn: sqlite3.Connection,
    term: str,
    synonyms_json: str = "[]",
    source: str = "accepted",
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO vocabulary (term, synonyms_json, source) VALUES (?, ?, ?)",
        (term, synonyms_json, source),
    )
    row = conn.execute("SELECT id FROM vocabulary WHERE term=?", (term,)).fetchone()
    return row["id"] if row else 0


def get_vocabulary_terms(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM vocabulary ORDER BY term").fetchall()


def delete_vocabulary_term(conn: sqlite3.Connection, term: str) -> None:
    conn.execute("DELETE FROM vocabulary WHERE term=?", (term,))


# ---------------------------------------------------------------------------
# Export readers
# ---------------------------------------------------------------------------

def get_export_vocabulary(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT term, synonyms_json, write_synonyms, source FROM vocabulary ORDER BY term"
    ).fetchall()


def get_export_stopwords(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT term FROM stoplist WHERE source != 'builtin' ORDER BY term"
    ).fetchall()
    return [r["term"] for r in rows]


def get_export_corrections_exact(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT raw_term, canonical_term FROM corrections WHERE type='exact' ORDER BY raw_term"
    ).fetchall()


def get_export_corrections_pattern(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT raw_term, canonical_term, correction_kind FROM corrections"
        " WHERE type='pattern' ORDER BY id"
    ).fetchall()


def get_export_capture_rules(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pattern, label, extract_as, value_type, format_str, keep_token"
        " FROM capture_rules ORDER BY id"
    ).fetchall()


def get_export_substitute_rules(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pattern, replacement, label, applies_to FROM substitute_rules ORDER BY id"
    ).fetchall()


def get_export_reject_tokens(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pattern, is_regex, label, scope FROM reject_tokens ORDER BY id"
    ).fetchall()


def get_export_entity_registry(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT table_name, display_name, trigger_word, trigger_aliases,"
        " key_column, match_type, description, source_csv"
        " FROM entity_table_registry ORDER BY table_name"
    ).fetchall()


def get_export_entity_links(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT parent_table, parent_column, linked_table, linked_key_column,"
        " label, include_in_text_pool FROM entity_table_links ORDER BY id"
    ).fetchall()


def get_export_entity_rows(
    conn: sqlite3.Connection, table_name: str
) -> tuple[list[str], list[sqlite3.Row]]:
    import re as _re
    safe = _re.sub(r"[^A-Za-z0-9_]", "_", table_name)
    col_rows = conn.execute(f'PRAGMA table_info("entity_{safe}")').fetchall()
    if not col_rows:
        return [], []
    columns = [r["name"] for r in col_rows]
    rows = conn.execute(f'SELECT * FROM "entity_{safe}" ORDER BY rowid').fetchall()
    return columns, rows


def get_entity_links_by_parent(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    rows = conn.execute(
        "SELECT parent_table, parent_column, linked_table, linked_key_column,"
        " label, include_in_text_pool FROM entity_table_links ORDER BY id"
    ).fetchall()
    result: dict[str, list[dict]] = {}
    for row in rows:
        d = dict(row)
        result.setdefault(d["parent_table"], []).append(d)
    return result


def seed_entity_links(conn: sqlite3.Connection, links: list[dict]) -> int:
    inserted = 0
    for lnk in links:
        pool = int(str(lnk.get("include_in_text_pool", "1")).strip().lower() in ("1", "true", "yes"))
        cur = conn.execute(
            "INSERT INTO entity_table_links"
            " (parent_table, parent_column, linked_table, linked_key_column, label, include_in_text_pool)"
            " SELECT ?, ?, ?, ?, ?, ?"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM entity_table_links"
            "   WHERE parent_table=? AND parent_column=? AND linked_table=? AND linked_key_column=?"
            " )",
            (
                lnk["parent_table"], lnk["parent_column"],
                lnk["linked_table"], lnk["linked_key_column"],
                lnk.get("label", ""), pool,
                lnk["parent_table"], lnk["parent_column"],
                lnk["linked_table"], lnk["linked_key_column"],
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def seed_entity_bundle(
    conn: sqlite3.Connection, entities_dir: Path
) -> tuple[int, int, int]:
    import csv as _csv
    registry_path = entities_dir / "_registry.csv"
    if not entities_dir.exists() or not registry_path.exists():
        return (0, 0, 0)

    tables_seeded = 0
    rows_seeded = 0

    with open(registry_path, newline="", encoding="utf-8-sig") as fh:
        registry_rows = list(_csv.DictReader(fh))

    for reg in registry_rows:
        table_name = reg["table_name"].strip()
        if not table_name:
            continue
        table_csv = entities_dir / f"{table_name}.csv"
        if not table_csv.exists():
            continue

        with open(table_csv, newline="", encoding="utf-8-sig") as fh:
            reader = _csv.DictReader(fh)
            col_names = list(reader.fieldnames or [])
            data_rows = list(reader)

        key_col = reg["key_column"].strip()
        create_entity_table(conn, table_name, col_names, key_col)
        register_entity_table(
            conn,
            table_name=table_name,
            display_name=reg.get("display_name", "").strip(),
            trigger_word=reg.get("trigger_word", "").strip(),
            trigger_aliases_json=reg.get("trigger_aliases", "[]").strip() or "[]",
            key_column=key_col,
            match_type=reg.get("match_type", "text").strip(),
            source_csv=reg.get("source_csv", "").strip(),
        )
        for row in data_rows:
            upsert_entity_row(conn, table_name, {k: v for k, v in row.items() if k})
        conn.commit()
        tables_seeded += 1
        rows_seeded += len(data_rows)

    links_path = entities_dir / "_links.csv"
    links_seeded = 0
    if links_path.exists():
        with open(links_path, newline="", encoding="utf-8-sig") as fh:
            link_rows = list(_csv.DictReader(fh))
        links_seeded = seed_entity_links(conn, link_rows)

    return (tables_seeded, rows_seeded, links_seeded)


# ---------------------------------------------------------------------------
# Seed writers (used by --import-kb and --template)
# ---------------------------------------------------------------------------

def seed_stopwords(conn: sqlite3.Connection, terms: list[str], scope: str = "global") -> int:
    inserted = 0
    for term in terms:
        cur = conn.execute(
            "INSERT OR IGNORE INTO stoplist (term, scope, source) VALUES (?, ?, 'seeded')",
            (term.strip(), scope),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def seed_corrections_exact(conn: sqlite3.Connection, corrections: dict[str, str]) -> int:
    inserted = 0
    for raw, canonical in corrections.items():
        cur = conn.execute(
            "INSERT OR IGNORE INTO corrections (raw_term, canonical_term, type)"
            " SELECT ?, ?, 'exact'"
            " WHERE NOT EXISTS (SELECT 1 FROM corrections WHERE raw_term=? AND type='exact')",
            (raw, canonical, raw),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def seed_capture_rules(conn: sqlite3.Connection, rules: list[dict]) -> int:
    inserted = 0
    for rule in rules:
        cur = conn.execute(
            "INSERT OR IGNORE INTO capture_rules"
            " (pattern, label, extract_as, value_type, format_str, keep_token)"
            " SELECT ?, ?, ?, ?, ?, ?"
            " WHERE NOT EXISTS (SELECT 1 FROM capture_rules WHERE pattern=?)",
            (
                rule.get("pattern"), rule.get("label"), rule.get("extract_as"),
                rule.get("value_type"), rule.get("format_str"),
                int(bool(rule.get("keep_token", False))),
                rule.get("pattern"),
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def seed_substitute_rules(conn: sqlite3.Connection, rules: list[dict]) -> int:
    inserted = 0
    for rule in rules:
        cur = conn.execute(
            "INSERT OR IGNORE INTO substitute_rules (pattern, replacement, label, applies_to)"
            " SELECT ?, ?, ?, ?"
            " WHERE NOT EXISTS (SELECT 1 FROM substitute_rules WHERE pattern=?)",
            (
                rule.get("pattern"), rule.get("replacement"),
                rule.get("label"), rule.get("applies_to", "both"),
                rule.get("pattern"),
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def seed_reject_tokens(conn: sqlite3.Connection, tokens: list[dict]) -> int:
    inserted = 0
    for tok in tokens:
        cur = conn.execute(
            "INSERT OR IGNORE INTO reject_tokens (pattern, is_regex, label, scope)"
            " SELECT ?, ?, ?, ?"
            " WHERE NOT EXISTS (SELECT 1 FROM reject_tokens WHERE pattern=?)",
            (
                tok.get("pattern"), int(bool(tok.get("is_regex", False))),
                tok.get("label"), tok.get("scope", "both"),
                tok.get("pattern"),
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

_CATEGORY_LABEL: dict[str, str] = {
    "calendar":   "Calendar",
    "technical":  "Technical",
    "temporal":   "Temporal",
    "tonality":   "Tonality",
    "life_event": "LifeEvent",
    "geographic": "Geographic",
}


def build_taxonomy_data(conn: sqlite3.Connection) -> dict:
    """Build a taxonomy dict from knowledge.db content.

    Returns a structure suitable for writing to reference/taxonomy.yaml:
        Tags → {Calendar: [...], Technical: [...], Temporal: [...], ...}
        Keywords → [...]
        People → [...]
        <EntityDisplayName> → [...] for each registered entity table
    """
    import re as _re

    taxonomy: dict = {}

    # Tags from classify rules, grouped by category
    tags_by_category: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT result_tag, category FROM classify_rules WHERE enabled = 1 ORDER BY id"
    ).fetchall():
        label = _CATEGORY_LABEL.get(row["category"], row["category"].capitalize())
        tags_by_category.setdefault(label, [])
        tag = row["result_tag"]
        if tag not in tags_by_category[label]:
            tags_by_category[label].append(tag)

    if tags_by_category:
        taxonomy["Tags"] = {k: sorted(v) for k, v in sorted(tags_by_category.items())}

    # Keywords from accepted/user vocabulary
    vocab_rows = conn.execute(
        "SELECT term FROM vocabulary WHERE source IN ('accepted', 'user') ORDER BY term"
    ).fetchall()
    if vocab_rows:
        taxonomy["Keywords"] = [r["term"] for r in vocab_rows]

    # People from the people register
    people_rows = conn.execute(
        "SELECT preferred_name FROM people ORDER BY preferred_name"
    ).fetchall()
    if people_rows:
        taxonomy["People"] = [r["preferred_name"] for r in people_rows]

    # Entity tables — one section per registered table
    for reg in conn.execute(
        "SELECT table_name, display_name, key_column FROM entity_table_registry ORDER BY table_name"
    ).fetchall():
        safe = _re.sub(r"[^A-Za-z0-9_]", "_", reg["table_name"])
        col_info = conn.execute(f'PRAGMA table_info("entity_{safe}")').fetchall()
        if not col_info:
            continue
        col_names = [r["name"] for r in col_info]
        key_col = reg["key_column"] if reg["key_column"] in col_names else col_names[0]
        rows = conn.execute(
            f'SELECT DISTINCT "{key_col}" FROM "entity_{safe}" ORDER BY "{key_col}"'
        ).fetchall()
        if rows:
            taxonomy[reg["display_name"]] = [str(r[0]) for r in rows if r[0] is not None]

    return taxonomy


def merge_taxonomy(existing: dict, generated: dict) -> dict:
    """Merge generated taxonomy into existing, preserving user edits.

    For list values: union (existing items kept, new items appended if not already present).
    For dict values: recurse per sub-key.
    Keys only in existing are left untouched.
    """
    result = dict(existing)
    for key, new_val in generated.items():
        if key not in result:
            result[key] = new_val
        elif isinstance(new_val, dict) and isinstance(result[key], dict):
            merged_sub: dict = dict(result[key])
            for sub_key, sub_list in new_val.items():
                if sub_key not in merged_sub:
                    merged_sub[sub_key] = sub_list
                elif isinstance(sub_list, list) and isinstance(merged_sub[sub_key], list):
                    existing_set = set(merged_sub[sub_key])
                    merged_sub[sub_key] = merged_sub[sub_key] + [
                        v for v in sub_list if v not in existing_set
                    ]
            result[key] = merged_sub
        elif isinstance(new_val, list) and isinstance(result[key], list):
            existing_set = set(result[key])
            result[key] = result[key] + [v for v in new_val if v not in existing_set]
    return result


# ---------------------------------------------------------------------------
# Face centroid helpers
# ---------------------------------------------------------------------------

def get_people_with_centroids(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, preferred_name, face_centroid, face_samples
        FROM people
        WHERE face_centroid IS NOT NULL
        ORDER BY id
        """
    ).fetchall()


def update_face_centroid(
    conn: sqlite3.Connection,
    person_id: int,
    new_centroid_blob: bytes,
    new_sample_count: int,
) -> None:
    conn.execute(
        "UPDATE people SET face_centroid = ?, face_samples = ? WHERE id = ?",
        (new_centroid_blob, new_sample_count, person_id),
    )


# ---------------------------------------------------------------------------
# People export helpers
# ---------------------------------------------------------------------------

def get_people_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, preferred_name, title, first_name, middle_name, last_name, notes
        FROM people
        ORDER BY id
        """
    ).fetchall()


def get_people_names_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT pn.person_id, p.preferred_name, pn.name
        FROM people_names pn
        JOIN people p ON p.id = pn.person_id
        ORDER BY pn.person_id, pn.name
        """
    ).fetchall()


def get_life_events_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT le.person_id, p.preferred_name, le.event_type, le.event_date,
               le.partner_id, le.notes
        FROM life_events le
        JOIN people p ON p.id = le.person_id
        ORDER BY le.person_id, le.event_date
        """
    ).fetchall()


def get_people_face_centroids_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id AS person_id, preferred_name, face_centroid, face_samples
        FROM people
        WHERE face_centroid IS NOT NULL
        ORDER BY id
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Voice centroid helpers
# ---------------------------------------------------------------------------

def get_people_with_voice_centroids(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, preferred_name, voice_centroid, voice_samples
        FROM people
        WHERE voice_centroid IS NOT NULL
        ORDER BY id
        """
    ).fetchall()


def update_voice_centroid(
    conn: sqlite3.Connection,
    person_id: int,
    new_centroid_blob: bytes,
    new_sample_count: int,
) -> None:
    conn.execute(
        "UPDATE people SET voice_centroid = ?, voice_samples = ? WHERE id = ?",
        (new_centroid_blob, new_sample_count, person_id),
    )


def get_all_people(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, preferred_name FROM people ORDER BY preferred_name"
    ).fetchall()


def merge_voice_centroid(
    conn: sqlite3.Connection,
    person_id: int,
    cluster_blob: bytes,
    cluster_count: int,
) -> None:
    """Weighted-average a cluster centroid into a person's voice_centroid, then L2-normalise."""
    import numpy as np

    row = conn.execute(
        "SELECT voice_centroid, voice_samples FROM people WHERE id = ?", (person_id,)
    ).fetchone()
    if row is None:
        return

    cluster_vec = np.frombuffer(cluster_blob, dtype=np.float32).copy()
    existing_blob = row["voice_centroid"]
    existing_count = row["voice_samples"] or 0

    if existing_blob is None or existing_count == 0:
        merged = cluster_vec
        new_count = cluster_count
    else:
        existing_vec = np.frombuffer(bytes(existing_blob), dtype=np.float32).copy()
        total = existing_count + cluster_count
        merged = (existing_vec * existing_count + cluster_vec * cluster_count) / total
        new_count = total

    norm = float(np.linalg.norm(merged))
    if norm > 0:
        merged = merged / norm

    conn.execute(
        "UPDATE people SET voice_centroid = ?, voice_samples = ? WHERE id = ?",
        (merged.astype(np.float32).tobytes(), new_count, person_id),
    )


def get_people_voice_centroids_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id AS person_id, preferred_name, voice_centroid, voice_samples
        FROM people
        WHERE voice_centroid IS NOT NULL
        ORDER BY id
        """
    ).fetchall()

