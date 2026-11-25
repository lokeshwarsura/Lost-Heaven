import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger("lost_heaven.translator")

SUPPORTED_LANGUAGES: List[str] = [
    "Spanish",
    "Hindi",
    "Telugu",
    "French",
    "German",
    "Portuguese",
    "Japanese",
    "Korean",
    "Arabic",
    "English",
]

# Compact deterministic offline word/phrase translations so bilingual rendering works without internet
_OFFLINE_LEXICON: Dict[str, Dict[str, str]] = {
    "spanish": {
        "hello": "hola", "world": "mundo", "today": "hoy", "we": "nosotros",
        "made": "hicimos", "make": "hacer", "money": "dinero", "secret": "secreto",
        "secrets": "secretos", "never": "nunca", "always": "siempre", "stop": "para",
        "this": "esto", "is": "es", "the": "el", "best": "mejor", "worst": "peor",
        "viral": "viral", "video": "video", "growth": "crecimiento", "grew": "creció",
        "faster": "más rápido", "fast": "rápido", "future": "futuro", "now": "ahora",
        "here": "aquí", "look": "mira", "why": "por qué", "how": "cómo",
        "first": "primero", "final": "final", "big": "gran", "huge": "enorme",
        "payoff": "recompensa", "and": "y", "in": "en", "to": "a", "for": "para",
        "with": "con", "you": "tú", "your": "tu", "subscribe": "suscríbete",
    },
    "french": {
        "hello": "bonjour", "world": "monde", "today": "aujourd'hui", "we": "nous",
        "made": "avons fait", "money": "argent", "secret": "secret", "never": "jamais",
        "always": "toujours", "stop": "arrête", "this": "ceci", "is": "est",
        "the": "le", "best": "meilleur", "faster": "plus vite", "future": "futur",
        "now": "maintenant", "look": "regarde", "first": "premier", "big": "grand",
        "and": "et", "in": "dans", "to": "à", "for": "pour", "subscribe": "abonnez-vous",
    },
    "german": {
        "hello": "hallo", "world": "welt", "today": "heute", "we": "wir",
        "made": "machten", "money": "geld", "secret": "geheimnis", "never": "nie",
        "always": "immer", "stop": "stopp", "this": "das", "is": "ist",
        "the": "die", "best": "beste", "faster": "schneller", "future": "zukunft",
        "now": "jetzt", "look": "schau", "first": "erste", "big": "groß",
        "and": "und", "in": "in", "to": "zu", "for": "für",
    },
    "portuguese": {
        "hello": "olá", "world": "mundo", "today": "hoje", "we": "nós",
        "made": "fizemos", "money": "dinheiro", "secret": "segredo", "never": "nunca",
        "always": "sempre", "this": "isso", "is": "é", "the": "o", "best": "melhor",
        "faster": "mais rápido", "future": "futuro", "now": "agora", "look": "olha",
        "and": "e", "in": "em", "for": "para",
    },
    "hindi": {
        "hello": "नमस्ते", "world": "दुनिया", "today": "आज", "we": "हम",
        "made": "कमाया", "money": "पैसा", "secret": "राज", "never": "कभी नहीं",
        "always": "हमेशा", "stop": "रुको", "this": "यह", "is": "है",
        "best": "सबसे अच्छा", "faster": "तेज़", "future": "भविष्य", "now": "अभी",
        "look": "देखो", "big": "बड़ा", "and": "और",
    },
    "telugu": {
        "hello": "నమస్కారం", "world": "ప్రపంచం", "today": "ఈరోజు", "we": "మేము",
        "money": "డబ్బు", "secret": "రహస్యం", "never": "ఎప్పుడూ కాదు",
        "this": "ఇది", "is": "ఉంది", "best": "అత్యుత్తమ", "now": "ఇప్పుడు",
        "look": "చూడు", "big": "పెద్ద", "and": "మరియు",
    },
    "japanese": {
        "hello": "こんにちは", "world": "世界", "today": "今日", "money": "お金",
        "secret": "秘密", "never": "決して", "best": "最高", "future": "未来",
        "now": "今", "look": "見て", "big": "大きな", "and": "そして",
    },
    "korean": {
        "hello": "안녕하세요", "world": "세계", "today": "오늘", "money": "돈",
        "secret": "비밀", "never": "절대", "best": "최고", "future": "미래",
        "now": "지금", "look": "보세요", "big": "큰", "and": "그리고",
    },
    "arabic": {
        "hello": "مرحبا", "world": "العالم", "today": "اليوم", "money": "المال",
        "secret": "سر", "never": "أبدا", "best": "الأفضل", "future": "المستقبل",
        "now": "الآن", "look": "انظر", "big": "كبير", "and": "و",
    },
}

