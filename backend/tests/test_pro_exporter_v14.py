"""Phase 3 tests: Pro Mastering Exporter (H.264, HEVC, ProRes, VP9, WAV/MP3 stems, Cover PNG, SRT/VTT/ASS/EDL, Multi-Aspect Batch ZIP)."""

import json
import subprocess
import zipfile
from pathlib import Path
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine


def _make_source_clip_media(out_path: Path, dur: float = 2.0) -> Path:
    from app.services.proxy import get_ffmpeg_cmd

    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size=640x360:rate=24:duration={dur}",
        "-f", "lavfi",
        "-i", f"sine=frequency=520:sample_rate=48000:duration={dur}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"Synthetic source video failed: {res.stderr[-400:]}"
    return out_path


def _seed_project_and_clip(tmp_path: Path, src_video: Path):
    from app.models import Project, ViralClip

    db_path = tmp_path / "exporter_v14.db"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    words = [
        {"word": "Hello", "start": 0.1, "end": 0.45},
        {"word": "world", "start": 0.5, "end": 0.85},
        {"word": "export", "start": 0.9, "end": 1.35},
        {"word": "master", "start": 1.4, "end": 1.85},
    ]
    with Session(eng) as session:
        proj = Project(id=701, title="Pro Export Studio", source_path=str(src_video), status="ready")
        clip = ViralClip(
            id=711,
            project_id=701,
            start_time=0.0,
            end_time=1.8,
            duration=1.8,
            selected_title="Pro Master Title",
            hook_headline="PRO MASTER",
            aspect_ratio="9:16",
            clip_size="480p",
            burn_captions=True,
            show_hook_banner=True,
            color_preset="teal_orange",
        )
        clip.set_transcript(words)
        session.add(proj)
        session.add(clip)
        session.commit()
        session.refresh(proj)
        session.refresh(clip)
        return proj, clip


def test_pro_master_codecs_audio_stems_and_cover_png(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.renderer import scale_dimensions_for_clip_size
    from app.services.exporter import export_pro_master, export_full_res_cover_png
    from app.services.proxy import get_ffprobe_cmd

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    assert scale_dimensions_for_clip_size(1080, 1920, "4k") == (2160, 3840)

    src_video = _make_source_clip_media(tmp_path / "src_exp.mp4", dur=2.0)
    proj, clip = _seed_project_and_clip(tmp_path, src_video)
    ffprobe = get_ffprobe_cmd()

    # 1. Clean Master H.264 + 24fps CFR
    r_h264 = export_pro_master(clip, proj, format_id="mp4_h264", resolution="480p", fps="24", quality_preset="compact", burn_captions=False, hardware_mode="cpu")
    assert Path(r_h264["file_path"]).exists()
    assert r_h264["filename"].endswith("_clean.mp4")

    # 2. MP4 HEVC / H.265
    r_hevc = export_pro_master(clip, proj, format_id="mp4_hevc", resolution="480p", quality_preset="compact", burn_captions=False, hardware_mode="cpu")
    assert Path(r_hevc["file_path"]).exists()
    p_hevc = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "json", r_hevc["file_path"]], capture_output=True, text=True)
    assert json.loads(p_hevc.stdout)["streams"][0]["codec_name"] == "hevc"

    # 3. MOV ProRes 422 LT
    r_prores = export_pro_master(clip, proj, format_id="mov_prores", resolution="480p", quality_preset="social_balanced", burn_captions=False, hardware_mode="cpu")
    assert Path(r_prores["file_path"]).exists() and r_prores["filename"].endswith(".mov")
    p_prores = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "json", r_prores["file_path"]], capture_output=True, text=True)
    assert json.loads(p_prores.stdout)["streams"][0]["codec_name"] == "prores"

    # 4. Audio Only WAV 24-bit & MP3 320k
    r_wav = export_pro_master(clip, proj, format_id="audio_wav")
    assert Path(r_wav["file_path"]).exists() and r_wav["filename"].endswith(".wav")
    p_wav = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "json", r_wav["file_path"]], capture_output=True, text=True)
    assert json.loads(p_wav.stdout)["streams"][0]["codec_name"] == "pcm_s24le"

    r_mp3 = export_pro_master(clip, proj, format_id="audio_mp3")
    assert Path(r_mp3["file_path"]).exists() and r_mp3["filename"].endswith(".mp3")

    # 5. Full-Res Cover PNG
    r_png = export_full_res_cover_png(clip, proj, resolution="540p")
    assert Path(r_png["file_path"]).exists()
    img = Image.open(r_png["file_path"])
    assert img.width > 100 and img.height > 100


def test_nle_vtt_ass_edl_and_multi_aspect_batch_zip(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.nle_exporter import export_clip_nle
    from app.services.exporter import export_multi_aspect_batch_zip

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage_nle"))
    src_video = _make_source_clip_media(tmp_path / "src_nle.mp4", dur=2.0)
    proj, clip = _seed_project_and_clip(tmp_path, src_video)

    srt = export_clip_nle(clip, proj, "srt")
    assert b"Hello world" in srt["content"]
    vtt = export_clip_nle(clip, proj, "vtt")
    assert vtt["content"].startswith(b"WEBVTT") and b"-->" in vtt["content"]
    ass = export_clip_nle(clip, proj, "ass")
    assert b"[Script Info]" in ass["content"] and b"[Events]" in ass["content"]
    edl = export_clip_nle(clip, proj, "edl")
    assert b"TITLE:" in edl["content"] and b"FCM: NON-DROP FRAME" in edl["content"]
    xml = export_clip_nle(clip, proj, "premiere")
    assert b"<xmeml" in xml["content"]
    fcp = export_clip_nle(clip, proj, "fcpxml")
    assert b"<fcpxml" in fcp["content"]

    batch_zip = export_multi_aspect_batch_zip(
        clip,
        proj,
        aspects=["9:16", "1:1"],
        resolution="480p",
        quality_preset="compact",
        burn_captions=False,
        hardware_mode="cpu",
    )
    assert Path(batch_zip["file_path"]).exists()
    with zipfile.ZipFile(batch_zip["file_path"], "r") as zf:
        names = set(zf.namelist())
        assert "clip_711_9x16.mp4" in names
        assert "clip_711_1x1.mp4" in names
        assert "clip_711.srt" in names
        assert "clip_711.vtt" in names
        assert "manifest.json" in names
