import pytest
from app.models import GeminiClipItem, GeminiAnalysisResponse
from app.services.gemini_clipper import (
    parse_time_str,
    get_duration_range,
    build_gemini_prompt
)

def test_parse_time_str():
    assert parse_time_str("00:01:23") == 83.0
    assert parse_time_str("01:30") == 90.0
    assert parse_time_str("45.5") == 45.5

def test_get_duration_range():
    assert get_duration_range("15-30s") == (15.0, 30.0)
    assert get_duration_range("30-58s") == (30.0, 58.0)
    assert get_duration_range("60-180s") == (60.0, 180.0)

def test_build_gemini_prompt_with_topic():
    prompt = build_gemini_prompt("15-30s", 8, topic_focus="clutch gaming moments")
    assert "clutch gaming moments" in prompt
    assert "8 distinct" in prompt
    assert "15 and 30 seconds" in prompt

def test_gemini_clip_item_schema():
    valid_data = {
        "start_time": "00:00:10",
        "end_time": "00:00:52",
        "start_seconds": 10.0,
        "end_seconds": 52.0,
        "virality_score": 95,
        "hook_score": 92,
        "retention_score": 90,
        "emotional_score": 88,
        "standalone_score": 94,
        "viral_rationale": "High curiosity opening with massive turnaround payoff.",
        "hook_headline": "THE WEALTH PARADOX REVEALED",
        "cold_open_quote": "Most people stay poor because of this single mindset error.",
        "youtube_titles": [
            "Why You Are Still Broke #Shorts",
            "The Mindset That Changed Everything #Shorts",
            "Stop Doing This In 2026 #Shorts"
        ],
        "youtube_description": "The brutal financial truth. #Shorts #Wealth #Mindset",
        "pinned_comment": "Do you believe mindset matters more than luck?"
    }
    item = GeminiClipItem(**valid_data)
    assert item.virality_score == 95
    assert item.start_seconds == 10.0

    resp = GeminiAnalysisResponse(clips=[item])
    assert len(resp.clips) == 1

def test_gemini_prompt_and_duration():
    min_d, max_d = get_duration_range("15s")
    assert min_d == 15.0 and max_d == 30.0
    prompt = build_gemini_prompt("60s", 5, topic_focus="AI video engineering")
    assert "AI video engineering" in prompt
    assert "5 distinct" in prompt
