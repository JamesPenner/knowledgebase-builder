import json
import shutil
import subprocess
from pathlib import Path


class ExifTool:
    """Persistent ExifTool subprocess using -stay_open mode for batch extraction."""

    def __init__(self, executable: str = "exiftool", config_path: str | None = None) -> None:
        resolved = shutil.which(executable)
        if resolved is None:
            p = Path(executable)
            if p.exists():
                resolved = str(p)
        if resolved is None:
            raise RuntimeError(
                f"ExifTool not found: '{executable}'. "
                "Set tools.exiftool in config.yaml to the path of exiftool.exe, "
                "or ensure it is on the system PATH."
            )
        self._executable = resolved
        self._config_path = config_path
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "ExifTool":
        cmd = [self._executable]
        if self._config_path and Path(self._config_path).exists():
            cmd += ["-config", self._config_path]
        cmd += ["-stay_open", "True", "-@", "-"]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *_) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.write(b"-stay_open\nFalse\n-execute\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            finally:
                self._proc = None

    def get_metadata(self, paths: list[Path]) -> list[dict]:
        """Extract metadata for a batch of files. Returns a list of dicts (one per file)."""
        if not paths or self._proc is None:
            return []

        cmd = "-j\n-G\n" + "\n".join(str(p) for p in paths) + "\n-execute\n"
        self._proc.stdin.write(cmd.encode())
        self._proc.stdin.flush()

        output_lines: list[bytes] = []
        while True:
            line = self._proc.stdout.readline()
            if not line:
                break
            if line.strip() == b"{ready}":
                break
            output_lines.append(line)

        raw = b"".join(output_lines).strip()
        if not raw:
            return []
        try:
            result = json.loads(raw)
            return result if isinstance(result, list) else [result]
        except json.JSONDecodeError:
            return []

    def write_metadata(self, path: Path, tags: list[tuple[str, str]]) -> bool:
        """Write metadata tags to a single file. Returns True on success.

        tags is a list of (field_name, value) tuples. List fields may appear
        multiple times with the same field_name for multiple values.
        """
        if not tags or self._proc is None:
            return False

        lines = ["-overwrite_original"]
        for field, value in tags:
            lines.append(f"-{field}={value}")
        lines.append(str(path))
        lines.append("-execute")
        cmd = "\n".join(lines) + "\n"

        self._proc.stdin.write(cmd.encode())
        self._proc.stdin.flush()

        output_lines: list[bytes] = []
        while True:
            line = self._proc.stdout.readline()
            if not line:
                break
            if line.strip() == b"{ready}":
                break
            output_lines.append(line)

        output = b"".join(output_lines).decode(errors="replace").lower()
        return "error" not in output
