"""Vocabulary proposal generation — entity alias columns and NLP lemma clustering."""
import logging

logger = logging.getLogger(__name__)

_ALIAS_COLUMNS = frozenset({
    "aliases", "alias", "alt_name", "alt_names",
    "nickname", "nicknames", "aka", "also_known_as",
})


def _detect_alias_columns(column_names: list[str]) -> list[str]:
    return [c for c in column_names if c.lower() in _ALIAS_COLUMNS]


def _suggest_canonical(terms: list[str], source: str, source_detail: str | None) -> str:
    """Return the most likely canonical term for a proposal group."""
    if not terms:
        return ""
    detail = source_detail or ""
    # Encoded hint: "canonical:<name> | ..." — used by entity + LLM generators
    if detail.startswith("canonical:"):
        hint = detail.split("|")[0].removeprefix("canonical:").strip()
        if hint in terms:
            return hint
    # NLP lemma: source_detail is "lemma: <word>"
    if source == "nlp_lemma" and detail.startswith("lemma:"):
        lemma = detail.removeprefix("lemma:").strip()
        if lemma in terms:
            return lemma
    return min(terms, key=len)


def _entity_proposals(kb_conn) -> list[dict]:
    import re as _re
    proposals = []
    try:
        tables = kb_conn.execute(
            "SELECT table_name, display_name, key_column FROM entity_table_registry"
        ).fetchall()
    except Exception:
        return []
    for reg in tables:
        table_name = reg["table_name"]
        display_name = reg["display_name"]
        key_col = reg["key_column"]
        safe = _re.sub(r"[^A-Za-z0-9_]", "_", table_name)
        full_table = f"entity_{safe}"
        try:
            col_rows = kb_conn.execute(f'PRAGMA table_info("{full_table}")').fetchall()
        except Exception:
            continue
        col_names = [r["name"] for r in col_rows]
        alias_cols = _detect_alias_columns(col_names)
        if not alias_cols or key_col not in col_names:
            continue
        try:
            rows = kb_conn.execute(f'SELECT * FROM "{full_table}"').fetchall()
        except Exception:
            continue
        for row in rows:
            try:
                canonical = row[key_col]
            except (KeyError, IndexError):
                continue
            if not canonical:
                continue
            synonyms: list[str] = []
            for alias_col in alias_cols:
                try:
                    val = row[alias_col]
                except (KeyError, IndexError):
                    continue
                if val:
                    for part in str(val).split("|"):
                        part = part.strip()
                        if part and part != canonical:
                            synonyms.append(part)
            if synonyms:
                cols_label = ", ".join(alias_cols)
                suffix = "columns" if len(alias_cols) > 1 else "column"
                proposals.append({
                    "terms": [canonical] + synonyms,
                    "source": "entity",
                    "source_detail": f"canonical:{canonical} | {display_name} → {cols_label} {suffix}",
                })
    return proposals


def _nlp_proposals(kb_conn) -> list[dict]:
    try:
        import spacy
    except ImportError:
        logger.debug("spaCy not available; skipping NLP lemma proposals")
        return []
    rows = kb_conn.execute(
        "SELECT term FROM vocabulary WHERE source IN ('accepted', 'user', 'seeded') ORDER BY term"
    ).fetchall()
    terms = [r["term"] for r in rows]
    if not terms:
        return []
    nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])
    lemma_groups: dict[str, list[str]] = {}
    for term in terms:
        doc = nlp(term)
        if len(doc) == 1:
            lemma = doc[0].lemma_.lower()
        else:
            lemma = term.lower()
        if term not in lemma_groups.get(lemma, []):
            lemma_groups.setdefault(lemma, []).append(term)
    proposals = []
    for lemma, group in lemma_groups.items():
        if len(group) >= 2:
            proposals.append({
                "terms": group,
                "source": "nlp_lemma",
                "source_detail": f"lemma: {lemma}",
            })
    return proposals


def generate_proposals(kb_conn) -> int:
    from src.db.kb import add_vocab_proposal
    added = 0
    for p in _entity_proposals(kb_conn):
        if add_vocab_proposal(kb_conn, p["terms"], p["source"], p.get("source_detail")):
            added += 1
    for p in _nlp_proposals(kb_conn):
        if add_vocab_proposal(kb_conn, p["terms"], p["source"], p.get("source_detail")):
            added += 1
    return added
