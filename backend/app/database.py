from sqlalchemy import text
from sqlmodel import SQLModel, create_engine, Session
from app.config import settings

_lh_db = settings.storage_path / "lost_heaven.db"
_legacy_db = settings.storage_path / "clipship.db"
SQLITE_DB_PATH = _legacy_db if (_legacy_db.exists() and not _lh_db.exists()) else _lh_db
SQLITE_URL = f"sqlite:///{SQLITE_DB_PATH}"
engine = create_engine(SQLITE_URL, echo=False, connect_args={"check_same_thread": False})

def init_db():
    import app.models  # noqa: F401 - ensure all SQLModel tables (including ExportEvent) register before create_all
    SQLModel.metadata.create_all(engine)
    # Lightweight schema auto-migration for existing SQLite tables
    try:
        with Session(engine) as session:
            res = session.exec(text("PRAGMA table_info(viral_clips)")).fetchall()
            col_names = [r[1] for r in res] if res else []
            if col_names:
                migrations = {
                    "analysis_source": "ALTER TABLE viral_clips ADD COLUMN analysis_source TEXT DEFAULT 'gemini'",
                    "audio_volume": "ALTER TABLE viral_clips ADD COLUMN audio_volume FLOAT DEFAULT 1.0",
                    "normalize_audio": "ALTER TABLE viral_clips ADD COLUMN normalize_audio BOOLEAN DEFAULT 1",
                    "vocal_clarity": "ALTER TABLE viral_clips ADD COLUMN vocal_clarity BOOLEAN DEFAULT 0",
                    "hashtags": "ALTER TABLE viral_clips ADD COLUMN hashtags TEXT DEFAULT ''",
                    "tighten_pacing": "ALTER TABLE viral_clips ADD COLUMN tighten_pacing BOOLEAN DEFAULT 0",
                    "silence_threshold": "ALTER TABLE viral_clips ADD COLUMN silence_threshold FLOAT DEFAULT 0.45",
                    "censor_profanity": "ALTER TABLE viral_clips ADD COLUMN censor_profanity BOOLEAN DEFAULT 0",
                    "punch_zoom_intensity": "ALTER TABLE viral_clips ADD COLUMN punch_zoom_intensity FLOAT DEFAULT 1.14",
                    "voice_isolation": "ALTER TABLE viral_clips ADD COLUMN voice_isolation BOOLEAN DEFAULT 0",
                    "auto_sfx": "ALTER TABLE viral_clips ADD COLUMN auto_sfx BOOLEAN DEFAULT 0",
                    "retention_bar_enabled": "ALTER TABLE viral_clips ADD COLUMN retention_bar_enabled BOOLEAN DEFAULT 0",
                    "retention_bar_color": "ALTER TABLE viral_clips ADD COLUMN retention_bar_color TEXT DEFAULT '#C6F32F'",
                    "outro_cta_enabled": "ALTER TABLE viral_clips ADD COLUMN outro_cta_enabled BOOLEAN DEFAULT 0",
                    "outro_cta_text": "ALTER TABLE viral_clips ADD COLUMN outro_cta_text TEXT DEFAULT 'Subscribe for More'",
                    "broll_json": "ALTER TABLE viral_clips ADD COLUMN broll_json TEXT DEFAULT '[]'",
                    "status": "ALTER TABLE viral_clips ADD COLUMN status TEXT DEFAULT 'draft'",
                    "rendered_path": "ALTER TABLE viral_clips ADD COLUMN rendered_path TEXT",
                    "highlight_power_words": "ALTER TABLE viral_clips ADD COLUMN highlight_power_words BOOLEAN DEFAULT 0",
                    "keep_segments_json": "ALTER TABLE viral_clips ADD COLUMN keep_segments_json TEXT DEFAULT '[]'",
                    "merge_crossfade_sec": "ALTER TABLE viral_clips ADD COLUMN merge_crossfade_sec FLOAT DEFAULT 0.03",
                    "speakers_json": "ALTER TABLE viral_clips ADD COLUMN speakers_json TEXT DEFAULT '{}'",
                    "speaker_lower_thirds": "ALTER TABLE viral_clips ADD COLUMN speaker_lower_thirds BOOLEAN DEFAULT 0",
                    "face_safe_captions": "ALTER TABLE viral_clips ADD COLUMN face_safe_captions BOOLEAN DEFAULT 1",
                    "callout_cards_json": "ALTER TABLE viral_clips ADD COLUMN callout_cards_json TEXT DEFAULT '[]'",
                    "auto_callouts": "ALTER TABLE viral_clips ADD COLUMN auto_callouts BOOLEAN DEFAULT 0",
                    "bilingual_enabled": "ALTER TABLE viral_clips ADD COLUMN bilingual_enabled BOOLEAN DEFAULT 0",
                    "secondary_language": "ALTER TABLE viral_clips ADD COLUMN secondary_language TEXT DEFAULT ''",
                    "dubbing_enabled": "ALTER TABLE viral_clips ADD COLUMN dubbing_enabled BOOLEAN DEFAULT 0",
                    "dubbing_language": "ALTER TABLE viral_clips ADD COLUMN dubbing_language TEXT DEFAULT 'Spanish'",
                    "dubbing_voice": "ALTER TABLE viral_clips ADD COLUMN dubbing_voice TEXT DEFAULT 'default'",
                    "dubbing_bed_volume": "ALTER TABLE viral_clips ADD COLUMN dubbing_bed_volume FLOAT DEFAULT 0.15",
                    "dubbed_audio_path": "ALTER TABLE viral_clips ADD COLUMN dubbed_audio_path TEXT",
                    "backdrop_mode": "ALTER TABLE viral_clips ADD COLUMN backdrop_mode TEXT DEFAULT 'none'",
                    "backdrop_blur_intensity": "ALTER TABLE viral_clips ADD COLUMN backdrop_blur_intensity FLOAT DEFAULT 18.0",
                    "backdrop_color": "ALTER TABLE viral_clips ADD COLUMN backdrop_color TEXT DEFAULT '#18181B'",
                    "privacy_blur_json": "ALTER TABLE viral_clips ADD COLUMN privacy_blur_json TEXT DEFAULT '[]'",
                    "beat_sync_enabled": "ALTER TABLE viral_clips ADD COLUMN beat_sync_enabled BOOLEAN DEFAULT 0",
                    "spectrum_enabled": "ALTER TABLE viral_clips ADD COLUMN spectrum_enabled BOOLEAN DEFAULT 0",
                    "spectrum_position": "ALTER TABLE viral_clips ADD COLUMN spectrum_position TEXT DEFAULT 'bottom'",
                    "spectrum_color": "ALTER TABLE viral_clips ADD COLUMN spectrum_color TEXT DEFAULT '#C6F32F'",
                    "chapters_json": "ALTER TABLE viral_clips ADD COLUMN chapters_json TEXT DEFAULT '[]'",
                    "chapters_enabled": "ALTER TABLE viral_clips ADD COLUMN chapters_enabled BOOLEAN DEFAULT 0",
                    "handle_watermark_enabled": "ALTER TABLE viral_clips ADD COLUMN handle_watermark_enabled BOOLEAN DEFAULT 0",
                    "handle_text": "ALTER TABLE viral_clips ADD COLUMN handle_text TEXT DEFAULT ''",
                    "handle_platform_icon": "ALTER TABLE viral_clips ADD COLUMN handle_platform_icon TEXT DEFAULT 'none'",
                    "handle_corner": "ALTER TABLE viral_clips ADD COLUMN handle_corner TEXT DEFAULT 'bottom_right'",
                    "handle_opacity": "ALTER TABLE viral_clips ADD COLUMN handle_opacity FLOAT DEFAULT 0.65",
                    "handle_display_mode": "ALTER TABLE viral_clips ADD COLUMN handle_display_mode TEXT DEFAULT 'always'",
                    "source_webcam_region_json": "ALTER TABLE viral_clips ADD COLUMN source_webcam_region_json TEXT DEFAULT ''",
                    "visual_map_json": "ALTER TABLE viral_clips ADD COLUMN visual_map_json TEXT DEFAULT ''",
                    "framing_override_json": "ALTER TABLE viral_clips ADD COLUMN framing_override_json TEXT DEFAULT ''",
                    "camera_motion": "ALTER TABLE viral_clips ADD COLUMN camera_motion TEXT DEFAULT 'steady'",
                    "whip_pan_cuts": "ALTER TABLE viral_clips ADD COLUMN whip_pan_cuts BOOLEAN DEFAULT 0",
                    "broll_layout": "ALTER TABLE viral_clips ADD COLUMN broll_layout TEXT DEFAULT 'fullscreen'",
                    "depth_pop": "ALTER TABLE viral_clips ADD COLUMN depth_pop FLOAT DEFAULT 0.0",
                    "best_frames_json": "ALTER TABLE viral_clips ADD COLUMN best_frames_json TEXT DEFAULT '[]'",
                    "options_json": "ALTER TABLE viral_clips ADD COLUMN options_json TEXT DEFAULT '{}'",
                }
                added = False
                for col, sql in migrations.items():
                    if col not in col_names:
                        session.exec(text(sql))
                        added = True
                # Also migrate projects table columns if missing
                p_res = session.exec(text("PRAGMA table_info(projects)")).fetchall()
                p_cols = [r[1] for r in p_res] if p_res else []
                if p_cols and "multimodal_analysis_json" not in p_cols:
                    session.exec(text("ALTER TABLE projects ADD COLUMN multimodal_analysis_json TEXT DEFAULT ''"))
                    added = True
                if p_cols and "default_framing" not in p_cols:
                    session.exec(text("ALTER TABLE projects ADD COLUMN default_framing TEXT DEFAULT 'smart_auto'"))
                    added = True
                if p_cols and "ai_mode" not in p_cols:
                    session.exec(text("ALTER TABLE projects ADD COLUMN ai_mode TEXT"))
                    added = True
                # Migrate caption_style_presets table columns if missing on older DBs
                cs_res = session.exec(text("PRAGMA table_info(caption_style_presets)")).fetchall()
                cs_cols = [r[1] for r in cs_res] if cs_res else []
                if cs_cols:
                    cs_migrations = {
                        "outline_color": "ALTER TABLE caption_style_presets ADD COLUMN outline_color TEXT DEFAULT '#000000'",
                        "border_style": "ALTER TABLE caption_style_presets ADD COLUMN border_style INTEGER DEFAULT 1",
                        "highlight_power_words": "ALTER TABLE caption_style_presets ADD COLUMN highlight_power_words BOOLEAN DEFAULT 0",
                        "how_phrase_arrives": "ALTER TABLE caption_style_presets ADD COLUMN how_phrase_arrives TEXT DEFAULT 'All at once'",
                    }
                    for cs_col, cs_sql in cs_migrations.items():
                        if cs_col not in cs_cols:
                            session.exec(text(cs_sql))
                            added = True
                if added:
                    session.commit()
    except Exception as e:
        pass

def get_session():
    with Session(engine) as session:
        yield session
