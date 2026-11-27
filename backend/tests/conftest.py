"""
Pytest session isolation for Lost Heaven Studio.

Redirects SQLite DATABASE_URL / engine and STORAGE_DIR to temporary pytest
directories (`tmp_path_factory`) before application modules load so test suites
NEVER insert synthetic projects or files into the user's real clipship.db.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from sqlmodel import create_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_BOOTSTRAP_STORAGE_DIR = Path(tempfile.mkdtemp(prefix="lost_heaven_pytest_env_"))
for _sub in (
    "masters",
    "proxies",
    "renders",
    "captions",
    "temp",
    "projects",
    "assets",
    "previews",
    "thumbnails",
    "luts",
    "fonts",
    "models",
):
    (_BOOTSTRAP_STORAGE_DIR / _sub).mkdir(parents=True, exist_ok=True)

_BOOTSTRAP_DB_PATH = _BOOTSTRAP_STORAGE_DIR / "lost_heaven_pytest.db"

# Must be set BEFORE app.config / app.database / app.main are imported
os.environ["LOST_HEAVEN_DATA_DIR"] = str(_BOOTSTRAP_STORAGE_DIR)
os.environ["CLIPSHIP_DATA_DIR"] = str(_BOOTSTRAP_STORAGE_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///{_BOOTSTRAP_DB_PATH}"


def _cleanup_bootstrap_storage() -> None:
    shutil.rmtree(_BOOTSTRAP_STORAGE_DIR, ignore_errors=True)


atexit.register(_cleanup_bootstrap_storage)


def _link_cached_whisper_models(dst_models_dir: Path) -> None:
    """
    Expose pre-downloaded local faster-whisper weights inside isolated test storage
    via zero-copy NTFS hardlinks (or symlinks) so offline tests never need HuggingFace network.
    """
    for candidate in (_REPO_ROOT / "storage" / "models", _BACKEND_DIR / "storage" / "models"):
        if not (candidate / "model.bin").exists():
            continue
        for item in candidate.iterdir():
            if not item.is_file():
                continue
            target = dst_models_dir / item.name
            if target.exists():
                continue
            try:
                os.link(str(item), str(target))
            except OSError:
                try:
                    shutil.copy2(str(item), str(target))
                except OSError:
                    pass
        break


@pytest.fixture(scope="session", autouse=True)
def _isolate_pytest_storage_and_db(tmp_path_factory: pytest.TempPathFactory):
    """Redirect settings.STORAGE_DIR, SQLite engine, and JobManager engine into tmp_path_factory."""
    import app.database as app_db
    import app.main as main_mod
    import app.services.job_manager as jm_mod
    from app.config import settings

    old_engine = app_db.engine

    session_storage = tmp_path_factory.mktemp("lost_heaven_storage")
    for sub in (
        "masters",
        "proxies",
        "renders",
        "captions",
        "temp",
        "projects",
        "assets",
        "previews",
        "thumbnails",
        "luts",
        "fonts",
        "models",
    ):
        (session_storage / sub).mkdir(parents=True, exist_ok=True)

    _link_cached_whisper_models(session_storage / "models")

    session_db = session_storage / "lost_heaven_suite.db"
    sqlite_url = f"sqlite:///{session_db}"
    iso_engine = create_engine(sqlite_url, echo=False, connect_args={"check_same_thread": False})

    os.environ["LOST_HEAVEN_DATA_DIR"] = str(session_storage)
    os.environ["CLIPSHIP_DATA_DIR"] = str(session_storage)
    os.environ["DATABASE_URL"] = sqlite_url

    settings.STORAGE_DIR = str(session_storage)
    app_db.SQLITE_DB_PATH = session_db
    app_db.SQLITE_URL = sqlite_url
    app_db.engine = iso_engine
    jm_mod.engine = iso_engine
    jm_mod._DEFAULT_ENGINE = iso_engine

    # Propagate isolated engine to any loaded app/test modules that did `from app.database import engine`
    for mod_name, mod in list(sys.modules.items()):
        if mod is not None and (
            mod_name.startswith("app.")
            or mod_name.startswith("tests.")
            or mod_name.startswith("backend.tests.")
            or mod_name.startswith("test_")
        ):
            if getattr(mod, "engine", None) is old_engine:
                setattr(mod, "engine", iso_engine)
    jm_mod._DEFAULT_ENGINE = iso_engine

    try:
        old_engine.dispose()
    except Exception:
        pass

    # Point module-level main.py paths inside isolated storage as well
    main_mod.storage_path = session_storage
    main_mod.storage_renders = session_storage / "renders"
    main_mod.storage_masters = session_storage / "masters"
    main_mod.storage_previews = session_storage / "previews"
    main_mod.storage_assets = session_storage / "assets"
    main_mod.storage_fonts = session_storage / "fonts"
    main_mod.storage_models = session_storage / "models"
    main_mod.storage_projects = session_storage / "projects"
    main_mod.storage_thumbnails = session_storage / "thumbnails"
    main_mod.storage_luts = session_storage / "luts"

    app_db.init_db()
    try:
        yield session_storage
    finally:
        try:
            iso_engine.dispose()
        except Exception:
            pass
