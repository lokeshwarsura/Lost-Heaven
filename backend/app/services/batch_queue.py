import json
import os
import threading
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlmodel import Session

from app.config import settings
from app.database import engine
from app.models import Project

logger = logging.getLogger(__name__)

_queue_lock = threading.RLock()
_worker_running = False

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def _queue_file() -> Path:
    return Path(settings.STORAGE_DIR) / "batch_queue.json"


def _load_queue() -> List[Dict[str, Any]]:
    qf = _queue_file()
    if not qf.exists():
        return []
    try:
        data = json.loads(qf.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_queue(items: List[Dict[str, Any]]) -> None:
    qf = _queue_file()
    qf.parent.mkdir(parents=True, exist_ok=True)
    qf.write_text(json.dumps(items, indent=2), encoding="utf-8")


def list_batch_queue() -> List[Dict[str, Any]]:
    with _queue_lock:
        return _load_queue()


def enqueue_batch_project(project_id: int, title: str, source: str = "upload", auto_start: bool = True) -> Dict[str, Any]:
    item = {
        "project_id": int(project_id),
        "title": title,
        "source": source,
        "status": "queued",
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    with _queue_lock:
        items = _load_queue()
        items = [x for x in items if int(x.get("project_id", -1)) != int(project_id)]
        items.append(item)
        _save_queue(items)

    if auto_start:
        start_batch_worker()
    return item


def scan_watch_folder(
    folder_path: str,
    *,
    aspect_ratio: str = "9:16",
    duration_preset: str = "Auto",
    clip_count: int = 5,
    topic_focus: Optional[str] = None,
    caption_language: str = "As spoken",
    hardware_mode: str = "gpu",
    auto_start: bool = True,
) -> Dict[str, Any]:
    """
    Scan a local directory for new `.mp4`/`.mov`/`.mkv`/`.webm` files not yet imported,
    create Project rows, and enqueue them into the sequential batch worker.
    """
    root = Path(folder_path)
    if not folder_path or not root.exists() or not root.is_dir():
        return {"scanned": 0, "enqueued": [], "error": "Watch folder does not exist or is not a directory"}

    discovered: List[Dict[str, Any]] = []
    with _queue_lock:
        existing_paths = {x.get("source_path") for x in _load_queue() if x.get("source_path")}

    with Session(engine) as session:
        all_projs = session.query(Project).all() if hasattr(session, "query") else []
        if not all_projs:
            from sqlmodel import select
            all_projs = list(session.exec(select(Project)).all())
        known_sources = {os.path.abspath(p.source_path) for p in all_projs if p.source_path} | existing_paths

        for f in sorted(root.iterdir()):
            if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
                continue
            abs_p = str(f.resolve())
            if abs_p in known_sources:
                continue

            proj = Project(
                title=f.stem,
                source_path=abs_p,
                status="created",
                progress=0,
                current_stage="created",
                status_message="Queued from Watch Folder",
                default_aspect_ratio=aspect_ratio,
                duration_preset=duration_preset,
                clip_count=clip_count,
                topic_focus=topic_focus,
                caption_language=caption_language,
                hardware_mode=hardware_mode,
            )
            session.add(proj)
            session.commit()
            session.refresh(proj)

            item = {
                "project_id": proj.id,
                "title": proj.title,
                "source": "watch_folder",
                "source_path": abs_p,
                "status": "queued",
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
            }
            with _queue_lock:
                q = _load_queue()
                q.append(item)
                _save_queue(q)
            discovered.append(item)
            known_sources.add(abs_p)

    if discovered and auto_start:
        start_batch_worker()

    return {"scanned": len(discovered), "enqueued": discovered}


def start_batch_worker() -> None:
    global _worker_running
    with _queue_lock:
        if _worker_running:
            return
        _worker_running = True

    def _run():
        global _worker_running
        from app.services.job_manager import job_manager
        try:
            while True:
                next_item: Optional[Dict[str, Any]] = None
                with _queue_lock:
                    q = _load_queue()
                    for item in q:
                        if item.get("status") == "queued":
                            item["status"] = "processing"
                            next_item = dict(item)
                            break
                    _save_queue(q)

                if not next_item:
                    break

                pid = int(next_item["project_id"])
                try:
                    job_manager.run_pipeline(pid)
                    with _queue_lock:
                        q = _load_queue()
                        for item in q:
                            if int(item.get("project_id", -1)) == pid:
                                item["status"] = "completed"
                        _save_queue(q)
                except Exception as exc:
                    logger.error(f"Batch queue job {pid} failed: {exc}")
                    with _queue_lock:
                        q = _load_queue()
                        for item in q:
                            if int(item.get("project_id", -1)) == pid:
                                item["status"] = "error"
                                item["error"] = str(exc)
                        _save_queue(q)
                time.sleep(0.2)
        finally:
            with _queue_lock:
                _worker_running = False

    t = threading.Thread(target=_run, daemon=True, name="lost-heaven-batch-worker")
    t.start()
