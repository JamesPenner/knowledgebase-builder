import typer
from pathlib import Path

app = typer.Typer(help="Knowledge base management")


def _write_library_yaml(path: Path) -> None:
    import yaml
    data = {
        "scan": {
            "default_file_types": "all",
            "exclude_dirs": ["System Volume Information", "$RECYCLE.BIN", "@eaDir", ".thumbnails"],
            "exclude_patterns": [],
        },
        "pipeline": {
            "scan_batch_size": 1000,
            "metadata_batch_size": 100,
            "vision_threads": 1,
            "thumbnail_max_px": 400,
            "thumbnail_quality": 85,
        },
        "hashing": {
            "phash_similarity_threshold": 10,
            "area_hash_grid": 8,
            "video_frame_similarity_threshold": 10,
        },
        "video": {"collage_frames": 9, "collage_grid_cols": 3, "collage_grid_rows": 3},
        "taxonomy": {"default_matcher": "pattern", "llm_confidence_threshold": 0.6},
        "map": {"tile_source": "online", "default_lat": 51.0, "default_lon": -120.0, "default_zoom": 6},
        "review": {"auto_advance": True, "display_size": "fullscreen"},
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _write_metrics_yaml(path: Path) -> None:
    import yaml
    data = {
        "metrics": [
            {"type": "pipeline_completion", "title": "Pipeline Progress"},
            {"type": "storage_breakdown", "title": "Storage by Extension"},
            {"type": "vocabulary_suggestions", "title": "Pending Vocabulary Suggestions"},
            {"type": "duplicate_groups", "title": "Duplicate Groups"},
            {"type": "file_count", "title": "Files by Year", "group_by": "year", "limit": 10},
        ]
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _find_catalogue_template() -> Path | None:
    """Auto-discover the catalogue's default KB as a reference template."""
    cwd = Path(".").resolve()
    candidate = cwd.parent / "portable_basic_image_catalogue" / "knowledge_bases" / "default"
    return candidate if candidate.exists() else None


def _copy_or_write(src_kb: Path | None, rel: str, dst_kb: Path, stub_fn) -> bool:
    """Copy rel from src_kb into dst_kb, or call stub_fn(dst) if not found."""
    import shutil
    dst = dst_kb / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src_kb is not None:
        src = src_kb / rel
        if src.exists():
            shutil.copy2(src, dst)
            return True
    stub_fn(dst)
    return False


def _stub_dates_yaml(path: Path) -> None:
    import yaml
    data = {
        "enabled": True,
        "people_dates_enabled": True,
        "season_hemisphere": "north",
        "calendar": [
            {"name": "Christmas", "type": "fixed", "month": 12, "day": 25, "algorithm": None, "enabled": True},
            {"name": "Christmas Eve", "type": "fixed", "month": 12, "day": 24, "algorithm": None, "enabled": True},
            {"name": "New Year's Day", "type": "fixed", "month": 1, "day": 1, "algorithm": None, "enabled": True},
            {"name": "New Year's Eve", "type": "fixed", "month": 12, "day": 31, "algorithm": None, "enabled": True},
            {"name": "Halloween", "type": "fixed", "month": 10, "day": 31, "algorithm": None, "enabled": True},
            {"name": "Canada Day", "type": "fixed", "month": 7, "day": 1, "algorithm": None, "enabled": True},
            {"name": "Remembrance Day", "type": "fixed", "month": 11, "day": 11, "algorithm": None, "enabled": True},
            {"name": "Valentine's Day", "type": "fixed", "month": 2, "day": 14, "algorithm": None, "enabled": True},
            {"name": "Easter Sunday", "type": "computed", "month": None, "day": None, "algorithm": "easter", "enabled": True},
            {"name": "Thanksgiving (Canada)", "type": "computed", "month": None, "day": None, "algorithm": "thanksgiving_ca", "enabled": True},
            {"name": "Thanksgiving (US)", "type": "computed", "month": None, "day": None, "algorithm": "thanksgiving_us", "enabled": False},
            {"name": "Mother's Day", "type": "computed", "month": None, "day": None, "algorithm": "mothers_day", "enabled": False},
            {"name": "Father's Day", "type": "computed", "month": None, "day": None, "algorithm": "fathers_day", "enabled": False},
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _stub_derive_rules_yaml(path: Path) -> None:
    path.write_text(
        "# derive_rules.yaml — Deterministic keyword derivation rules\n"
        "# See catalogue documentation for full field list.\n\n"
        "field_rules:\n\n"
        "  - field: aspect_ratio\n    operator: \"<\"\n    value: 0.9\n    output: portrait\n\n"
        "  - field: aspect_ratio\n    operator: \">\"\n    value: 1.1\n    output: landscape\n\n"
        "  - field: aspect_ratio\n    operator: \">\"\n    value: 2.4\n    output: panoramic\n\n"
        "  - field: focal_length_35mm\n    operator: \"<\"\n    value: 24\n    output: ultra_wide\n\n"
        "  - field: focal_length_35mm\n    operator: \">\"\n    value: 70\n    output: telephoto\n\n"
        "  - field: capture_month\n    operator: in\n    value: [3, 4, 5]\n    output: spring\n\n"
        "  - field: capture_month\n    operator: in\n    value: [6, 7, 8]\n    output: summer\n\n"
        "  - field: capture_month\n    operator: in\n    value: [9, 10, 11]\n    output: autumn\n\n"
        "  - field: capture_month\n    operator: in\n    value: [12, 1, 2]\n    output: winter\n\n"
        "  - field: gps_present\n    operator: \"=\"\n    value: true\n    output: geotagged\n\n"
        "compound_rules: []\n\n"
        "image_analysis: []\n",
        encoding="utf-8",
    )


def _stub_taxonomy_yaml(path: Path) -> None:
    path.write_text(
        "name: \"KB Taxonomy\"\nversion: \"1.0\"\n\ncategories:\n\n"
        "  event:\n    terms:\n      - birthday\n      - anniversary\n      - wedding\n"
        "      - graduation\n      - celebration\n      - party\n    subcategories:\n"
        "      holiday:\n        terms:\n          - Christmas\n          - Easter\n"
        "          - Halloween\n          - Thanksgiving\n          - New Year\n"
        "          - Canada Day\n          - Remembrance Day\n\n"
        "  season:\n    terms:\n      - spring\n      - summer\n      - autumn\n"
        "      - winter\n\n"
        "  place_type:\n    terms:\n      - indoor\n      - outdoor\n      - urban\n"
        "      - rural\n    subcategories:\n      nature:\n        terms:\n"
        "          - beach\n          - forest\n          - mountain\n          - lake\n"
        "          - national park\n\n"
        "  source_media:\n    terms:\n      - digital photograph\n      - film photograph\n"
        "      - print scan\n      - slide scan\n",
        encoding="utf-8",
    )


def _stub_stopwords_txt(path: Path) -> None:
    path.write_text(
        "# Common stop words — extend as needed\n"
        "a\nan\nthe\nis\nare\nwas\nwere\nbe\nbeen\nbeing\n"
        "to\nof\nand\nin\nit\nfor\non\nwith\nat\nby\nfrom\n"
        "this\nthat\nthese\nthose\nhe\nshe\nit\nwe\nthey\n"
        "his\nher\nits\nour\ntheir\nhow\nwhat\nwhen\nwhere\nwho\n"
        "# Photo meta-words\nbackground\nforeground\nimage\nphoto\nphotograph\npicture\nscene\nshot\n",
        encoding="utf-8",
    )


def _stub_vocabulary_csv(path: Path) -> None:
    path.write_text(
        "domain,category,keyword,synonyms,related_terms,notes\n"
        "Activities,Outdoor,Camping,Camp,,\n"
        "Activities,Outdoor,Hiking,,,\n"
        "Nature,Landscape,Beach,,,\n"
        "Nature,Landscape,Mountain,,,\n"
        "Nature,Landscape,Forest,,,\n"
        "Events,Celebration,Birthday,,Celebration,\n"
        "Events,Celebration,Anniversary,,Celebration,\n"
        "Events,Social,Wedding,,,\n"
        "People,Family,Baby,,,\n"
        "People,Family,Children,Kids,,\n",
        encoding="utf-8",
    )


def _stub_acdsee_mapping_yaml(path: Path) -> None:
    path.write_text(
        "# ACDSee Categories adapter — variable name to canonical field mapping.\n\n"
        "field_mappings:\n"
        "  Person01: person_in_image\n  Person02: person_in_image\n"
        "  LocationCreatedCountryName: location_country\n"
        "  LocationCreatedProvinceState: location_state\n"
        "  LocationCreatedCity: location_city\n"
        "  LocationCreatedLocationName: location_sublocation\n"
        "  LocalityGeneral: location_locality\n"
        "  Event01: event\n  Event02: event\n  Event03: event\n"
        "  ShotSubject: keywords\n",
        encoding="utf-8",
    )


def _stub_acdsee_template(path: Path) -> None:
    path.write_text(
        "-Categories<<Categories>"
        "<Category Assigned=\"1\">Dates<Category Assigned=\"1\">${Year}"
        "<Category Assigned=\"1\">${Month}</Category></Category></Category>"
        "<Category Assigned=\"1\">People<Category Assigned=\"1\">All"
        "<Category Assigned=\"1\">${Person01}</Category></Category></Category>"
        "<Category Assigned=\"1\">Places<Category Assigned=\"1\">Country"
        "<Category Assigned=\"1\">${LocationCreatedCountryName}</Category></Category></Category>"
        "</Categories>",
        encoding="utf-8",
    )


def _populate_reference_files(kb_folder: Path) -> None:
    template = _find_catalogue_template()
    copied = []
    stubbed = []

    files = [
        ("reference/dates.yaml",                        _stub_dates_yaml),
        ("reference/derive_rules.yaml",                 _stub_derive_rules_yaml),
        ("reference/taxonomy.yaml",                     _stub_taxonomy_yaml),
        ("reference/stopwords.txt",                     _stub_stopwords_txt),
        ("seed/vocabulary.csv",                         _stub_vocabulary_csv),
        ("adapters/acdsee/mapping.yaml",                _stub_acdsee_mapping_yaml),
        ("adapters/acdsee/ACDSeeCategoriesTemplate.arg", _stub_acdsee_template),
    ]

    for rel, stub_fn in files:
        was_copied = _copy_or_write(template, rel, kb_folder, stub_fn)
        (copied if was_copied else stubbed).append(rel)

    if template and copied:
        typer.echo(f"  Reference files copied from: {template}")
    if stubbed:
        typer.echo(f"  Reference stubs written for: {', '.join(stubbed)}")


def _load_general_media_seed(kb_path: Path) -> None:
    """Load general-media seed stopwords and capture rules into knowledge.db."""
    from src.db.kb import open_kb, seed_capture_rules, seed_stopwords

    seed_root = Path("seed") / "general-media"
    kb_conn = open_kb(kb_path)
    try:
        stopwords_file = seed_root / "stopwords.txt"
        if stopwords_file.exists():
            terms = [
                ln.strip() for ln in stopwords_file.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
            seed_stopwords(kb_conn, terms)

        rules_file = seed_root / "capture_rules.yaml"
        if rules_file.exists():
            import yaml
            rules = yaml.safe_load(rules_file.read_text(encoding="utf-8")) or []
            seed_capture_rules(kb_conn, rules)
    finally:
        kb_conn.close()


def _import_kb_bundle(kb_path: Path, bundle_path: Path) -> None:
    """Import a prior KB export bundle into knowledge.db with source='seeded'."""
    import csv as _csv
    from src.db.kb import (
        add_vocabulary_term,
        open_kb,
        seed_capture_rules,
        seed_corrections_exact,
        seed_reject_tokens,
        seed_stopwords,
        seed_substitute_rules,
    )

    kb_conn = open_kb(kb_path)
    try:
        vocab_file = bundle_path / "vocabulary.csv"
        if vocab_file.exists():
            with open(vocab_file, newline="", encoding="utf-8") as fh:
                for row in _csv.DictReader(fh):
                    add_vocabulary_term(kb_conn, row["term"], row.get("synonyms_json", "[]"), source="seeded")
            kb_conn.commit()

        stopwords_file = bundle_path / "stopwords.txt"
        if stopwords_file.exists():
            terms = [
                ln.strip() for ln in stopwords_file.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
            seed_stopwords(kb_conn, terms)

        corrections_file = bundle_path / "corrections.yaml"
        if corrections_file.exists():
            import yaml
            data = yaml.safe_load(corrections_file.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                seed_corrections_exact(kb_conn, data)

        patterns_file = bundle_path / "patterns.yaml"
        if patterns_file.exists():
            import yaml
            data = yaml.safe_load(patterns_file.read_text(encoding="utf-8")) or {}
            seed_capture_rules(kb_conn, data.get("capture_rules") or [])
            seed_substitute_rules(kb_conn, data.get("substitute_rules") or [])
            # pattern_corrections go into corrections table with type='pattern'
            for pc in (data.get("pattern_corrections") or []):
                kb_conn.execute(
                    "INSERT OR IGNORE INTO corrections (raw_term, canonical_term, type, correction_kind)"
                    " SELECT ?, ?, 'pattern', ?"
                    " WHERE NOT EXISTS (SELECT 1 FROM corrections"
                    " WHERE raw_term=? AND type='pattern')",
                    (pc.get("pattern"), pc.get("canonical"), pc.get("correction_kind"), pc.get("pattern")),
                )
            kb_conn.commit()

        reject_file = bundle_path / "reject_tokens.csv"
        if reject_file.exists():
            tokens = []
            with open(reject_file, newline="", encoding="utf-8") as fh:
                for row in _csv.DictReader(fh):
                    tokens.append(row)
            seed_reject_tokens(kb_conn, tokens)

        entities_dir = bundle_path / "entities"
        if entities_dir.exists():
            from src.db.kb import seed_entity_bundle
            tables, rows, links = seed_entity_bundle(kb_conn, entities_dir)
            typer.echo(f"  Entities: {tables} table(s), {rows} row(s), {links} link(s)")

        typer.echo(f"  Bundle imported from: {bundle_path}")
    finally:
        kb_conn.close()


@app.command("create")
def kb_create(
    name: str = typer.Argument(..., help="KB name (used as folder name and registry key)"),
    template: str = typer.Option("blank", "--template", help="Seed template: blank|general-media"),
    import_kb: str | None = typer.Option(
        None, "--import-kb", help="Path to a prior KB export bundle directory"
    ),
) -> None:
    """Create a new knowledge base."""
    from src.db.corpus import open_corpus
    from src.db.kb import open_kb
    from src.db.registry import open_registry, list_kbs, register_kb, set_active

    if template not in ("blank", "general-media"):
        typer.echo(f"Error: unknown template '{template}'. Choose blank or general-media.", err=True)
        raise typer.Exit(1)

    kb_root = Path("knowledge-bases")
    kb_root.mkdir(exist_ok=True)
    kb_folder = kb_root / name
    if kb_folder.exists():
        typer.echo(f"Error: folder already exists: {kb_folder}", err=True)
        raise typer.Exit(1)

    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "reference" / "registers").mkdir()
    (kb_folder / "seed").mkdir()
    open_corpus(kb_folder / "corpus.db").close()
    open_kb(kb_folder / "knowledge.db").close()
    _write_library_yaml(kb_folder / "library.yaml")
    _write_metrics_yaml(kb_folder / "metrics.yaml")
    _populate_reference_files(kb_folder)

    if template == "general-media":
        _load_general_media_seed(kb_folder / "knowledge.db")
        typer.echo("  General-media seed data loaded.")

    if import_kb:
        _import_kb_bundle(kb_folder / "knowledge.db", Path(import_kb))

    reg = open_registry(Path("."))
    try:
        register_kb(reg, name, kb_folder.resolve())
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    existing = list_kbs(reg)
    if len(existing) == 1:
        set_active(reg, name)
        typer.echo(f"Created KB '{name}' at {kb_folder} (set as active)")
    else:
        typer.echo(f"Created KB '{name}' at {kb_folder}")


@app.command("set-active")
def kb_set_active(
    name: str = typer.Argument(..., help="KB name to make active"),
) -> None:
    """Set the active knowledge base."""
    from pathlib import Path
    from src.db.registry import open_registry, set_active

    reg = open_registry(Path("."))
    try:
        set_active(reg, name)
        typer.echo(f"'{name}' is now the active KB.")
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


@app.command("list")
def kb_list() -> None:
    """List all registered knowledge bases."""
    from pathlib import Path
    from src.db.registry import list_kbs, open_registry

    reg = open_registry(Path("."))
    kbs = list_kbs(reg)
    if not kbs:
        typer.echo("No knowledge bases registered.")
        return
    for kb in kbs:
        active = " *" if kb["is_active"] else "  "
        typer.echo(f"{active} {kb['name']:<30} {kb['path']}")


@app.command("delete")
def kb_delete(
    name: str = typer.Argument(..., help="KB name to remove from registry"),
) -> None:
    """Remove a KB from the registry. Disk files are not deleted."""
    from pathlib import Path
    from src.db.registry import delete_kb, open_registry

    reg = open_registry(Path("."))
    try:
        path = delete_kb(reg, name)
        typer.echo(f"KB '{name}' removed from registry. Files at {path} were not deleted.")
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


@app.command("health")
def kb_health(
    name: str = typer.Argument(..., help="KB name to inspect"),
) -> None:
    """Check KB files, environment tools, and scaffold completeness."""
    from pathlib import Path
    from src.config import load_config
    from src.db.registry import get_kb_path, open_registry
    from src.health import run_checks

    reg = open_registry(Path("."))
    try:
        kb_folder = get_kb_path(reg, name)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    config = load_config(Path("config.yaml"), kb_folder / "config.yaml")

    corpus_conn = kb_conn = None
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"
    if corpus_path.exists() and kb_path.exists():
        from src.db.corpus import open_corpus
        from src.db.kb import open_kb
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)

    checks = run_checks(config, corpus_conn, kb_conn, kb_folder)

    if corpus_conn:
        corpus_conn.close()
    if kb_conn:
        kb_conn.close()

    _GROUPS = [
        ("Environment (Required)", "error"),
        ("Optional Tools", "warning"),
        ("KB State", "info"),
        ("KB Scaffold Files", "info"),
    ]
    _GROUP_IDS = [
        {"exiftool", "ffmpeg"},
        {"vision_model", "text_model", "spacy_model", "field_map"},
        {"sources", "corpus_files", "vocabulary", "focus", "unknown_fields"},
        {"library_yaml", "exiftool_config", "dates_yaml", "derive_rules_yaml", "taxonomy_yaml"},
    ]

    typer.echo(f"\nKB health: {name}  ({kb_folder})\n")
    has_error = False
    for (group_label, _), id_set in zip(_GROUPS, _GROUP_IDS):
        typer.echo(f"  {group_label}")
        for chk in checks:
            if chk.id not in id_set:
                continue
            if chk.ok:
                prefix = "OK  "
            elif chk.severity == "error":
                prefix = "FAIL"
                has_error = True
            elif chk.severity == "warning":
                prefix = "WARN"
            else:
                prefix = "INFO"
            suffix = f"  ({chk.detail})" if chk.detail else ""
            fix = f"  → {chk.fix}" if chk.fix and not chk.ok else ""
            typer.echo(f"    [{prefix}] {chk.label}{suffix}{fix}")
        typer.echo("")

    if has_error:
        raise typer.Exit(1)


@app.command("generate-taxonomy")
def generate_taxonomy(
    name: str = typer.Argument(..., help="KB name"),
) -> None:
    """Populate reference/taxonomy.yaml from vocabulary, classify rules, people, and entity tables."""
    import yaml
    from src.db.kb import build_taxonomy_data, merge_taxonomy, open_kb
    from src.db.registry import get_kb_path, open_registry

    reg = open_registry(Path("."))
    try:
        kb_folder = get_kb_path(reg, name)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    kb_conn = open_kb(kb_folder / "knowledge.db")
    generated = build_taxonomy_data(kb_conn)
    kb_conn.close()

    taxonomy_path = kb_folder / "reference" / "taxonomy.yaml"
    existing: dict = {}
    if taxonomy_path.exists():
        try:
            loaded = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            pass

    merged = merge_taxonomy(existing, generated)

    with open(taxonomy_path, "w", encoding="utf-8") as fh:
        yaml.dump(merged, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    tag_count = sum(len(v) for v in merged.get("Tags", {}).values())
    kw_count = len(merged.get("Keywords", []))
    typer.echo(
        f"Taxonomy written: {tag_count} tags, {kw_count} keywords  →  {taxonomy_path}"
    )
