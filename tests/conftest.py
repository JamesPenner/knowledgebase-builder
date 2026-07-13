import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from src.db.corpus import open_corpus
from src.db.kb import open_kb
from src.pipeline.progress import NullProgressReporter


@pytest.fixture(autouse=True)
def _reset_stage_progress_state():
    """`_progress` is keyed by (kb, stage) and is process-global (KB.AN1's
    reentrancy guard reads it before allowing a new /run). Without resetting
    it, a stubbed stage runner in one test that never calls progress.done()
    leaves a stale 'running' entry that spuriously 409s an unrelated test
    reusing the same kb/stage name.
    """
    import src.pipeline.progress as _progress_mod
    _progress_mod._progress.clear()
    yield
    _progress_mod._progress.clear()


@pytest.fixture
def corpus_db(tmp_path):
    return open_corpus(tmp_path / "corpus.db")


@pytest.fixture
def kb_db(tmp_path):
    return open_kb(tmp_path / "knowledge.db")


@pytest.fixture
def dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    return open_corpus(corpus_path), open_kb(kb_path), corpus_path, kb_path


@pytest.fixture
def null_progress():
    return NullProgressReporter()


@pytest.fixture
def no_cancel():
    return threading.Event()


@pytest.fixture
def sample_image(tmp_path):
    from PIL import Image
    path = tmp_path / "test_image.jpg"
    Image.new("RGB", (64, 64), color=(128, 64, 32)).save(path, "JPEG")
    return path


@pytest.fixture
def sample_images(tmp_path):
    from PIL import Image
    paths = []
    for i in range(5):
        p = tmp_path / f"img_{i:03d}.jpg"
        Image.new("RGB", (64, 64), color=(i * 40, 100, 200)).save(p, "JPEG")
        paths.append(p)
    return paths


@pytest.fixture
def sample_video(tmp_path):
    ffmpeg = shutil.which("ffmpeg") or (
        "tools/ffmpeg/ffmpeg.exe" if Path("tools/ffmpeg/ffmpeg.exe").exists() else None
    )
    if ffmpeg is None:
        pytest.skip("ffmpeg not found")

    path = tmp_path / "test_video.mp4"
    result = subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg failed to generate test video")
    return path
