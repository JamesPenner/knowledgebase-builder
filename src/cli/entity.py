"""CLI commands for entity table and people register management."""
import csv
import re
from difflib import SequenceMatcher
import typer
from pathlib import Path

app = typer.Typer(help="Entity table and people register management")

_SAFE_COL = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*$")


def _normalise(s: str) -> str:
    return " ".join(s.lower().split())


def _find_near_duplicates(
    candidate: str, existing: list[str], threshold: float
) -> list[tuple[str, float]]:
    norm_c = _normalise(candidate)
    results = []
    for e in existing:
        norm_e = _normalise(e)
        if norm_e == norm_c:
            continue  # exact match handled by upsert ON CONFLICT
        score = SequenceMatcher(None, norm_c, norm_e).ratio()
        if score >= threshold:
            results.append((e, score))
    return results


def _resolve_kb(name: str | None) -> tuple[Path, Path]:
    from src.db.registry import get_active_kb_path, get_kb_path, open_registry
    reg = open_registry(Path("."))
    try:
        folder = get_kb_path(reg, name) if name else get_active_kb_path(reg)
        if folder is None:
            typer.echo("Error: no active KB. Use --kb <name>.", err=True)
            raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    return folder / "corpus.db", folder / "knowledge.db"


@app.command("import-locations")
def import_locations(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    csv_file: str | None = typer.Option(None, "--csv", help="Path to CSV (default: reference/registers/Index_of_Locations.csv)"),
    similarity_threshold: float = typer.Option(0.85, "--similarity-threshold", help="Minimum similarity ratio to flag a near-duplicate pair (0.0–1.0)"),
    force: bool = typer.Option(False, "--force", help="Import even when near-duplicates are detected"),
) -> None:
    """Import a locations register CSV into the entity_locations table."""
    from src.db.kb import (
        create_entity_table,
        get_entity_table_keys,
        open_kb,
        register_entity_table,
        upsert_entity_row,
    )

    corpus_path, kb_path = _resolve_kb(kb)
    kb_folder = kb_path.parent

    src = Path(csv_file) if csv_file else kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    if not src.exists():
        typer.echo(f"Error: CSV not found: {src}", err=True)
        raise typer.Exit(1)

    with open(src, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        raw_headers = reader.fieldnames or []
        if not raw_headers:
            typer.echo("Error: CSV has no headers.", err=True)
            raise typer.Exit(1)

        for h in raw_headers:
            if not _SAFE_COL.match(h):
                typer.echo(f"Error: unsafe column name in CSV: {h!r}", err=True)
                raise typer.Exit(1)

        headers = [h.strip().lower().replace(" ", "_") for h in raw_headers]
        key_col = headers[0]

        kb_conn = open_kb(kb_path)
        create_entity_table(kb_conn, "locations", headers, key_col)
        register_entity_table(
            kb_conn,
            table_name="locations",
            display_name="Locations",
            trigger_word="",
            trigger_aliases_json="[]",
            key_column=key_col,
            match_type="gps",
            source_csv=str(src),
        )

        existing_keys = get_entity_table_keys(kb_conn, "locations", key_col)
        session_keys: list[str] = []
        rows_to_import: list[dict] = []
        flags: list[tuple[str, str, float]] = []
        skipped = 0

        for raw_row in reader:
            row = {h: raw_row.get(orig, "").strip() for h, orig in zip(headers, raw_headers)}
            key_val = row.get(key_col, "")
            if key_val in ("", "-"):
                skipped += 1
                continue
            for other, score in _find_near_duplicates(key_val, existing_keys + session_keys, similarity_threshold):
                flags.append((key_val, other, score))
            rows_to_import.append(row)
            session_keys.append(key_val)

        if flags and not force:
            for name_a, name_b, score in flags:
                typer.echo(f"  near-duplicate: {name_a!r} ≈ {name_b!r} (similarity={score:.2f})")
            typer.echo(f"Import aborted — {len(flags)} near-duplicate(s) found. Use --force to import anyway.")
            kb_conn.close()
            raise typer.Exit(1)

        if flags and force:
            typer.echo(f"Warning: {len(flags)} near-duplicate(s) ignored (--force).")

        imported = 0
        for row in rows_to_import:
            upsert_entity_row(kb_conn, "locations", row)
            imported += 1

        kb_conn.commit()
        kb_conn.close()

    typer.echo(f"Locations imported: {imported} rows ({skipped} skipped).")


@app.command("import-people")
def import_people(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    csv_file: str | None = typer.Option(None, "--csv", help="Path to CSV (default: reference/registers/Index_of_People.csv)"),
) -> None:
    """Import a people register CSV into the people, people_names, and life_events tables."""
    from src.db.kb import (
        add_life_event,
        add_person_name,
        open_kb,
        upsert_person,
    )

    corpus_path, kb_path = _resolve_kb(kb)
    kb_folder = kb_path.parent

    src = Path(csv_file) if csv_file else kb_folder / "reference" / "registers" / "Index_of_People.csv"
    if not src.exists():
        typer.echo(f"Error: CSV not found: {src}", err=True)
        raise typer.Exit(1)

    with open(src, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        typer.echo("CSV is empty.")
        return

    kb_conn = open_kb(kb_path)

    # Pass 1: import people rows, build NameID → person_id dict
    nameid_to_pid: dict[str, int] = {}
    for row in rows:
        nameid = (row.get("NameID") or "").strip()
        if not nameid:
            continue

        prefer_nick = (row.get("Prefer NickName") or "").strip().upper() == "TRUE"
        nick_names_raw = (row.get("Nick Names") or "").strip()
        nick_names = [n.strip() for n in nick_names_raw.split("|") if n.strip()]

        if prefer_nick and nick_names:
            preferred = nick_names[0]
        else:
            first = (row.get("First Name") or "").strip()
            last = (row.get("Last Name") or "").strip()
            preferred = " ".join(p for p in [first, last] if p) or nameid

        person_id = upsert_person(
            kb_conn,
            preferred_name=preferred,
            title=(row.get("Title") or "").strip(),
            first_name=(row.get("First Name") or "").strip(),
            middle_name=(row.get("Middle Name") or "").strip(),
            last_name=(row.get("Last Name") or "").strip(),
            family=(row.get("Family") or "").strip().upper() == "TRUE",
        )
        nameid_to_pid[nameid] = person_id

    # Pass 2: import name forms
    for row in rows:
        nameid = (row.get("NameID") or "").strip()
        person_id = nameid_to_pid.get(nameid)
        if not person_id:
            continue

        meta_name = (row.get("Metadata Name") or "").strip()
        if meta_name:
            add_person_name(kb_conn, person_id, meta_name, is_metadata_form=True)

        nick_names_raw = (row.get("Nick Names") or "").strip()
        for name in (n.strip() for n in nick_names_raw.split("|") if n.strip()):
            add_person_name(kb_conn, person_id, name)

        married_names_raw = (row.get("Married Names") or "").strip()
        for name in (n.strip() for n in married_names_raw.split("|") if n.strip()):
            add_person_name(kb_conn, person_id, name)

    # Pass 3: life events with partner_id lookup
    for row in rows:
        nameid = (row.get("NameID") or "").strip()
        person_id = nameid_to_pid.get(nameid)
        if not person_id:
            continue

        birth = (row.get("birth_date") or "").strip()
        if birth:
            add_life_event(kb_conn, person_id, "birth", birth)

        marriage = (row.get("date_marriage") or "").strip()
        if marriage:
            spouse_nameid = (row.get("SpouseID") or "").strip()
            partner_id = nameid_to_pid.get(spouse_nameid) if spouse_nameid else None
            add_life_event(kb_conn, person_id, "marriage", marriage, partner_id)

        death = (row.get("death_date") or "").strip()
        if death:
            add_life_event(kb_conn, person_id, "death", death)

    kb_conn.close()
    typer.echo(f"People imported: {len(nameid_to_pid)} people from {src.name}.")


@app.command("import-bundle")
def import_entity_bundle(
    bundle: str = typer.Argument(..., help="Path to KB export bundle directory"),
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Import entity tables from a KB export bundle into the active KB."""
    from src.db.kb import open_kb, seed_entity_bundle

    _corpus_path, kb_path = _resolve_kb(kb)
    entities_dir = Path(bundle) / "entities"
    if not entities_dir.exists():
        typer.echo("No entities/ folder found in bundle.")
        return

    kb_conn = open_kb(kb_path)
    try:
        tables, rows, links = seed_entity_bundle(kb_conn, entities_dir)
        typer.echo(f"Imported {tables} entity table(s), {rows} row(s), {links} link(s).")
    finally:
        kb_conn.close()
