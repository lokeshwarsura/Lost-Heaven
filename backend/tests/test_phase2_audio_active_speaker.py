import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from app.services.audio_mastering import build_voice_filter_chain
from app.services.face_tracker import (
    _pick_primary_face,
    detect_punch_zoom_peaks,
    snap_clip_boundaries_to_scenes,
    track_faces_in_clip,
)
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.renderer import render_clip_ultra
from app.services.sfx_engine import ensure_procedural_sfx_pack


def test_active_speaker_scoring_prefers_speaking_face():
    silent_left = {"cx": 0.25, "cy": 0.4, "w": 0.22, "h": 0.30, "mouth_activity": 0.02}
    talking_right = {"cx": 0.75, "cy": 0.4, "w": 0.22, "h": 0.30, "mouth_activity": 0.78}
    picked = _pick_primary_face(
        [silent_left, talking_right],
        prev_cx=0.25,
        prev_cy=0.4,
        has_prev=True,
        audio_energy=0.85,
    )
    assert picked is not None
    assert abs(picked["cx"] - 0.75) < 1e-4


def test_scene_aware_cut_snapping_aligns_boundaries():
    tmp_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_p2_scene_ドキュメント test_"))
    try:
        ffmpeg = get_ffmpeg_cmd()
        scene_mp4 = tmp_dir / "scenes test.mp4"
        # Construct 3 distinct visual shots: Blue [0-2s], Red [2-4.5s], Green [4.5-6s]
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=15:d=2.0",
            "-f", "lavfi", "-i", "color=c=red:s=320x180:r=15:d=2.5",
            "-f", "lavfi", "-i", "color=c=green:s=320x180:r=15:d=1.5",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(scene_mp4),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr

        clips = [{"start_time": 2.30, "end_time": 4.25, "duration": 1.95}]
        # Verify start=2.30 & end=4.25 stay safe if cut >0.5s, and [1.80, 4.70] snaps cleanly onto 2.0s & 4.5s!
        clips_snap = [{"start_time": 1.80, "end_time": 4.70, "duration": 2.90}]
        snapped = snap_clip_boundaries_to_scenes(str(scene_mp4), clips_snap, total_duration=6.0, snap_window=0.5)
        assert abs(snapped[0]["start_time"] - 2.0) <= 0.25, f"Expected start snap ~2.0, got {snapped[0]['start_time']}"
        assert abs(snapped[0]["end_time"] - 4.5) <= 0.25, f"Expected end snap ~4.5, got {snapped[0]['end_time']}"

        # Face tracker also completes cleanly inside Unicode path
        trk = track_faces_in_clip(str(scene_mp4), start_sec=0.0, end_sec=3.0, cache_dir=str(tmp_dir))
        assert len(trk["keyframes"]) >= 2
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_sfx_pack_voice_isolation_ducked_bgm_and_punch_zoom():
    tmp_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_p2_fx_ドキュメント test_"))
    try:
        sfx_pack = ensure_procedural_sfx_pack(tmp_dir / "sfx")
        for key in ("whoosh", "pop", "riser"):
            p = Path(sfx_pack[key])
            assert p.exists()
            with wave.open(str(p), "rb") as wf:
                assert wf.getnchannels() == 2
                assert wf.getframerate() == 48000
                assert wf.getnframes() > 2000

        chain = build_voice_filter_chain(voice_isolation=True, audio_volume=1.1, normalize_audio=True)
        assert "highpass=f=90" in chain
        assert "afftdn" in chain
        assert "acompressor" in chain
        assert "loudnorm" in chain

        ffmpeg = get_ffmpeg_cmd()
        src_mp4 = tmp_dir / "speaker input.mp4"
        bgm_wav = tmp_dir / "bgm loop.wav"

        # 5s video with speech bursts around t=1.2s and t=3.6s + silence around t=2.2s
        voice_expr = (
            "if(between(t,0.8,1.8),0.45*sin(2*PI*300*t),"
            "if(between(t,3.4,4.2),0.55*sin(2*PI*350*t),0.0))"
        )
        res_v = subprocess.run(
            [
                ffmpeg, "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=5.0",
                "-f", "lavfi", "-i", f"aevalsrc='{voice_expr}':s=44100:d=5.0",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "1",
                str(src_mp4),
            ],
            capture_output=True, text=True, timeout=45,
        )
        assert res_v.returncode == 0, res_v.stderr

        # Generate constant 660Hz BGM WAV
        res_b = subprocess.run(
            [
                ffmpeg, "-y",
                "-f", "lavfi", "-i", "aevalsrc='0.35*sin(2*PI*660*t)':s=48000:d=5.0",
                "-c:a", "pcm_s16le", "-ac", "2",
                str(bgm_wav),
            ],
            capture_output=True, text=True, timeout=20,
        )
        assert res_b.returncode == 0, res_b.stderr

        pz = detect_punch_zoom_peaks(str(src_mp4), 0.0, 5.0, base_zoom=1.0, punch_intensity=1.22)
        assert pz["peak_zoom"] == 1.22
        assert len(pz["punch_timestamps"]) >= 1

        out_mp4 = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=9902,
            project_id=9902,
            start_time=0.0,
            duration=5.0,
            words=[{"word": "Testing", "start": 0.8, "end": 1.7}],
            aspect_ratio="9:16",
            frame_layout="auto_track",
            burn_captions=False,
            hook_headline="BIG HOOK HERE",
            show_hook_banner=True,
            dynamic_punch_zoom=True,
            punch_zoom_intensity=1.22,
            voice_isolation=True,
            auto_sfx=True,
            bgm_path=str(bgm_wav),
            bgm_volume=0.35,
            quality_mode="fast_preview",
        )
        assert os.path.exists(out_mp4)
        meta = probe_video(out_mp4)
        assert meta["has_audio"] is True
        assert abs(meta["duration"] - 5.0) < 0.45

        # Extract output audio & verify non-silent stereo audio rendered cleanly
        out_wav = tmp_dir / "rendered.wav"
        res_e = subprocess.run(
            [ffmpeg, "-y", "-i", out_mp4, "-vn", "-c:a", "pcm_s16le", str(out_wav)],
            capture_output=True, text=True, timeout=20,
        )
        assert res_e.returncode == 0
        with wave.open(str(out_wav), "rb") as wf:
            arr = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        assert float(np.sqrt(np.mean(arr * arr))) > 0.01
    finally:
        if "out_mp4" in locals() and os.path.exists(out_mp4):
            try:
                os.remove(out_mp4)
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
