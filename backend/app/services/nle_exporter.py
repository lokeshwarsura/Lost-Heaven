import io
import json
import os
import zipfile
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models import Project, ViralClip
from app.services.profanity_filter import mask_words_profanity
from app.services.reframer import resolve_target_dimensions
from app.services.timeline_editor import compute_kept_segments_and_remapped_words


def _fmt_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(float(seconds) * 1000.0)))
    hrs = ms_total // 3600000
    mins = (ms_total % 3600000) // 60000
    secs = (ms_total % 60000) // 1000
    ms = ms_total % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def build_srt_content(
    words: List[Dict[str, Any]],
    max_words_per_cue: int = 5,
    bilingual: bool = False,
) -> str:
    """Group remapped spoken words into readable SubRip (.srt) cues (optional dual-track bilingual lines)."""
    clean_words = [
        w for w in (words or [])
        if isinstance(w, dict) and not w.get("deleted") and not w.get("cut_from_video") and str(w.get("word", "")).strip()
    ]
    if not clean_words:
        return "1\n00:00:00,000 --> 00:00:02,000\n[No spoken captions]\n"

    cues: List[str] = []
    idx = 1
    for i in range(0, len(clean_words), max_words_per_cue):
        chunk = clean_words[i:i + max_words_per_cue]
        t0 = float(chunk[0].get("start", 0.0))
        t1 = max(t0 + 0.35, float(chunk[-1].get("end", t0 + 0.5)))
        txt = " ".join(str(w.get("word", "")).strip() for w in chunk)
        sec_txt = ""
        if bilingual:
            for cw in chunk:
                ph = str(cw.get("translated_phrase") or "").strip()
                if ph:
                    sec_txt = ph
                    break
            if not sec_txt:
                sec_txt = " ".join(
                    str(cw.get("translated_text") or "").strip()
                    for cw in chunk
                    if str(cw.get("translated_text") or "").strip()
                ).strip()
        body = f"{txt}\n{sec_txt}" if (bilingual and sec_txt) else txt
        cues.append(f"{idx}\n{_fmt_srt_time(t0)} --> {_fmt_srt_time(t1)}\n{body}\n")
        idx += 1
    return "\n".join(cues)


def build_fcpxml(
    clip: ViralClip,
    project: Project,
    words: List[Dict[str, Any]],
    effective_duration: float,
    media_path: str,
) -> str:
    """Generate Final Cut Pro / DaVinci Resolve FCPXML v1.10 project."""
    tw, th = resolve_target_dimensions(clip.aspect_ratio, clip.custom_width, clip.custom_height)
    fps = 30
    dur_frames = max(1, int(round(effective_duration * fps)))
    dur_rational = f"{dur_frames}/{fps}s"
    media_uri = Path(os.path.abspath(media_path)).as_uri() if media_path else "file:///clip.mp4"
    title_safe = escape(clip.selected_title or clip.hook_headline or f"Clip_{clip.id}")
    event_safe = escape(project.title or "Lost Heaven Project")

    markers = []
    for i in range(0, len(words), 5):
        chunk = words[i:i + 5]
        if not chunk:
            continue
        t_s = float(chunk[0].get("start", 0.0))
        t_f = max(0, int(round(t_s * fps)))
        txt = escape(" ".join(str(w.get("word", "")).strip() for w in chunk))
        markers.append(f'            <marker start="{t_f}/{fps}s" duration="1/{fps}s" value="{txt}"/>')
    markers_xml = "\n".join(markers)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat{tw}x{th}p{fps}" frameDuration="1/{fps}s" width="{tw}" height="{th}" colorSpace="1-1-1 (Rec. 709)"/>
    <asset id="r2" name="{title_safe}" uid="LH-CLIP-{clip.id}" start="0s" duration="{dur_rational}" hasVideo="1" hasAudio="1" format="r1">
      <media-rep kind="original-media" src="{escape(media_uri)}"/>
    </asset>
  </resources>
  <library>
    <event name="{event_safe}">
      <project name="{title_safe}">
        <sequence format="r1" duration="{dur_rational}" tcStart="0s" tcFormat="NDF">
          <spine>
            <asset-clip ref="r2" offset="0s" name="{title_safe}" start="0s" duration="{dur_rational}" format="r1">
{markers_xml}
            </asset-clip>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def build_premiere_xmeml(
    clip: ViralClip,
    project: Project,
    effective_duration: float,
    media_path: str,
) -> str:
    """Generate Adobe Premiere Pro XMEML v5 sequence XML."""
    tw, th = resolve_target_dimensions(clip.aspect_ratio, clip.custom_width, clip.custom_height)
    fps = 30
    dur_frames = max(1, int(round(effective_duration * fps)))
    in_frame = max(0, int(round(float(clip.start_time) * fps)))
    out_frame = in_frame + dur_frames
    media_uri = Path(os.path.abspath(media_path)).as_uri() if media_path else "file:///clip.mp4"
    title_safe = escape(clip.selected_title or clip.hook_headline or f"Clip_{clip.id}")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="lh-seq-{clip.id}">
    <name>{title_safe}</name>
    <duration>{dur_frames}</duration>
    <rate>
      <timebase>{fps}</timebase>
      <ntsc>FALSE</ntsc>
    </rate>
    <media>
      <video>
        <format>
          <samplecharacteristics>
            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
            <width>{tw}</width>
            <height>{th}</height>
          </samplecharacteristics>
        </format>
        <track>
          <clipitem id="clipitem-v-{clip.id}">
            <name>{title_safe}</name>
            <duration>{dur_frames}</duration>
            <start>0</start>
            <end>{dur_frames}</end>
            <in>{in_frame}</in>
            <out>{out_frame}</out>
            <file id="file-{clip.id}">
              <name>{escape(os.path.basename(media_path or 'clip.mp4'))}</name>
              <pathurl>{escape(media_uri)}</pathurl>
              <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
              <media>
                <video><samplecharacteristics><width>{tw}</width><height>{th}</height></samplecharacteristics></video>
                <audio><channelcount>2</channelcount></audio>
              </media>
            </file>
          </clipitem>
        </track>
      </video>
      <audio>
        <track>
          <clipitem id="clipitem-a-{clip.id}">
            <name>{title_safe}</name>
            <duration>{dur_frames}</duration>
            <start>0</start>
            <end>{dur_frames}</end>
            <in>{in_frame}</in>
            <out>{out_frame}</out>
            <file id="file-{clip.id}"/>
          </clipitem>
        </track>
      </audio>
    </media>
  </sequence>
