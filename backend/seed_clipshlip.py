import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import engine, init_db
from app.models import Project, ViralClip, BrandPreset, CaptionStylePreset
from sqlmodel import Session

db_file = Path(__file__).parent / "storage" / "clipship.db"
if db_file.exists():
    try:
        os.remove(db_file)
        print("Removed old database.")
    except Exception as e:
        print(f"Could not remove old db (might be open): {e}")

init_db()

with Session(engine) as session:
    # 1. Default Brand Preset
    brand = BrandPreset(
        name="Obsidian Amber Brand",
        caption_style_name="Karaoke pop",
        watermark_corner="top_right",
        watermark_opacity=0.85,
        watermark_size=0.15,
        aspect_ratio="9:16",
        safe_zone_guide="TikTok"
    )
    session.add(brand)

    # 2. Project
    proj = Project(
        title="AI Startup Masters: Podcasting and Local Silicon",
        source_type="local",
        source_filename="ai_startup_podcast.mp4",
        source_path="storage/masters/ai_startup_podcast.mp4",
        duration=70.0,
        resolution_w=1920,
        resolution_h=1080,
        fps=30.0,
        status="analyzed",
        default_aspect_ratio="9:16",
        duration_preset="Auto",
        clip_count=5,
        caption_language="As spoken",
        hardware_mode="gpu"
    )
    session.add(proj)
    session.commit()
    session.refresh(proj)

    # 3. Clip 1
    clip1 = ViralClip(
        project_id=proj.id,
        start_time=5.0,
        end_time=38.0,
        duration=33.0,
        virality_score=97,
        hook_score=98,
        retention_score=96,
        emotional_score=92,
        standalone_score=95,
        viral_rationale="Intense hook revealing how modern AI architectures replace traditional rendering farms with millisecond local inference.",
        hook_headline="THE $10M GPU SECRET",
        show_hook_banner=True,
        cold_open_quote="We stopped cloud rendering completely and moved 100% to local silicon.",
        use_cold_open=True,
        auto_trim_dead_air=False,
        dynamic_punch_zoom=False,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        caption_style="Karaoke pop",
        caption_font="Montserrat",
        caption_animation="Karaoke",
        base_color="#FFFFFF",
        active_color="#C6F32F",
        outline_color="#000000",
        font_size_percent=100,
        bold=True,
        italic=False,
        all_caps=True,
        text_align="Centre",
        position_grid="bottom_center",
        position_x=50.0,
        position_y=78.0,
        hold_back_alpha=1.0,
        highlight_shape="Colour only",
        emoji_enabled=True,
        clean_filler_words=False,
        clip_size="1080p",
        color_preset="cinematic",
        warmth=0.10,
        contrast=1.15,
        vignette=0.30,
        selected_title="Why We Swapped Cloud Farms for Local GPUs #Shorts",
        youtube_description="The real engineering breakdown of why local inference is crushing cloud costs in 2026. #AI #GPU #Tech #Shorts",
        pinned_comment="Would you run this locally on your own machine? Drop your thoughts below!",
        privacy_status="private"
    )
    clip1.set_titles([
        "Why We Swapped Cloud Farms for Local GPUs #Shorts",
        "The $10M GPU Secret Nobody Talks About #Shorts",
        "Stop Paying Cloud Render Bills Immediately #Shorts"
    ])
    words = [
        {"word": "We", "start": 5.1, "end": 5.4, "is_filler": False},
        {"word": "stopped", "start": 5.4, "end": 5.9, "is_filler": False},
        {"word": "cloud", "start": 5.9, "end": 6.3, "is_filler": False},
        {"word": "rendering", "start": 6.3, "end": 6.9, "is_filler": False},
        {"word": "completely", "start": 6.9, "end": 7.6, "is_filler": False},
        {"word": "and", "start": 7.6, "end": 7.8, "is_filler": False},
        {"word": "moved", "start": 7.8, "end": 8.2, "is_filler": False},
        {"word": "everything", "start": 8.2, "end": 8.8, "is_filler": False},
        {"word": "to", "start": 8.8, "end": 9.0, "is_filler": False},
        {"word": "local", "start": 9.0, "end": 9.4, "is_filler": False},
        {"word": "hardware", "start": 9.4, "end": 10.0, "is_filler": False}
    ]
    clip1.set_transcript(words)
    session.add(clip1)

    # 4. Clip 2
    clip2 = ViralClip(
        project_id=proj.id,
        start_time=39.0,
        end_time=68.0,
        duration=29.0,
        virality_score=91,
        hook_score=93,
        retention_score=89,
        emotional_score=88,
        standalone_score=90,
        viral_rationale="Contrarian take explaining why most creator startups burn millions on unnecessary infrastructure.",
        hook_headline="THE STARTUP CAPITAL TRAP",
        show_hook_banner=True,
        cold_open_quote="Most founders are raising venture debt just to pay AWS.",
        use_cold_open=False,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        caption_style="Hormozi",
        caption_font="Montserrat",
        active_color="#FFFF00",
        selected_title="The Hidden AWS Trap Destroying Startups #Shorts"
    )
    clip2.set_titles([
        "The Hidden AWS Trap Destroying Startups #Shorts",
        "90% of Tech Startups Make This Fatal Cloud Error #Shorts",
        "How to Cut Your Server Bills by 80% Overnight #Shorts"
    ])
    clip2.set_transcript(words)
    session.add(clip2)

    session.commit()
    print("Database seeded with ClipShlip project, brand preset, and viral clips.")
