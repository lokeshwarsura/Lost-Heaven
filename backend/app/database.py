from sqlalchemy import text
from sqlmodel import SQLModel, create_engine, Session
from app.config import settings

_lh_db = settings.storage_path / "lost_heaven.db"
_legacy_db = settings.storage_path / "clipship.db"
SQLITE_DB_PATH = _legacy_db if (_legacy_db.exists() and not _lh_db.exists()) else _lh_db
SQLITE_URL = f"sqlite:///{SQLITE_DB_PATH}"
engine = create_engine(SQLITE_URL, echo=False, connect_args={"check_same_thread": False})

def init_db():
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
                }
                added = False
                for col, sql in migrations.items():
                    if col not in col_names:
                        session.exec(text(sql))
                        added = True
                if added:
                    session.commit()
    except Exception as e:
        pass

def get_session():
    with Session(engine) as session:
        yield session
