import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

render_lock = threading.RLock()
active_renders: Dict[str, subprocess.Popen] = {}
active_render_tokens: Dict[str, str] = {}
active_temp_dirs: Dict[str, Path] = {}
inflight_entries: Dict[str, "InFlightRenderEntry"] = {}


class RenderSupersededError(Exception):
    """Raised when an in-flight render is terminated by a newer render request for the same clip."""


@dataclass
class InFlightRenderEntry:
    clip_key: str
    token: str
    options_hash: str
    event: threading.Event = field(default_factory=threading.Event)
    result_path: Optional[str] = None
    error: Optional[BaseException] = None
    cancelled: bool = False


def _normalize_signature_value(val: Any) -> Any:
    if isinstance(val, float):
        return round(val, 4)
    if isinstance(val, Path):
        return str(val)
    if isinstance(val, dict):
        return {
            str(k): _normalize_signature_value(v)
            for k, v in sorted(val.items(), key=lambda kv: str(kv[0]))
            if v is not None
        }
    if isinstance(val, (list, tuple)):
        return [_normalize_signature_value(v) for v in val]
    return val


def compute_render_signature(
    clip_id: Any,
    project_id: Any = None,
    start_time: float = 0.0,
    duration: float = 0.0,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Compute a deterministic hash for `(clip_id, start, end, effective render options)` so
    identical in-flight renders attach to the existing job instead of cancelling each other.
    """
    excluded_keys = {
        "progress_callback",
        "output_path_override",
        "allow_concurrent",
        "_clip_key",
        "_my_token",
    }
    clean_opts: Dict[str, Any] = {}
    for k, v in (params or {}).items():
        if k in excluded_keys:
            continue
        if k == "options":
            clean_opts[k] = _normalize_signature_value(v or {})
        else:
            clean_opts[k] = _normalize_signature_value(v)

    s_t = round(float(start_time or 0.0), 3)
    e_t = round(s_t + max(0.0, float(duration or 0.0)), 3)
    payload = {
        "project_id": project_id,
        "clip_id": int(clip_id),
        "start_time": s_t,
        "end_time": e_t,
        "options": clean_opts,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _terminate_proc_and_cleanup(proc: Optional[subprocess.Popen], old_temp: Optional[Path], clip_key: str) -> None:
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    proc.kill()
                    try:
                        proc.wait(timeout=0.5)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Ignored error terminating superseded FFmpeg for {clip_key}: {e}")

    if old_temp is not None and old_temp.exists():
        shutil.rmtree(old_temp, ignore_errors=True)


def cancel_active_render(clip_key: str) -> bool:
    """Immediately terminate any active FFmpeg process for `clip_key` and clean its temp dir."""
    woke_entry: Optional[InFlightRenderEntry] = None
    with render_lock:
        proc = active_renders.pop(clip_key, None)
        old_temp = active_temp_dirs.pop(clip_key, None)
        existing = inflight_entries.pop(clip_key, None)
        had_active = proc is not None
        if existing is not None:
            existing.cancelled = True
            if existing.error is None:
                existing.error = RenderSupersededError(f"Render for {clip_key} was cancelled.")
            woke_entry = existing

    if woke_entry is not None:
        woke_entry.event.set()
    _terminate_proc_and_cleanup(proc, old_temp, clip_key)
    return had_active


def acquire_or_attach_render(
    clip_key: str,
    my_token: str,
    options_hash: str,
) -> Tuple[str, InFlightRenderEntry]:
    """
    Either attach to an identical active render for `clip_key` (`("attach", existing_entry)`)
    or supersede any mismatched active render and register this worker (`("run", new_entry)`).
    """
    proc_to_kill: Optional[subprocess.Popen] = None
    temp_to_clean: Optional[Path] = None
    superseded_entry: Optional[InFlightRenderEntry] = None

    with render_lock:
        existing = inflight_entries.get(clip_key)
        if existing is not None and not existing.cancelled and existing.options_hash == options_hash:
            return "attach", existing

        if existing is not None:
            existing.cancelled = True
            if existing.error is None:
                existing.error = RenderSupersededError(
                    f"Render for {clip_key} was superseded by a newer request."
                )
            superseded_entry = existing

        proc_to_kill = active_renders.pop(clip_key, None)
        temp_to_clean = active_temp_dirs.pop(clip_key, None)
        active_render_tokens[clip_key] = my_token
        new_entry = InFlightRenderEntry(
            clip_key=clip_key,
            token=my_token,
            options_hash=options_hash,
        )
        inflight_entries[clip_key] = new_entry

    if superseded_entry is not None:
        superseded_entry.event.set()
    _terminate_proc_and_cleanup(proc_to_kill, temp_to_clean, clip_key)
    return "run", new_entry


def await_attached_render(entry: InFlightRenderEntry, timeout: float = 1800.0) -> str:
    """Wait for an attached in-flight render to finish and return its output file path."""
    completed = entry.event.wait(timeout=timeout)
    if entry.error is not None:
        raise entry.error
    if entry.cancelled:
        raise RenderSupersededError(f"Render for {entry.clip_key} was superseded by a newer request.")
    if not completed or not entry.result_path or not os.path.exists(entry.result_path):
        raise RenderSupersededError(f"Attached render for {entry.clip_key} did not finish.")
    return entry.result_path


def finish_render_entry(
    entry: InFlightRenderEntry,
    result_path: Optional[str] = None,
    error: Optional[BaseException] = None,
) -> None:
    """Mark an in-flight render entry complete or failed and wake all attached waiters."""
    with render_lock:
        if result_path is not None:
            entry.result_path = result_path
        if error is not None:
            entry.error = error
        if inflight_entries.get(entry.clip_key) is entry:
            inflight_entries.pop(entry.clip_key, None)
        entry.event.set()
