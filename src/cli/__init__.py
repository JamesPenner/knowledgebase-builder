import typer
import uvicorn

from src.cli.aesthetic import app as _aesthetic
from src.cli.face import app as _face
from src.cli.geolocate import app as _geolocate
from src.cli.quality import app as _quality
from src.cli.voice import app as _voice
from src.cli.corpus import app as _corpus
from src.cli.entity import app as _entity
from src.cli.kb import app as _kb
from src.cli.pipeline import app as _pipeline
from src.cli.quick import app as _quick
from src.cli.review import app as _review
from src.cli.source import app as _source

app = typer.Typer(
    name="enrich",
    help="KB Builder — domain knowledge extraction pipeline.",
    no_args_is_help=True,
)

app.add_typer(_pipeline, name="pipeline")
app.add_typer(_kb,       name="kb")
app.add_typer(_review,   name="review")
app.add_typer(_aesthetic,  name="aesthetic")
app.add_typer(_face,       name="face")
app.add_typer(_geolocate,  name="geolocate")
app.add_typer(_voice,      name="voice")
app.add_typer(_quality,   name="quality")
app.add_typer(_quick,    name="quick")
app.add_typer(_source,   name="source")
app.add_typer(_entity,   name="entity")
app.add_typer(_corpus,   name="corpus")


@app.command("serve")
def serve(
    host: str = typer.Option("", "--host", help="Override host from config.yaml"),
    port: int = typer.Option(0,  "--port", help="Override port from config.yaml"),
) -> None:
    """Start the KB Builder web server."""
    from pathlib import Path
    from src.config import load_config

    cfg = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)
    bind_host = host or cfg.host
    bind_port = port or cfg.port
    typer.echo(f"KB Builder running at http://{bind_host}:{bind_port}")
    uvicorn.run("src.api:app", host=bind_host, port=bind_port, reload=False)