</xmeml>
"""


def export_clip_nle(
    clip: ViralClip,
    project: Project,
    format_name: str,
) -> Dict[str, Any]:
    """
    Export clip timeline & captions into `.fcpxml`, Premiere `.xml`, `.srt`, or `.zip` Studio Bundle.
    """
    fmt = (format_name or "fcpxml").strip().lower()
    raw_words = clip.get_transcript()
    tl = compute_kept_segments_and_remapped_words(
        words=raw_words,
        clip_start_time=float(clip.start_time),
        clip_duration=max(0.5, float(clip.duration)),
        tighten_pacing=bool(getattr(clip, "tighten_pacing", False)),
        silence_threshold=float(getattr(clip, "silence_threshold", 0.45)),
        clean_filler_words=bool(getattr(clip, "clean_filler_words", False)),
    )
    eff_dur = float(tl["effective_duration"])
    words = list(tl["remapped_words"])
    if getattr(clip, "censor_profanity", False):
        words = mask_words_profanity(words)

    media_path = (
        clip.rendered_video_path
        if (clip.rendered_video_path and os.path.exists(clip.rendered_video_path))
        else (project.source_path or "")
    )
    safe_stem = f"lost_heaven_clip_{clip.id}"

    srt_txt = build_srt_content(
        words,
        bilingual=bool(getattr(clip, "bilingual_enabled", False)),
    )
    if fmt == "srt":
        return {"filename": f"{safe_stem}.srt", "media_type": "text/plain; charset=utf-8", "content": srt_txt.encode("utf-8")}

    fcpxml_txt = build_fcpxml(clip, project, words, eff_dur, media_path)
    if fmt in ("fcpxml", "resolve"):
        return {"filename": f"{safe_stem}.fcpxml", "media_type": "application/xml", "content": fcpxml_txt.encode("utf-8")}

    premiere_txt = build_premiere_xmeml(clip, project, eff_dur, media_path)
    if fmt in ("premiere", "xml", "xmeml"):
        return {"filename": f"{safe_stem}_premiere.xml", "media_type": "application/xml", "content": premiere_txt.encode("utf-8")}

    # Full ZIP bundle
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe_stem}.fcpxml", fcpxml_txt.encode("utf-8"))
        zf.writestr(f"{safe_stem}_premiere.xml", premiere_txt.encode("utf-8"))
        zf.writestr(f"{safe_stem}.srt", srt_txt.encode("utf-8"))
        meta = {
            "clip_id": clip.id,
            "project_id": project.id,
            "title": clip.selected_title or clip.hook_headline,
            "duration_seconds": eff_dur,
            "aspect_ratio": clip.aspect_ratio,
            "source_media": media_path,
        }
        zf.writestr("metadata.json", json.dumps(meta, indent=2).encode("utf-8"))
        if clip.rendered_video_path and os.path.exists(clip.rendered_video_path):
            zf.write(clip.rendered_video_path, arcname=f"{safe_stem}.mp4")
    return {
        "filename": f"{safe_stem}_nle_bundle.zip",
        "media_type": "application/zip",
        "content": buf.getvalue(),
    }
