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


def _fmt_vtt_time(seconds: float) -> str:
    ms_total = max(0, int(round(float(seconds) * 1000.0)))
    hrs = ms_total // 3600000
    mins = (ms_total % 3600000) // 60000
    secs = (ms_total % 60000) // 1000
    ms = ms_total % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{ms:03d}"


def build_vtt_content(
    words: List[Dict[str, Any]],
    max_words_per_cue: int = 5,
    bilingual: bool = False,
) -> str:
    """Group remapped spoken words into WebVTT (.vtt) captions."""
    clean_words = [
        w for w in (words or [])
        if isinstance(w, dict) and not w.get("deleted") and not w.get("cut_from_video") and str(w.get("word", "")).strip()
    ]
    if not clean_words:
        return "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n[No spoken captions]\n"

    cues: List[str] = ["WEBVTT\n"]
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
        body = f"{txt}\n{sec_txt}" if (bilingual and sec_txt) else txt
        cues.append(f"{_fmt_vtt_time(t0)} --> {_fmt_vtt_time(t1)}\n{body}\n")
    return "\n".join(cues)


def _fmt_smpte(seconds: float, fps: int = 30) -> str:
    tot_f = max(0, int(round(float(seconds) * fps)))
    f = tot_f % fps
    tot_s = tot_f // fps
    s = tot_s % 60
    m = (tot_s // 60) % 60
    h = tot_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def build_edl_content(
    clip: ViralClip,
    project: Project,
    effective_duration: float,
    media_path: str,
) -> str:
    """Generate CMX 3600 EDL cut list for Premiere / Resolve / Avid import."""
    title = (clip.selected_title or clip.hook_headline or f"LOST_HEAVEN_CLIP_{clip.id}").upper()
    reel = Path(media_path).stem[:16] if media_path else f"CLIP_{clip.id}"
    src_in = _fmt_smpte(float(clip.start_time or 0.0), 30)
    src_out = _fmt_smpte(float(clip.start_time or 0.0) + max(0.5, effective_duration), 30)
    rec_in = "01:00:00:00"
    rec_out = _fmt_smpte(3600.0 + max(0.5, effective_duration), 30)
    return "\n".join([
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        "",
        f"001  {reel:<8} V     C        {src_in} {src_out} {rec_in} {rec_out}",
        f"* FROM CLIP NAME: {Path(media_path).name if media_path else f'clip_{clip.id}.mp4'}",
        f"001  {reel:<8} A     C        {src_in} {src_out} {rec_in} {rec_out}",
        "",
    ])


def export_clip_nle(
    clip: ViralClip,
    project: Project,
    format_name: str,
) -> Dict[str, Any]:
    """
    Export clip timeline & captions into `.fcpxml`, Premiere `.xml`, `.edl`, `.srt`, `.vtt`, `.ass`, or `.zip` Studio Bundle.
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

    vtt_txt = build_vtt_content(
        words,
        bilingual=bool(getattr(clip, "bilingual_enabled", False)),
    )
    if fmt == "vtt":
        return {"filename": f"{safe_stem}.vtt", "media_type": "text/vtt; charset=utf-8", "content": vtt_txt.encode("utf-8")}

    import tempfile
    from app.services.captions_ass import generate_ass_script

    tw, th = resolve_target_dimensions(clip.aspect_ratio, clip.custom_width, clip.custom_height)
    ass_dir = tempfile.mkdtemp(prefix="lost_heaven_ass_")
    try:
        ass_path = os.path.join(ass_dir, f"{safe_stem}.ass")
        generate_ass_script(
            words=words,
            output_ass_path=ass_path,
            caption_style=getattr(clip, "caption_style", "Karaoke pop") or "Karaoke pop",
            caption_font=getattr(clip, "caption_font", "Montserrat") or "Montserrat",
            caption_animation=getattr(clip, "caption_animation", "Karaoke") or "Karaoke",
            font_size_percent=int(getattr(clip, "font_size_percent", 100) or 100),
            base_color=getattr(clip, "base_color", "#FFFFFF") or "#FFFFFF",
            active_color=getattr(clip, "active_color", "#C6F32F") or "#C6F32F",
            outline_color=getattr(clip, "outline_color", "#000000") or "#000000",
            position_grid=getattr(clip, "position_grid", "bottom_center") or "bottom_center",
            position_x=float(getattr(clip, "position_x", 50.0) or 50.0),
            position_y=float(getattr(clip, "position_y", 78.0) or 78.0),
            video_width=tw,
            video_height=th,
            clip_start_time=0.0,
            clip_duration=eff_dur,
            project_id=project.id,
        )
        ass_txt = Path(ass_path).read_text(encoding="utf-8") if os.path.exists(ass_path) else ""
    finally:
        import shutil
        shutil.rmtree(ass_dir, ignore_errors=True)

    if fmt == "ass":
        return {"filename": f"{safe_stem}.ass", "media_type": "text/plain; charset=utf-8", "content": ass_txt.encode("utf-8")}

    edl_txt = build_edl_content(clip, project, eff_dur, media_path)
    if fmt == "edl":
        return {"filename": f"{safe_stem}.edl", "media_type": "text/plain; charset=utf-8", "content": edl_txt.encode("utf-8")}

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
        zf.writestr(f"{safe_stem}.edl", edl_txt.encode("utf-8"))
        zf.writestr(f"{safe_stem}.srt", srt_txt.encode("utf-8"))
        zf.writestr(f"{safe_stem}.vtt", vtt_txt.encode("utf-8"))
        if ass_txt:
            zf.writestr(f"{safe_stem}.ass", ass_txt.encode("utf-8"))
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
