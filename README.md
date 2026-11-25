# Lost Heaven — AI Video Clipping Studio

**Local-First AI Video Clipping, Reframing & Multi-Platform Publishing Workstation**

Lost Heaven pairs multimodal AI clip discovery (**Google Gemini API** via `google-genai`, with deterministic offline NLP/audio fallback) with a 100% local FFmpeg / OpenCV / Pillow rendering pipeline. Master videos stay on your machine—only lightweight proxies or transcripts are analyzed when cloud AI is enabled.

---

## Architecture

```
+----------------------------------------------------------------------+
|                AI Creative Director (Gemini / Offline NLP)           |
|  - Universal live Gemini model discovery & multi-model fallback       |
|  - Transcript-first or 480p proxy analysis -> JSON clip blueprints    |
|  - AI Hook Rewriter (Curiosity Gap / Contrarian / High-Stakes)        |
|  - Multi-Platform Social Copywriter (Shorts, TikTok, Reels, X)        |
+----------------------------------+-----------------------------------+
                                   |
                                   v
+----------------------------------------------------------------------+
|                  Local Post-Production Factory                       |
|  - FastAPI + SQLModel (SQLite persistence & per-stage checkpoints)    |
|  - OpenCV / MediaPipe smooth EMA active-speaker face tracking         |
|  - Pillow RGBA + ffconcat word-level caption engine (Unicode-safe)    |
|  - FFmpeg NVENC (h264_nvenc) / libx264 rendering + -14 LUFS audio     |
|  - React 19 + Vite 6 Desktop NLE UI with 11 live CSS-variable themes  |
+----------------------------------------------------------------------+
```

---

## Key Features

### 1. Multi-Clip Ingestion & Stage 5 Batch Pre-Rendering
- Ingest local videos or URLs via `yt-dlp`, transcribe with `faster-whisper`, and rank viral segments.
- Stage 5 pre-renders playable `fast_preview` MP4s for **all extracted clips (`1..N`)**, with automatic on-demand rendering fallback on `GET /api/clips/{project_id}/{clip_id}/video` so switching clips never hits `404`.
- Live progress console with server-computed UTC elapsed timer and API-key recovery pause/resume.

### 2. Step 1 — Setup, Framing & AI Hook Generator
- **5 Framing Templates:** `auto_track`, `fit_blur`, `webcam_gameplay_split`, `gameplay_webcam_pip`, and `split_podcast`.
- **Aspect Ratios & Framing Controls:** `9:16`, `4:5`, `1:1`, `4:3`, `16:9`, custom dimensions, zoom, horizontal pan (`pan_x`), webcam corner & split ratio, cold-open quotes, dead-air trimming, and punch zoom.
- **AI Hook Variants:** One-click generation of 3 ranked hook angles (*Curiosity Gap*, *Bold / Contrarian Claim*, *High-Stakes Action*) backed by `/api/clips/{project_id}/{clip_id}/generate-hooks`.

### 3. Step 2 — Pro Captions, Smart Emojis & Transcript Cuts
- **Pillow RGBA + `ffconcat` Engine:** Unicode-safe rendering inside isolated temporary workspaces with 12 built-in presets, custom saved quick styles, and uploaded TTF/OTF fonts.
- **Smart Emoji & Power Words:** 120+ categorized keyword emoji badges plus an **Auto-Highlight Power Words** toggle (`1.08x` pop & highlight accent).
- **Word-Level Timeline Editing:** Toggle captions per word (`deleted`), cut words out of video/audio (`cut_from_video` with 30 ms `acrossfade`), tighten dead-air pacing, remove fillers, and auto-bleep profanity (`1000 Hz`).

### 4. Step 3 — Color Grading, Effects, Audio & Publishing
- **100% Parity Grade & Effects:** Preset looks + manual light/exposure/color sliders, `100% B&W` footage isolation with colored captions, vignette, glow, blur, film grain, bottom fade, Retention Progress Bar, 2s Outro CTA, and watermark overlay.
- **Broadcast Audio:** Loudness normalization (`-14 LUFS`), vocal clarity/isolation, micro-SFX, and ducked background music.
- **Multi-Platform Publish Pack (`.zip`):** Exports `01_Shorts_TikTok_9x16.mp4` (`1080x1920`), `02_Instagram_Feed_4x5.mp4` (`1080x1350`), `03_YouTube_X_16x9.mp4` (`1920x1080`), `04_Thumbnail_Cover.jpg` (sharpness + vocal-peak selected frame), `05_Captions.srt`, and `06_Social_Copy.txt`.
- **Social Copy Studio & Direct NLE Export:** AI platform copy tabs (`YouTube Shorts`, `TikTok`, `Instagram Reels`, `X`) plus FCPXML / Premiere XML / `.srt` export.

### 5. Workstation UX, Heatmap & LAN Access
- **Waveform Retention Heatmap:** Visualizes per-bin RMS vocal energy peaks, speech density, dead-air gaps, live playhead needle, click-to-seek, and `±5s` trim handles.
- **NLE Shortcuts & Per-Clip Undo/Redo:** 30-snapshot undo/redo stack (`Ctrl+Z` / `Ctrl+Y`), `Space` play/pause, `ArrowLeft`/`ArrowRight` (`Shift` for `±5s`), `I`/`O` trim at playhead, `M` mute, and `?` cheatsheet modal.
- **LAN & Tunnel Ready:** Vite binds to `0.0.0.0:5173` and proxies `/api` + `/media` directly to FastAPI so other devices on the local network can operate the studio seamlessly.

---

## Local Setup

### Prerequisites
- **Python 3.11+**
- **Node.js 20+**
- **FFmpeg & FFprobe** installed in `PATH` (or placed in `bin/` locally)
- *(Optional)* NVIDIA CUDA GPU for `h264_nvenc` acceleration

### 1. Backend Setup
```bash
cd backend
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS/Linux:
# source venv/bin/activate

pip install -r requirements.txt
cp storage/settings.example.json storage/settings.json
cp .env.example .env
```

Start the FastAPI backend (from repo root):
```powershell
.\backend\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
```

### 2. Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

Open **[http://localhost:5173](http://localhost:5173)** locally, or open `http://<YOUR_PC_LAN_IP>:5173` from another laptop on the same network (`Vite` proxies `/api` and `/media` to `http://127.0.0.1:8000`).

---

## Testing & Verification

```powershell
# Backend test suite
.\backend\venv\Scripts\python.exe -m pytest backend/tests -v --tb=short

# Frontend TypeScript check & production bundle
cd frontend
npm run build
```

---

## License
MIT License.
