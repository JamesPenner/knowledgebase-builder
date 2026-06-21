import logging
from pathlib import Path

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(help="Geolocation — resolve GPS to place hierarchies", invoke_without_command=True)

_NE_ADMIN0_URL = (
    "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip"
)
_NE_ADMIN1_URL = (
    "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip"
)
_NE_FILES = ["ne_10m_admin_0_countries", "ne_10m_admin_1_states_provinces"]


def _resolve_kb(name: str | None) -> tuple:
    from src.db.registry import get_active_kb_path, get_kb_path, open_registry

    reg = open_registry(Path("."))
    try:
        folder = get_kb_path(reg, name) if name else get_active_kb_path(reg)
        if folder is None:
            typer.echo("Error: no active KB. Use --kb <name> or run 'enrich kb create'.", err=True)
            raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    return folder / "corpus.db", folder / "knowledge.db"


@app.callback(invoke_without_command=True)
def geolocate(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command("download")
def download(
    force: bool = typer.Option(False, "--force", help="Re-download even if files already exist"),
) -> None:
    """Download Natural Earth shapefiles to reference/geo/natural_earth/."""
    from src.db.registry import get_active_kb_path, open_registry

    reg = open_registry(Path("."))
    folder = get_active_kb_path(reg)
    if folder is None:
        typer.echo("Error: no active KB.", err=True)
        raise typer.Exit(1)

    ne_dir = folder / "reference" / "geo" / "natural_earth"
    ne_dir.mkdir(parents=True, exist_ok=True)

    for stem, url in zip(_NE_FILES, [_NE_ADMIN0_URL, _NE_ADMIN1_URL]):
        shp_path = ne_dir / f"{stem}.shp"
        if shp_path.exists() and not force:
            typer.echo(f"Already present: {shp_path.name} (use --force to re-download)")
            continue
        _download_and_extract(url, stem, ne_dir)

    typer.echo("Natural Earth data ready.")


def _download_and_extract(url: str, stem: str, dest_dir: Path) -> None:
    import io
    import zipfile

    import requests  # lazy import kept here intentionally

    typer.echo(f"Downloading {stem}…")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for member in zf.namelist():
            member_stem = Path(member).stem
            if member_stem == stem and Path(member).suffix.lower() in (".shp", ".dbf", ".shx", ".prj"):
                target = dest_dir / Path(member).name
                target.write_bytes(zf.read(member))

    typer.echo(f"  saved to {dest_dir / stem}.shp")


@app.command("run")
def run(
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    force: bool = typer.Option(False, "--force", help="Re-resolve already-geolocated files"),
) -> None:
    """Resolve GPS coordinates to place hierarchies using offline shapefiles."""
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.geolocate import run_geolocate

    corpus_path, kb_path = _resolve_kb(kb)
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if force:
        from src.db.corpus import open_corpus
        conn = open_corpus(corpus_path)
        conn.execute("DELETE FROM file_geolabels")
        conn.commit()
        conn.close()
        typer.echo("Cleared existing geolabels.")

    typer.echo("Running geolocate…")
    run_geolocate(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Geolocate complete.")


@app.command("cluster")
def cluster(
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    eps_km: float = typer.Option(0.0, "--eps-km", help="Cluster radius in km (0 = use config default)"),
    min_samples: int = typer.Option(0, "--min-samples", help="Minimum files per cluster (0 = use config default)"),
    export: bool = typer.Option(False, "--export", help="Write export/gps_clusters.csv"),
) -> None:
    """Group corpus files by GPS proximity into named clusters using DBSCAN."""
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.gps_cluster import run_gps_cluster

    corpus_path, kb_path = _resolve_kb(kb)
    kb_folder = kb_path.parent
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if eps_km > 0:
        from dataclasses import replace
        config = replace(config, gps_cluster_eps_km=eps_km)
    if min_samples > 0:
        from dataclasses import replace
        config = replace(config, gps_cluster_min_samples=min_samples)

    typer.echo(
        f"Clustering with eps={config.gps_cluster_eps_km} km,"
        f" min_samples={config.gps_cluster_min_samples}…"
    )
    result = run_gps_cluster(corpus_path, kb_folder, config, NullProgressReporter(), make_cancel_event(), export=export)
    typer.echo(
        f"{result['clusters']} clusters, {result['assigned']} assigned, {result['noise']} noise"
    )
    if export:
        typer.echo(f"Report written to {kb_folder / 'export' / 'gps_clusters.csv'}")


@app.command("seed-clusters")
def seed_clusters(
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
) -> None:
    """Seed GPS cluster labels into the entity_gps_cluster_locations table in knowledge.db."""
    from src.db.corpus import get_gps_clusters, open_corpus
    from src.db.kb import create_entity_table, open_kb, register_entity_table, upsert_entity_row

    corpus_path, kb_path = _resolve_kb(kb)

    corpus_conn = open_corpus(corpus_path)
    clusters = get_gps_clusters(corpus_conn)
    corpus_conn.close()

    if not clusters:
        typer.echo("No clusters found — run 'enrich geolocate cluster' first.", err=True)
        raise typer.Exit(1)

    kb_conn = open_kb(kb_path)
    columns = ["location", "latitude", "longitude", "threshold_m", "file_count"]
    create_entity_table(kb_conn, "gps_cluster_locations", columns, "location")
    register_entity_table(
        kb_conn,
        table_name="gps_cluster_locations",
        display_name="GPS Cluster Locations",
        trigger_word="",
        trigger_aliases_json="[]",
        key_column="location",
        match_type="gps",
        source_csv="gps_clusters",
    )

    for row in clusters:
        upsert_entity_row(kb_conn, "gps_cluster_locations", {
            "location": row["label"],
            "latitude": str(row["centroid_lat"]),
            "longitude": str(row["centroid_lon"]),
            "threshold_m": str(row["eps_km"] * 1000),
            "file_count": str(row["file_count"]),
        })

    kb_conn.commit()
    kb_conn.close()
    typer.echo(f"Seeded {len(clusters)} cluster(s) into entity_gps_cluster_locations.")
