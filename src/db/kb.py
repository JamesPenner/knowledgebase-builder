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
    seed_stage_prompts(conn)
    return conn


# ---------------------------------------------------------------------------
# Normalization rule writers
# ---------------------------------------------------------------------------

def add_pattern_rule(
    conn: sqlite3.Connection,
    pattern: str,
    action: str,
    *,
    is_regex: bool = True,
    label: str = "",
    replace_with: str = "",
    replace_type: str = "",
    extract_as: str = "",
    format_str: str = "",
    value_type: str = "",
    keep_token: bool = False,
    date_precision: str | None = None,
    scope: str = "both",
) -> int:
    cur = conn.execute(
        "INSERT INTO pattern_rules"
        " (pattern, is_regex, action, label, replace_with, replace_type,"
        "  extract_as, format_str, value_type, keep_token, date_precision, scope)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pattern, int(is_regex), action, label or None,
            replace_with or None, replace_type or None,
            extract_as or None, format_str or None,
            value_type or None, int(keep_token),
            date_precision or None, scope,
        ),
    )
    conn.commit()
    if action == "replace" and replace_type == "synonym" and replace_with:
        _add_synonym_to_vocabulary(conn, replace_with, pattern)
    return cur.lastrowid


def update_pattern_rule(
    conn: sqlite3.Connection,
    rule_id: int,
    pattern: str,
    action: str,
    *,
    is_regex: bool = True,
    label: str = "",
    replace_with: str = "",
    replace_type: str = "",
    extract_as: str = "",
    format_str: str = "",
    value_type: str = "",
    keep_token: bool = False,
    date_precision: str | None = None,
    scope: str = "both",
) -> None:
    conn.execute(
        "UPDATE pattern_rules"
        " SET pattern=?, is_regex=?, action=?, label=?, replace_with=?, replace_type=?,"
        "     extract_as=?, format_str=?, value_type=?, keep_token=?, date_precision=?, scope=?"
        " WHERE id=?",
        (
            pattern, int(is_regex), action, label or None,
            replace_with or None, replace_type or None,
            extract_as or None, format_str or None,
            value_type or None, int(keep_token),
            date_precision or None, scope, rule_id,
        ),
    )
    conn.commit()
    if action == "replace" and replace_type == "synonym" and replace_with:
        _add_synonym_to_vocabulary(conn, replace_with, pattern)


def delete_pattern_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    delete_decision(conn, "pattern_rules", rule_id)


def _add_synonym_to_vocabulary(conn: sqlite3.Connection, canonical: str, synonym: str) -> None:
    import json as _json
    row = conn.execute(
        "SELECT synonyms_json FROM vocabulary WHERE term=?", (canonical,)
    ).fetchone()
    if not row:
        return
    try:
        synonyms = _json.loads(row["synonyms_json"] or "[]")
    except (ValueError, TypeError):
        synonyms = []
    if synonym not in synonyms:
        synonyms.append(synonym)
        conn.execute(
            "UPDATE vocabulary SET synonyms_json=? WHERE term=?",
            (_json.dumps(synonyms, ensure_ascii=False), canonical),
        )
        conn.commit()


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


def remove_from_stoplist(conn: sqlite3.Connection, term: str) -> None:
    conn.execute("DELETE FROM stoplist WHERE term=? AND source='domain'", (term,))
    conn.commit()


def add_token_rejection(conn: sqlite3.Connection, token: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO token_rejections (token) VALUES (?)", (token,)
    )
    conn.commit()


def get_token_rejections(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, token FROM token_rejections ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_decision(conn: sqlite3.Connection, table: str, row_id: int) -> None:
    _ALLOWED = {"pattern_rules", "stoplist", "token_rejections"}
    if table not in _ALLOWED:
        raise ValueError(f"Unknown decision table: {table!r}")
    if table == "stoplist":
        conn.execute("DELETE FROM stoplist WHERE rowid=?", (row_id,))
    else:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))  # noqa: S608
    conn.commit()


