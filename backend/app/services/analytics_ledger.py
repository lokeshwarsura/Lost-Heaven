"""
Lost Heaven — Local Export & Style Intelligence Ledger (Phase 3).
Tracks exported/rendered style combinations in SQLite (`export_events`), imports YouTube
Studio Analytics CSVs to correlate high retention/views, summarizes "Your Top Performing Formula",
and gently boosts matching scoring priorities in clip extraction.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from app.models import ExportEvent, ViralClip

logger = logging.getLogger(__name__)


def _duration_band_key(duration_sec: float) -> str:
    d = float(duration_sec or 30.0)
    if d < 25.0:
        return "15-25s"
    if d <= 40.0:
        return "25-40s"
    return "40-60s"


def _duration_band_bounds(band: str) -> Tuple[float, float]:
    if band == "15-25s":
        return 12.0, 25.0
    if band == "25-40s":
        return 25.0, 40.0
    if band == "40-60s":
        return 40.0, 65.0
    return 20.0, 55.0


def record_export_event(
    session: Session,
    clip: Optional[ViralClip] = None,
    *,
    event_type: str = "mp4_export",
    views: int = 0,
    retention_pct: float = 0.0,
    ctr_pct: float = 0.0,
    title: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    caption_style: Optional[str] = None,
    duration_sec: Optional[float] = None,
) -> Optional[ExportEvent]:
    """Persist an export/render/analytics telemetry row into SQLite `export_events`."""
    try:
        ev = ExportEvent(
            project_id=getattr(clip, "project_id", None) if clip else None,
            clip_id=getattr(clip, "id", None) if clip else None,
            event_type=str(event_type or "mp4_export"),
            aspect_ratio=str(aspect_ratio or (getattr(clip, "aspect_ratio", "9:16") if clip else "9:16") or "9:16"),
            caption_style=str(caption_style or (getattr(clip, "caption_style", "Karaoke pop") if clip else "Karaoke pop") or "Karaoke pop"),
            frame_layout=str(getattr(clip, "frame_layout", "auto_track") if clip else "auto_track"),
            duration_sec=float(duration_sec if duration_sec is not None else (getattr(clip, "duration", 30.0) if clip else 30.0)),
            hook_score=int(getattr(clip, "hook_score", 0) or 0) if clip else 0,
            has_hook_banner=bool(getattr(clip, "show_hook_banner", False)) if clip else False,
            beat_sync_enabled=bool(getattr(clip, "beat_sync_enabled", False)) if clip else False,
            bilingual_enabled=bool(getattr(clip, "bilingual_enabled", False)) if clip else False,
            title=str(title or (getattr(clip, "selected_title", "") or getattr(clip, "title", "") if clip else "") or "")[:180],
            views=max(0, int(views or 0)),
            retention_pct=max(0.0, float(retention_pct or 0.0)),
            ctr_pct=max(0.0, float(ctr_pct or 0.0)),
            created_at=datetime.now(timezone.utc),
        )
        session.add(ev)
        session.commit()
        session.refresh(ev)
        return ev
    except Exception as exc:
        logger.debug("Skipped export event telemetry: %s", exc)
        return None


def _pick_col(row: Dict[str, str], candidates: Tuple[str, ...]) -> Optional[str]:
    lowered = {str(k or "").strip().lower(): v for k, v in row.items()}
    for c in candidates:
        if c in lowered and str(lowered[c] or "").strip() != "":
            return str(lowered[c]).strip()
    for k, v in lowered.items():
        if any(c in k for c in candidates) and str(v or "").strip() != "":
            return str(v).strip()
    return None


def _parse_num(raw: Optional[str], default: float = 0.0) -> float:
    if raw is None:
        return default
    cleaned = str(raw).strip().replace(",", "").replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return default


def import_youtube_analytics_csv(session: Session, csv_text: str) -> Dict[str, Any]:
    """
    Parse a YouTube Studio CSV export and correlate performance metrics (`views`, `retention_pct`,
    `ctr_pct`) with exported clips / style combinations in `export_events`.
    """
    text = (csv_text or "").strip()
    if not text:
        raise ValueError("Uploaded CSV content is empty.")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV must include column headers (e.g. Title, Views, Average percentage viewed (%)).")

    existing_events = session.exec(select(ExportEvent)).all()
    existing_clips = session.exec(select(ViralClip)).all()

    imported = 0
    matched = 0
    for row in reader:
        if not row or all(not str(v or "").strip() for v in row.values()):
            continue
        title = _pick_col(row, ("video title", "title", "content", "clip")) or f"Imported Row {imported + 1}"
        views_val = int(round(_parse_num(_pick_col(row, ("views", "view count")), 0.0)))
        ret_val = _parse_num(
            _pick_col(row, ("average percentage viewed", "retention", "avg retention", "avg_percentage_viewed")),
            0.0,
        )
        ctr_val = _parse_num(
            _pick_col(row, ("impressions click-through rate", "ctr", "click-through rate")),
            0.0,
        )
        dur_val = _parse_num(_pick_col(row, ("duration", "length", "duration_sec")), 0.0)
        aspect_val = _pick_col(row, ("aspect_ratio", "aspect ratio", "format"))
        style_val = _pick_col(row, ("caption_style", "caption style", "style"))

        title_low = title.lower()
        # Match against prior exported event or clip title if present
        matched_ev = next(
            (ev for ev in existing_events if ev.title and (ev.title.lower() in title_low or title_low in ev.title.lower())),
            None,
        )
        matched_clip = next(
            (
                c for c in existing_clips
                if (c.selected_title and (c.selected_title.lower() in title_low or title_low in c.selected_title.lower()))
                or (c.hook_headline and c.hook_headline.lower() in title_low)
            ),
            None,
        )

        if matched_ev is not None:
            matched_ev.views = max(matched_ev.views, views_val)
            matched_ev.retention_pct = max(matched_ev.retention_pct, ret_val)
            matched_ev.ctr_pct = max(matched_ev.ctr_pct, ctr_val)
            if dur_val > 0:
                matched_ev.duration_sec = dur_val
            session.add(matched_ev)
            matched += 1
        else:
            ev = ExportEvent(
                project_id=matched_clip.project_id if matched_clip else None,
                clip_id=matched_clip.id if matched_clip else None,
                event_type="csv_import",
                aspect_ratio=str(aspect_val or (matched_clip.aspect_ratio if matched_clip else "9:16") or "9:16"),
                caption_style=str(style_val or (matched_clip.caption_style if matched_clip else "Karaoke pop") or "Karaoke pop"),
                frame_layout=str(matched_clip.frame_layout if matched_clip else "auto_track"),
                duration_sec=dur_val if dur_val > 0 else float(matched_clip.duration if matched_clip else 30.0),
                hook_score=int(matched_clip.hook_score or 0) if matched_clip else 85,
                has_hook_banner=bool(matched_clip.show_hook_banner) if matched_clip else ("hook" in title_low or True),
                beat_sync_enabled=bool(getattr(matched_clip, "beat_sync_enabled", False)) if matched_clip else False,
                bilingual_enabled=bool(getattr(matched_clip, "bilingual_enabled", False)) if matched_clip else False,
                title=title[:180],
                views=max(0, views_val),
                retention_pct=max(0.0, ret_val),
                ctr_pct=max(0.0, ctr_val),
                created_at=datetime.now(timezone.utc),
            )
            session.add(ev)
            if matched_clip is not None:
                matched += 1
        imported += 1

    session.commit()
    formula = compute_top_performing_formula(session)
    return {
        "status": "imported",
        "imported_rows": imported,
        "matched_clips": matched,
        "formula": formula,
    }


def compute_top_performing_formula(session: Session) -> Dict[str, Any]:
    """Summarize top-performing format, caption style, duration band, and hook signals."""
    events = session.exec(select(ExportEvent)).all()
    if not events:
        return {
            "has_data": False,
            "sample_count": 0,
            "headline": "Export clips or import YouTube Analytics CSV to discover your winning Shorts formula.",
            "preferred_aspect_ratio": "9:16",
            "preferred_caption_style": "Karaoke pop",
            "preferred_frame_layout": "auto_track",
            "preferred_duration_band": "25-40s",
            "preferred_duration_bounds": [25.0, 40.0],
            "hook_banner_recommended": True,
            "beat_sync_recommended": False,
            "avg_views": 0,
            "avg_retention_pct": 0.0,
            "prompt_guidance": "",
        }

    aspect_scores: Dict[str, float] = defaultdict(float)
    style_scores: Dict[str, float] = defaultdict(float)
    layout_scores: Dict[str, float] = defaultdict(float)
    band_scores: Dict[str, float] = defaultdict(float)

    hook_yes_weight = 0.0
    hook_no_weight = 0.0
    beat_yes_weight = 0.0
    beat_no_weight = 0.0

    total_views = 0
    ret_vals: List[float] = []

    for ev in events:
        if ev.views > 0 or ev.retention_pct > 0:
            w = float(max(1, ev.views)) * (0.5 + max(0.0, ev.retention_pct) / 60.0)
        else:
            w = 10.0 + (2.0 if ev.event_type == "publish_pack" else 0.0)

        aspect_scores[ev.aspect_ratio or "9:16"] += w
        style_scores[ev.caption_style or "Karaoke pop"] += w
        layout_scores[ev.frame_layout or "auto_track"] += w
        band_scores[_duration_band_key(ev.duration_sec)] += w

        if ev.has_hook_banner:
            hook_yes_weight += w
        else:
            hook_no_weight += w

        if ev.beat_sync_enabled:
            beat_yes_weight += w
        else:
            beat_no_weight += w

        total_views += max(0, int(ev.views or 0))
        if ev.retention_pct > 0:
            ret_vals.append(float(ev.retention_pct))

    top_aspect = max(aspect_scores.items(), key=lambda x: x[1])[0]
    top_style = max(style_scores.items(), key=lambda x: x[1])[0]
    top_layout = max(layout_scores.items(), key=lambda x: x[1])[0]
    top_band = max(band_scores.items(), key=lambda x: x[1])[0]
    min_b, max_b = _duration_band_bounds(top_band)

    hook_rec = hook_yes_weight >= hook_no_weight * 0.85
    beat_rec = beat_yes_weight > beat_no_weight
    avg_views = int(round(total_views / max(1, sum(1 for e in events if e.views > 0)))) if any(e.views > 0 for e in events) else 0
    avg_ret = round(sum(ret_vals) / len(ret_vals), 1) if ret_vals else 0.0

    parts = [top_aspect, top_style, top_band]
    if hook_rec:
        parts.append("Hook Banner")
    if beat_rec:
        parts.append("Beat-Sync")
    headline = " • ".join(parts)

    guidance = (
        f"HISTORICAL WINNER FORMULA ({len(events)} tracked exports): Favor clips sized for "
        f"{top_band} duration ({min_b:.0f}-{max_b:.0f}s), {top_aspect} framing, with strong opening "
        f"{'Hook Banner + ' if hook_rec else ''}first-3s curiosity hooks."
    )

    return {
        "has_data": True,
        "sample_count": len(events),
        "headline": headline,
        "preferred_aspect_ratio": top_aspect,
        "preferred_caption_style": top_style,
        "preferred_frame_layout": top_layout,
        "preferred_duration_band": top_band,
        "preferred_duration_bounds": [min_b, max_b],
        "hook_banner_recommended": bool(hook_rec),
        "beat_sync_recommended": bool(beat_rec),
        "avg_views": avg_views,
        "avg_retention_pct": avg_ret,
        "prompt_guidance": guidance,
    }


def apply_formula_boost_to_score(
    base_score: int,
    clip_duration: float,
    hook_score: int,
    formula: Optional[Dict[str, Any]],
) -> int:
    """Apply gentle analytics-driven virality boost (+0..+8 pts) to candidates matching top-performing formula."""
    if not formula or not formula.get("has_data"):
        return int(base_score)
    bounds = formula.get("preferred_duration_bounds") or [25.0, 40.0]
    min_b, max_b = float(bounds[0]), float(bounds[1])
    boost = 0.0
    if min_b <= float(clip_duration) <= max_b:
        boost += 5.0
    if formula.get("hook_banner_recommended") and hook_score >= 78:
        boost += 3.0
    return int(min(99, max(1, round(base_score + boost))))