_LANG_PREFIX_TAG = {
    "spanish": "ES",
    "french": "FR",
    "german": "DE",
    "portuguese": "PT",
    "hindi": "HI",
    "telugu": "TE",
    "japanese": "JA",
    "korean": "KO",
    "arabic": "AR",
    "english": "EN",
}


def _offline_translate_phrase(phrase: str, target_language: str) -> str:
    lang_key = str(target_language or "Spanish").strip().lower()
    lex = _OFFLINE_LEXICON.get(lang_key, {})
    tokens = phrase.strip().split()
    if not tokens:
        return ""
    out_tokens: List[str] = []
    hits = 0
    for tok in tokens:
        m = re.match(r"^([^\w]*)(.*?)([^\w]*)$", tok)
        if not m:
            out_tokens.append(tok)
            continue
        lead, core, trail = m.groups()
        mapped = lex.get(core.lower())
        if mapped:
            hits += 1
            if core.istitle():
                mapped = mapped[:1].upper() + mapped[1:]
            elif core.isupper() and mapped.isascii():
                mapped = mapped.upper()
            out_tokens.append(f"{lead}{mapped}{trail}")
        else:
            out_tokens.append(tok)

    candidate = " ".join(out_tokens)
    if hits == 0 and lang_key != "english":
        tag = _LANG_PREFIX_TAG.get(lang_key, lang_key[:2].upper())
        candidate = f"[{tag}] {candidate}"
    return candidate


def _group_words_for_translation(words: List[Dict[str, Any]], max_words: int = 4) -> List[List[int]]:
    groups: List[List[int]] = []
    cur: List[int] = []
    for idx, w in enumerate(words):
        if not isinstance(w, dict) or w.get("deleted") or w.get("cut_from_video"):
            continue
        txt = str(w.get("word", "")).strip()
        if not txt:
            continue
        if cur:
            gap = float(w.get("start", 0.0)) - float(words[cur[-1]].get("end", 0.0))
            if len(cur) >= max_words or gap > 0.65:
                groups.append(cur)
                cur = []
        cur.append(idx)
        if txt[-1] in ".!?":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def translate_clip_transcript(
    words: List[Dict[str, Any]],
    target_language: str = "Spanish",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Translate clip caption chunks into `target_language` (Gemini when configured,
    with deterministic offline dictionary fallback) and store `translated_phrase`
    + `translated_text` cleanly on active words.
    """
    lang = (target_language or "Spanish").strip()
    out_words = [dict(w) for w in (words or []) if isinstance(w, dict)]
    groups = _group_words_for_translation(out_words, max_words=4)
    if not groups:
        return {"target_language": lang, "words": out_words, "source": "empty", "translations": []}

    phrases = [" ".join(str(out_words[i].get("word", "")).strip() for i in g) for g in groups]
    translated_phrases: Optional[List[str]] = None
    source_used = "offline"

    eff_key = (api_key or getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if eff_key:
        try:
            from app.services.gemini_clipper import DEFAULT_MODEL_CASCADE, invoke_model_compatible
            from pydantic import BaseModel, Field

            class TranslationBatch(BaseModel):
                lines: List[str] = Field(description="Translated lines matching input list 1-to-1 in exact order")

            prompt = (
                f"Translate each short video caption phrase naturally into {lang}. "
                f"Keep translations punchy (similar length for vertical captions) and return exactly {len(phrases)} strings.\n"
                f"Input JSON array: {json.dumps(phrases, ensure_ascii=False)}"
            )
            chosen_model = (model or "").strip() or DEFAULT_MODEL_CASCADE[0]
            resp = invoke_model_compatible(
                api_key=eff_key,
                model_name=chosen_model,
                prompt=prompt,
                response_schema=TranslationBatch,
                timeout_seconds=25,
            )
            parsed = json.loads(resp.text) if getattr(resp, "text", None) else {}
            cand_lines = parsed.get("lines") if isinstance(parsed, dict) else None
            if isinstance(cand_lines, list) and len(cand_lines) == len(phrases):
                translated_phrases = [str(x).strip() or phrases[i] for i, x in enumerate(cand_lines)]
                source_used = "gemini"
        except Exception as exc:
            logger.info(f"Gemini translation fallback to offline lexicon ({exc})")

    if translated_phrases is None:
        translated_phrases = [_offline_translate_phrase(p, lang) for p in phrases]

    for g_idx, grp in enumerate(groups):
        tr_line = translated_phrases[g_idx]
        tr_tokens = tr_line.split()
        for pos, w_idx in enumerate(grp):
            out_words[w_idx]["translated_phrase"] = tr_line
            out_words[w_idx]["translated_chunk_id"] = g_idx
            # Distribute translated tokens across words cleanly so word-level metadata stays rich
            out_words[w_idx]["translated_text"] = tr_tokens[pos] if pos < len(tr_tokens) else ""

    return {
        "target_language": lang,
        "words": out_words,
        "source": source_used,
        "translations": translated_phrases,
    }