def get_decision_token(conn: sqlite3.Connection, table: str, row_id: int) -> str | None:
    """Return the token text for an existing KB decision row, or None if not found."""
    _COLUMN = {
        "stoplist": ("stoplist", "term", "WHERE rowid=?"),
        "pattern_rules": ("pattern_rules", "pattern", "WHERE id=?"),
        "token_rejections": ("token_rejections", "token", "WHERE id=?"),
    }
    if table not in _COLUMN:
        raise ValueError(f"Unknown decision table: {table!r}")
    tbl, col, clause = _COLUMN[table]
    row = conn.execute(f"SELECT {col} FROM {tbl} {clause}", (row_id,)).fetchone()  # noqa: S608
    return row[col] if row else None


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
        "SELECT id, pattern, action, label, replace_with, extract_as FROM pattern_rules ORDER BY id"
    ).fetchall():
        action = row["action"]
        if action == "replace":
            detail = row["replace_with"] or ""
        elif action == "capture":
            detail = row["extract_as"] or ""
        else:
            detail = ""
        decisions.append({
            "id": f"pattern_rules:{row['id']}",
            "token": row["label"] or row["pattern"],
            "action": action,
            "detail": detail,
        })

    for row in conn.execute(
        "SELECT id, token FROM token_rejections ORDER BY id"
    ).fetchall():
        decisions.append({
            "id": f"token_rejections:{row['id']}",
            "token": row["token"],
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

def get_pattern_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT pattern, is_regex, action, replace_with, replace_type,"
        "       extract_as, format_str, value_type, keep_token, date_precision"
        " FROM pattern_rules ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def list_pattern_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, pattern, is_regex, action, label, replace_with, replace_type,"
        "       extract_as, format_str, value_type, keep_token, date_precision, scope"
        " FROM pattern_rules ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_substitute_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT pattern, replacement, applies_to FROM substitute_rules ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


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


def get_gps_entity_tables(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entity_table_registry WHERE match_type = 'gps' ORDER BY table_name"
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
# Entity location registry helpers (KB.Q2)
# ---------------------------------------------------------------------------

def _loc_table_safe(conn: sqlite3.Connection, table: str) -> str:
    """Validate full entity table name (entity_<name>) against registry. Returns safe SQL name.

    Raises ValueError for unknown or unregistered tables.
    """
    import re as _re
    if not table.startswith("entity_"):
        raise ValueError(f"Unknown table: {table!r}")
    registry_name = table[len("entity_"):]
    safe = _re.sub(r"[^A-Za-z0-9_]", "_", registry_name)
    row = conn.execute(
        "SELECT 1 FROM entity_table_registry WHERE table_name=? AND match_type IN ('gps','text')",
        (registry_name,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown table: {table!r}")
    return safe


def get_entity_location_tables(conn: sqlite3.Connection) -> list[dict]:
    """Return [{name, match_type}] for registered entity tables with match_type in ('gps','text')
    that contain a 'location' column."""
    import re as _re
    rows = conn.execute(
        "SELECT table_name, match_type FROM entity_table_registry"
        " WHERE match_type IN ('gps','text') ORDER BY table_name"
    ).fetchall()
    result = []
    for row in rows:
        safe = _re.sub(r"[^A-Za-z0-9_]", "_", row["table_name"])
        full = f"entity_{safe}"
        try:
            cols = [c["name"] for c in conn.execute(f'PRAGMA table_info("{full}")').fetchall()]
        except sqlite3.OperationalError:
            continue
        if "location" in cols:
            result.append({"name": full, "match_type": row["match_type"]})
    return result


def get_entity_table_entries(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    """Return all rows (rowid exposed as id) from a registered location entity table.

    Raises ValueError for unknown or unregistered tables.
    table must be the full entity table name, e.g. 'entity_gps_cluster_locations'.
    """
    safe = _loc_table_safe(conn, table)
    return conn.execute(
        f'SELECT rowid AS id, * FROM "entity_{safe}" ORDER BY rowid'
    ).fetchall()


def update_entity_table_entry(
    conn: sqlite3.Connection, table: str, entry_id: int, fields: dict
) -> sqlite3.Row:
    """Update specified fields on a single entry and return the updated row.

    Raises ValueError for unknown table or missing id.
    """
    safe = _loc_table_safe(conn, table)
    row = conn.execute(
        f'SELECT rowid FROM "entity_{safe}" WHERE rowid=?', (entry_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Entry {entry_id} not found in {table!r}")
    set_clause = ", ".join(f'"{k}" = ?' for k in fields)
    values = list(fields.values()) + [entry_id]
    conn.execute(f'UPDATE "entity_{safe}" SET {set_clause} WHERE rowid=?', values)
    conn.commit()
    return conn.execute(
        f'SELECT rowid AS id, * FROM "entity_{safe}" WHERE rowid=?', (entry_id,)
    ).fetchone()


def delete_entity_table_entry(
    conn: sqlite3.Connection, table: str, entry_id: int
) -> None:
    """Delete a single entry by rowid.

    Raises ValueError for unknown table or missing id.
    """
    safe = _loc_table_safe(conn, table)
    row = conn.execute(
        f'SELECT rowid FROM "entity_{safe}" WHERE rowid=?', (entry_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Entry {entry_id} not found in {table!r}")
    conn.execute(f'DELETE FROM "entity_{safe}" WHERE rowid=?', (entry_id,))
    conn.commit()


def merge_entity_table_entries(
    conn: sqlite3.Connection, table: str, keep_id: int, drop_id: int
) -> None:
    """Back-fill null columns in keep row from drop row, then delete drop row.

    Raises ValueError for unknown table, missing ids, or keep_id == drop_id.
    """
    if keep_id == drop_id:
        raise ValueError("keep_id and drop_id must be different")
    safe = _loc_table_safe(conn, table)
    keep_row = conn.execute(
        f'SELECT rowid AS id, * FROM "entity_{safe}" WHERE rowid=?', (keep_id,)
    ).fetchone()
    if keep_row is None:
        raise ValueError(f"Entry {keep_id} not found in {table!r}")
    drop_row = conn.execute(
        f'SELECT rowid AS id, * FROM "entity_{safe}" WHERE rowid=?', (drop_id,)
    ).fetchone()
    if drop_row is None:
        raise ValueError(f"Entry {drop_id} not found in {table!r}")
    updates = {}
    for col in drop_row.keys():
        if col == "id":
            continue
        keep_val = keep_row[col]
        drop_val = drop_row[col]
        if (keep_val is None or keep_val == "") and drop_val is not None and drop_val != "":
            updates[col] = drop_val
    if updates:
        set_clause = ", ".join(f'"{k}" = ?' for k in updates)
        conn.execute(
            f'UPDATE "entity_{safe}" SET {set_clause} WHERE rowid=?',
            list(updates.values()) + [keep_id],
        )
    conn.execute(f'DELETE FROM "entity_{safe}" WHERE rowid=?', (drop_id,))
    conn.commit()


def find_location_near_duplicates(
    entries: list, threshold: float = 0.85
) -> list[dict]:
    """Return near-duplicate pairs from a list of entry dicts.

    Each entry must have 'id' and 'location' keys.
    Returns [{"a_id": int, "b_id": int, "score": float}] for pairs scoring >= threshold.
    Normalisation: lowercase, strip punctuation, collapse whitespace.
    """
    import string
    from difflib import SequenceMatcher

    def _norm(s: str) -> str:
        s = s.lower().translate(str.maketrans("", "", string.punctuation))
        return " ".join(s.split())

    valid = [(e["id"], _norm(str(e.get("location") or ""))) for e in entries if e.get("location")]
    if len(valid) < 2:
        return []
    results = []
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            id_a, n_a = valid[i]
            id_b, n_b = valid[j]
            if n_a == n_b:
                continue
            score = SequenceMatcher(None, n_a, n_b).ratio()
            if score >= threshold:
                results.append({"a_id": id_a, "b_id": id_b, "score": round(score, 4)})
    return results


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


def get_pattern_rule_type(conn: sqlite3.Connection, field_name: str) -> str:
    row = conn.execute(
        "SELECT value_type FROM pattern_rules WHERE extract_as=? AND action='capture' LIMIT 1",
        (field_name,),
    ).fetchone()
    return row["value_type"] if row else "text"


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


def get_export_pattern_rules(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pattern, is_regex, action, label, replace_with, replace_type,"
        "       extract_as, format_str, value_type, keep_token, date_precision, scope"
        " FROM pattern_rules ORDER BY id"
    ).fetchall()


def get_export_substitute_rules(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pattern, replacement, label, applies_to FROM substitute_rules ORDER BY id"
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


def seed_pattern_rules(conn: sqlite3.Connection, rules: list[dict]) -> int:
    inserted = 0
    for rule in rules:
        pattern = rule.get("pattern")
        if not pattern:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO pattern_rules"
            " (pattern, is_regex, action, label, replace_with, replace_type,"
            "  extract_as, format_str, value_type, keep_token, date_precision, scope)"
            " SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
            " WHERE NOT EXISTS (SELECT 1 FROM pattern_rules WHERE pattern=?)",
            (
                pattern,
                int(bool(rule.get("is_regex", True))),
                rule.get("action", "capture"),
                rule.get("label"),
                rule.get("replace_with"),
                rule.get("replace_type"),
                rule.get("extract_as"),
                rule.get("format_str"),
                rule.get("value_type"),
                int(bool(rule.get("keep_token", False))),
                rule.get("date_precision"),
                rule.get("scope", "both"),
                pattern,
            ),
        )
        if cur.rowcount:
            inserted += 1
            action = rule.get("action", "")
            replace_type = rule.get("replace_type", "")
            replace_with = rule.get("replace_with", "")
            if action == "replace" and replace_type == "synonym" and replace_with:
                _add_synonym_to_vocabulary(conn, replace_with, pattern)
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


def update_face_centroid_with_spread(
    conn: sqlite3.Connection,
    person_id: int,
    new_centroid_blob: bytes,
    new_sample_count: int,
    spread: float,
) -> None:
    conn.execute(
        "UPDATE people SET face_centroid = ?, face_samples = ?, face_centroid_spread = ? WHERE id = ?",
        (new_centroid_blob, new_sample_count, spread, person_id),
    )


def get_all_people_names(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT name, person_id FROM people_names").fetchall()
    return {row["name"]: row["person_id"] for row in rows}


def create_person_from_name(conn: sqlite3.Connection, full_name: str) -> int:
    parts = full_name.strip().split()
    if len(parts) == 1:
        person_id = upsert_person(conn, preferred_name=parts[0])
    elif len(parts) == 2:
        person_id = upsert_person(conn, preferred_name=full_name, first_name=parts[0], last_name=parts[1])
    else:
        person_id = upsert_person(
            conn,
            preferred_name=full_name,
            first_name=parts[0],
            middle_name=" ".join(parts[1:-1]),
            last_name=parts[-1],
        )
    add_person_name(conn, person_id, full_name)
    return person_id


def get_face_embeddings_for_person(
    conn: sqlite3.Connection,
    corpus_conn: sqlite3.Connection,
    person_id: int,
) -> list[bytes]:
    rows = corpus_conn.execute(
        "SELECT embedding FROM file_face_regions WHERE person_id = ?",
        (person_id,),
    ).fetchall()
    return [bytes(row["embedding"]) for row in rows]


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


def get_people_with_cluster_counts(
    kb_conn: sqlite3.Connection,
    corpus_conn: sqlite3.Connection,
) -> list[dict]:
    people = kb_conn.execute(
        "SELECT id, preferred_name, face_samples, face_centroid_spread FROM people ORDER BY preferred_name"
    ).fetchall()
    voice_counts = {
        r[0]: r[1]
        for r in corpus_conn.execute(
            "SELECT person_id, COUNT(*) FROM voice_speaker_clusters"
            " WHERE person_id IS NOT NULL GROUP BY person_id"
        ).fetchall()
    }
    face_counts = {
        r[0]: r[1]
        for r in corpus_conn.execute(
            "SELECT person_id, COUNT(*) FROM face_clusters"
            " WHERE person_id IS NOT NULL GROUP BY person_id"
        ).fetchall()
    }
    return [
        {
            "id": r["id"],
            "preferred_name": r["preferred_name"],
            "voice_cluster_count": voice_counts.get(r["id"], 0),
            "face_cluster_count": face_counts.get(r["id"], 0),
            "face_samples": r["face_samples"] or 0,
            "face_centroid_spread": r["face_centroid_spread"],
        }
        for r in people
    ]


def delete_person(
    kb_conn: sqlite3.Connection,
    corpus_conn: sqlite3.Connection,
    person_id: int,
) -> None:
    row = kb_conn.execute("SELECT id FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        raise KeyError(f"Person {person_id} not found")
    voice_count = corpus_conn.execute(
        "SELECT COUNT(*) FROM voice_speaker_clusters WHERE person_id = ?", (person_id,)
    ).fetchone()[0]
    face_count = corpus_conn.execute(
        "SELECT COUNT(*) FROM face_clusters WHERE person_id = ?", (person_id,)
    ).fetchone()[0]
    if voice_count > 0 or face_count > 0:
        parts = []
        if voice_count:
            parts.append(f"{voice_count} voice cluster(s)")
        if face_count:
            parts.append(f"{face_count} face cluster(s)")
        raise ValueError(f"Cannot delete: person has {' and '.join(parts)} assigned")
    kb_conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    kb_conn.commit()


def merge_people(
    kb_conn: sqlite3.Connection,
    corpus_conn: sqlite3.Connection,
    keep_id: int,
    merge_from_id: int,
) -> None:
    if keep_id == merge_from_id:
        raise ValueError("keep_id and merge_from_id must differ")
    keep_row = kb_conn.execute(
        "SELECT id, preferred_name FROM people WHERE id = ?", (keep_id,)
    ).fetchone()
    if keep_row is None:
        raise KeyError(f"Person {keep_id} not found")
    from_row = kb_conn.execute(
        "SELECT id FROM people WHERE id = ?", (merge_from_id,)
    ).fetchone()
    if from_row is None:
        raise KeyError(f"Person {merge_from_id} not found")
    keep_label = keep_row["preferred_name"]
    voice_clusters = corpus_conn.execute(
        "SELECT id, centroid, member_count FROM voice_speaker_clusters WHERE person_id = ?",
        (merge_from_id,),
    ).fetchall()
    for vc in voice_clusters:
        if vc["centroid"] is not None:
            merge_voice_centroid(kb_conn, keep_id, bytes(vc["centroid"]), vc["member_count"] or 0)
    kb_conn.commit()
    corpus_conn.execute(
        "UPDATE face_clusters SET person_id = ?, label = ? WHERE person_id = ?",
        (keep_id, keep_label, merge_from_id),
    )
    corpus_conn.execute(
        "UPDATE voice_speaker_clusters SET person_id = ?, label = ? WHERE person_id = ?",
        (keep_id, keep_label, merge_from_id),
    )
    corpus_conn.commit()
    kb_conn.execute("DELETE FROM people WHERE id = ?", (merge_from_id,))
    kb_conn.commit()


# ---------------------------------------------------------------------------
# Prompt library
# ---------------------------------------------------------------------------

_BUILTIN_STAGE_PROMPTS = [
    (
        "describe", "system", "Default",
        "Describe this image in detail. Focus on the subjects, setting, "
        "activity, and any visible text or identifiable objects.",
    ),
    (
        "describe", "aggregate", "Default",
        "Using the frame descriptions above, write a single cohesive description of the video.\n\n"
        "Rules:\n"
        "- Prioritise details and themes that appear consistently across multiple frames — "
        "repeated elements are more reliable than single-frame observations.\n"
        "- If a detail appears in only one frame and conflicts with what other frames show "
        "(e.g. a different clothing colour, a person not seen elsewhere), omit it rather than "
        "including potentially hallucinated content.\n"
        "- Where frame descriptions agree or reinforce each other, describe those elements "
        "with confidence.\n"
        "- Focus on the overall content, activity, and setting.",
    ),
    (
        "retag", "system", "Default",
        "You are a metadata tagging assistant. Given a description and a controlled vocabulary, "
        "identify which vocabulary terms apply to this content. You may also propose new terms "
        "that would be good additions to the vocabulary.\n\n"
        "Respond with valid JSON only — no markdown, no explanation. Use this exact schema:\n"
        '{"tags": ["term1", "term2"], "refined_description": "...", "new_terms_proposed": ["term3"]}\n\n'
        "Rules:\n"
        "- tags: only use terms from the vocabulary list; include all that genuinely apply\n"
        "- refined_description: correct obvious errors; keep it factual; do not change meaning\n"
        "- new_terms_proposed: terms you consider valuable that are not in the vocabulary; leave empty if none",
    ),
    (
        "summarize", "system", "Default",
        "You are a metadata summarization assistant. Write a factual, searchable summary "
        "of a media file. Respond with plain text only — no bullet points, no headings, "
        "no explanation outside the summary itself. "
        "Preserve all proper nouns (personal names, place names, event names) exactly as "
        "they appear in the source material — do not paraphrase, normalise, or correct them.",
    ),
]


def seed_stage_prompts(kb_conn: sqlite3.Connection) -> None:
    for stage, prompt_key, name, body in _BUILTIN_STAGE_PROMPTS:
        kb_conn.execute(
            """
            INSERT OR IGNORE INTO stage_prompts (stage, prompt_key, name, body, is_active, is_builtin)
            VALUES (?, ?, ?, ?, 1, 1)
            """,
            (stage, prompt_key, name, body),
        )
    kb_conn.commit()


def load_stage_prompt(
    kb_conn: sqlite3.Connection, stage: str, prompt_key: str, default: str
) -> str:
    try:
        row = kb_conn.execute(
            "SELECT body FROM stage_prompts WHERE stage=? AND prompt_key=? AND is_active=1",
            (stage, prompt_key),
        ).fetchone()
        return row["body"] if row else default
    except sqlite3.OperationalError:
        return default


def list_stage_prompts(kb_conn: sqlite3.Connection) -> list[dict]:
    rows = kb_conn.execute(
        "SELECT * FROM stage_prompts ORDER BY stage, prompt_key, is_builtin DESC, name"
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_stage_prompt(
    kb_conn: sqlite3.Connection,
    stage: str,
    prompt_key: str,
    name: str,
    body: str,
) -> int:
    kb_conn.execute(
        """
        INSERT INTO stage_prompts (stage, prompt_key, name, body, is_active, is_builtin)
        VALUES (?, ?, ?, ?, 0, 0)
        ON CONFLICT (stage, prompt_key, name) DO UPDATE SET body=excluded.body
        """,
        (stage, prompt_key, name, body),
    )
    kb_conn.commit()
    row = kb_conn.execute(
        "SELECT id FROM stage_prompts WHERE stage=? AND prompt_key=? AND name=?",
        (stage, prompt_key, name),
    ).fetchone()
    return row["id"]


def set_active_stage_prompt(
    kb_conn: sqlite3.Connection, stage: str, prompt_key: str, prompt_id: int
) -> None:
    row = kb_conn.execute(
        "SELECT id FROM stage_prompts WHERE id=?", (prompt_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"No stage_prompt with id={prompt_id}")
    kb_conn.execute(
        "UPDATE stage_prompts SET is_active=0 WHERE stage=? AND prompt_key=?",
        (stage, prompt_key),
    )
    kb_conn.execute(
        "UPDATE stage_prompts SET is_active=1 WHERE id=?",
        (prompt_id,),
    )
    kb_conn.commit()


def delete_stage_prompt(kb_conn: sqlite3.Connection, prompt_id: int) -> None:
    row = kb_conn.execute(
        "SELECT is_builtin, is_active, stage, prompt_key FROM stage_prompts WHERE id=?",
        (prompt_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No stage_prompt with id={prompt_id}")
    if row["is_builtin"]:
        raise ValueError("Built-in prompts cannot be deleted")
    stage, prompt_key, was_active = row["stage"], row["prompt_key"], row["is_active"]
    kb_conn.execute("DELETE FROM stage_prompts WHERE id=?", (prompt_id,))
    if was_active:
        builtin = kb_conn.execute(
            "SELECT id FROM stage_prompts WHERE stage=? AND prompt_key=? AND is_builtin=1",
            (stage, prompt_key),
        ).fetchone()
        if builtin:
            kb_conn.execute(
                "UPDATE stage_prompts SET is_active=1 WHERE id=?", (builtin["id"],)
            )
    kb_conn.commit()


# ---------------------------------------------------------------------------
# Register seeding helpers
# ---------------------------------------------------------------------------

def seed_location_register(conn: sqlite3.Connection, csv_path) -> int:
    """Import locations from CSV into entity_locations. Skip if already populated.

    Returns the number of rows imported, or 0 if the table was already populated.
    """
    import csv as _csv
    from pathlib import Path as _Path

    try:
        existing = conn.execute("SELECT COUNT(*) FROM entity_locations").fetchone()[0]
        if existing > 0:
            return 0
    except Exception:
        pass

    with open(_Path(csv_path), newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        raw_headers = reader.fieldnames or []
        headers = [h.strip().lower().replace(" ", "_") for h in raw_headers]
        key_col = headers[0] if headers else "location"
        create_entity_table(conn, "locations", headers, key_col)
        register_entity_table(
            conn,
            table_name="locations",
            display_name="Locations",
            trigger_word="",
            trigger_aliases_json="[]",
            key_column=key_col,
            match_type="gps",
            source_csv=str(csv_path),
        )
        imported = 0
        for raw_row in reader:
            row = {h: raw_row.get(orig, "").strip() for h, orig in zip(headers, raw_headers)}
            key_val = row.get(key_col, "")
            if key_val in ("", "-"):
                continue
            upsert_entity_row(conn, "locations", row)
            imported += 1
    conn.commit()
    return imported


def seed_people_register(conn: sqlite3.Connection, csv_path) -> int:
    """Import people from CSV into people/people_names/life_events. Skip if already populated.

    Returns the number of people imported, or 0 if the table was already populated.
    """
    import csv as _csv
    from pathlib import Path as _Path

    existing = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    if existing > 0:
        return 0

    with open(_Path(csv_path), newline="", encoding="utf-8-sig") as fh:
        rows = list(_csv.DictReader(fh))

    nameid_to_pid: dict[str, int] = {}
    for row in rows:
        nameid = (row.get("NameID") or "").strip()
        if not nameid:
            continue
        prefer_nick = (row.get("Prefer NickName") or "").strip().upper() == "TRUE"
        nick_names = [n.strip() for n in (row.get("Nick Names") or "").split("|") if n.strip()]
        if prefer_nick and nick_names:
            preferred = nick_names[0]
        else:
            first = (row.get("First Name") or "").strip()
            last = (row.get("Last Name") or "").strip()
            preferred = " ".join(p for p in [first, last] if p) or nameid
        person_id = upsert_person(
            conn,
            preferred_name=preferred,
            title=(row.get("Title") or "").strip(),
            first_name=(row.get("First Name") or "").strip(),
            middle_name=(row.get("Middle Name") or "").strip(),
            last_name=(row.get("Last Name") or "").strip(),
            family=(row.get("Family") or "").strip().upper() == "TRUE",
        )
        nameid_to_pid[nameid] = person_id

    for row in rows:
        nameid = (row.get("NameID") or "").strip()
        person_id = nameid_to_pid.get(nameid)
        if not person_id:
            continue
        meta_name = (row.get("Metadata Name") or "").strip()
        if meta_name:
            add_person_name(conn, person_id, meta_name, is_metadata_form=True)
        for name in (n.strip() for n in (row.get("Nick Names") or "").split("|") if n.strip()):
            add_person_name(conn, person_id, name)
        for name in (n.strip() for n in (row.get("Married Names") or "").split("|") if n.strip()):
            add_person_name(conn, person_id, name)

    for row in rows:
        nameid = (row.get("NameID") or "").strip()
        person_id = nameid_to_pid.get(nameid)
        if not person_id:
            continue
        birth = (row.get("birth_date") or "").strip()
        if birth:
            add_life_event(conn, person_id, "birth", birth)
        marriage = (row.get("date_marriage") or "").strip()
        if marriage:
            spouse_nameid = (row.get("SpouseID") or "").strip()
            partner_id = nameid_to_pid.get(spouse_nameid) if spouse_nameid else None
            add_life_event(conn, person_id, "marriage", marriage, partner_id)
        death = (row.get("death_date") or "").strip()
        if death:
            add_life_event(conn, person_id, "death", death)

    conn.commit()
    return len(nameid_to_pid)

