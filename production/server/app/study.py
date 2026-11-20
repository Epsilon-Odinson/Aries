"""Study skill — teaching from the user's own material.

This is the "great teacher" skill: Aries teaches the way Muse
teaches Epsilon — answer the exact question, straight from the
user's own slides, handout or notes; the material is the authority;
correct gently; brief when brief; link across lessons; teach a new
tool by one worked example; never quiz unless asked; one fact per
Anki card.

Everything here is per account: materials, card sets, quizzes and
exam dates are the signed-in user's only. Files live under the
server data dir (``data/study/``), never in the repo. Extracted
text is also kept in the database, so teaching still works when the
server disk is wiped (Render's free disk is ephemeral).

No new paid service: the LLM is the same NVIDIA model as chat, and
audio reuses the existing ElevenLabs talk-back setup.
"""

from __future__ import annotations

import io
import os
import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from .db import ExamCountdown, QuizSession, StudyMaterial, StudySet
from .llm import chat as llm_chat

STUDY_DIR = os.getenv("STUDY_DIR", "data/study")
SETS_DIR = os.path.join(STUDY_DIR, "sets")

# Uploads bigger than this are refused, and extracted text is capped
# so one giant file cannot fill the database or the prompt.
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 200_000

# Anki: one fact per card, capped per request so a runaway generation
# cannot flood the user (or the model) with cards.
MAX_CARDS = 30

# A summary or a listen-to text is trimmed to this, so one request
# cannot eat the whole free voice quota in a single call.
MAX_AUDIO_CHARS = 4000


def _safe_user_key(user_key: str) -> str:
    safe = re.sub(r"[^a-z0-9_\-]", "", (user_key or "").lower())
    if not safe:
        raise ValueError("Invalid user key")
    return safe


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "file")
    safe = re.sub(r"[^A-Za-z0-9._\-]", "_", base).strip("._") or "file"
    return safe[:120]


# ---------------------------------------------------------------- extraction


def extract_text(data: bytes, kind: str) -> str:
    """Plain text out of an uploaded file. PPTX slides (including
    tables), PDF pages, or plain text. Returns "" when the file has
    no readable text — the caller says so honestly instead of
    pretending the file was read."""
    if kind == "pptx":
        from pptx import Presentation

        prs = Presentation(io.BytesIO(data))
        parts: list[str] = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    text = shape.text_frame.text.strip()
                    if text:
                        parts.append(text)
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if cells:
                            parts.append(" | ".join(cells))
            if slide.notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    parts.append(notes)
        return "\n".join(parts)[:MAX_TEXT_CHARS]
    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages:
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                text = ""
            if text:
                parts.append(text)
        return "\n".join(parts)[:MAX_TEXT_CHARS]
    # txt / md
    return data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]


def kind_for_filename(filename: str) -> str:
    """pptx | pdf | txt | md by extension — "" for anything else,
    so an unsupported file is refused instead of misread as text."""
    lower = (filename or "").lower()
    if lower.endswith(".pptx"):
        return "pptx"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".txt"):
        return "txt"
    if lower.endswith(".md"):
        return "md"
    return ""


# ---------------------------------------------------------------- grounding


_STOPWORDS = {
    "what", "when", "where", "which", "who", "whom", "whose", "why",
    "how", "does", "do", "did", "is", "are", "was", "were", "can",
    "could", "would", "should", "will", "shall", "the", "a", "an",
    "and", "or", "but", "of", "to", "in", "on", "for", "with", "from",
    "about", "that", "this", "these", "those", "there", "here", "your",
    "you", "please", "tell", "me", "explain", "teach", "know", "give",
    "show", "says", "say", "does", "mean", "means", "called", "name",
}


def _content_words(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z]{3,}", (text or "").lower())
        if w not in _STOPWORDS
    }


def _chunks(text: str, size: int = 700) -> list[str]:
    """Split text into readable passages, on paragraph breaks where
    possible, so a quoted passage is a whole thought, not a cut-off."""
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paras:
        if len(current) + len(para) + 1 > size and current:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def find_passages(db: Session, user_key: str, query: str, limit: int = 3) -> list[dict]:
    """Best-matching passages from this user's own material, by
    shared words with the question. Returns [] when nothing matches
    — the caller then answers normally and never pretends the
    material covered it."""
    words = _content_words(query)
    if not words:
        return []
    # A passage must share at least one content word; when the
    # question has several, two shared words is a stronger bar.
    needed = 2 if len(words) >= 3 else 1
    scored: list[tuple[int, str, str]] = []
    for mat in (
        db.query(StudyMaterial).filter_by(user_key=user_key).all()
    ):
        for chunk in _chunks(mat.text or ""):
            chunk_words = _content_words(chunk)
            shared = words & chunk_words
            if len(shared) >= needed:
                scored.append((len(shared), mat.filename or "material", chunk))
    scored.sort(key=lambda item: -item[0])
    return [
        {"filename": filename, "passage": chunk}
        for _, filename, chunk in scored[:limit]
    ]


def match_material(db: Session, user_key: str, topic: str):
    """The user's material whose name — or whose text — best matches
    a topic like "cell structure". Name match wins; otherwise the
    material with the most shared words. None when nothing fits."""
    topic_words = _content_words(topic)
    best = None
    best_score = 0
    for mat in db.query(StudyMaterial).filter_by(user_key=user_key).all():
        name_words = _content_words(mat.filename or "")
        score = len(topic_words & name_words) * 10
        if score == 0:
            score = len(topic_words & _content_words((mat.text or "")[:2000]))
        if score > best_score:
            best, best_score = mat, score
    return best if best_score > 0 else None


# ---------------------------------------------------------------- files


def save_material_file(user_key: str, filename: str, data: bytes) -> str:
    """Keep the uploaded file under the server data dir, one folder
    per account. Returns the stored name."""
    folder = os.path.join(STUDY_DIR, _safe_user_key(user_key))
    os.makedirs(folder, exist_ok=True)
    stored = _safe_filename(filename)
    path = os.path.join(folder, stored)
    if os.path.exists(path):
        stem, ext = os.path.splitext(stored)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        stored = f"{stem}-{stamp}{ext}"
        path = os.path.join(folder, stored)
    with open(path, "wb") as fh:
        fh.write(data)
    return stored


def delete_material_file(user_key: str, stored_name: str) -> None:
    path = os.path.join(STUDY_DIR, _safe_user_key(user_key), _safe_filename(stored_name))
    try:
        os.remove(path)
    except OSError:
        pass


def save_anki_set(user_key: str, name: str, tsv_text: str) -> tuple[str, str]:
    """Save a generated .tsv under the sets folder. Returns the
    stored filename and its full path. The name carries a timestamp
    plus a random suffix, so two sets made in the same second never
    share a file."""
    import secrets

    folder = os.path.join(SETS_DIR, _safe_user_key(user_key))
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    stored = _safe_filename(f"anki-{stamp}-{secrets.token_hex(3)}.tsv")
    path = os.path.join(folder, stored)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tsv_text)
    return stored, path


def anki_set_path(user_key: str, stored_name: str) -> str:
    return os.path.join(SETS_DIR, _safe_user_key(user_key), _safe_filename(stored_name))


# ---------------------------------------------------------------- Anki


def anki_tsv(cards: list[tuple[str, str]]) -> str:
    """One fact per card, tab-separated, Basic note type — the same
    format Muse hands Epsilon for AnkiDroid import: exactly two tab
    fields per line, no header."""
    lines = []
    for front, back in cards:
        front = " ".join((front or "").split())
        back = " ".join((back or "").split())
        if front and back:
            lines.append(f"{front}\t{back}")
    return "\n".join(lines) + ("\n" if lines else "")


def parse_anki_lines(raw: str, limit: int = MAX_CARDS) -> list[tuple[str, str]]:
    """Parse the model's card lines ("FRONT ||| BACK"). Only lines
    with exactly two non-empty sides become cards; duplicates are
    dropped. Anything else is ignored, never guessed at."""
    cards: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        if "|||" not in line:
            continue
        front, back = (part.strip() for part in line.split("|||", 1))
        if not front or not back:
            continue
        key = (front + "\n" + back).lower()
        if key in seen:
            continue
        seen.add(key)
        cards.append((front, back))
        if len(cards) >= limit:
            break
    return cards


def generate_anki(source_text: str, topic: str, limit: int = MAX_CARDS) -> list[tuple[str, str]]:
    """Ask the model for flashcards from the material. One fact per
    card, front a short prompt, back one short fact. The model's
    output is parsed strictly — a line that is not a clean card is
    dropped, never repaired by guessing."""
    if (source_text or "").strip():
        prompt = (
            "Make Anki flashcards from the study material below. Rules, "
            "follow them exactly:\n"
            "- One fact per card. Front: a short question or prompt. Back: "
            "the answer — one fact, short.\n"
            "- Only use facts that are in the material. Never invent a fact.\n"
            f"- At most {limit} cards.\n"
            "- Output one card per line, exactly like this: "
            "Front of card ||| Back of card\n"
            "- No numbering, no headings, no extra text, no blank lines.\n\n"
            f"Topic: {topic}\n\nMaterial:\n{source_text[:12000]}"
        )
    else:
        # No material for this topic: cards from general knowledge.
        # The caller says so plainly in the reply — these are not
        # presented as coming from the user's material.
        prompt = (
            "Make Anki flashcards about this topic. Rules, follow them "
            "exactly:\n"
            "- One fact per card. Front: a short question or prompt. Back: "
            "the answer — one fact, short.\n"
            "- Only use facts you are sure of — well-known, settled "
            "facts. Never invent a fact.\n"
            f"- At most {limit} cards.\n"
            "- Output one card per line, exactly like this: "
            "Front of card ||| Back of card\n"
            "- No numbering, no headings, no extra text, no blank lines.\n\n"
            f"Topic: {topic}"
        )
    raw = llm_chat(
        [{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2048,
    )
    return parse_anki_lines(raw, limit=limit)


# ---------------------------------------------------------------- summary


def summarize(source_text: str) -> str:
    """A concise summary in plain words: the key points only, as
    short bullets. Nothing is added that is not in the source."""
    prompt = (
        "Summarize the text below in plain words. Keep it short — "
        "the key points only, as short bullet points. Do not add "
        "anything that is not in the text, and do not pad it out.\n\n"
        f"{source_text[:12000]}"
    )
    return llm_chat(
        [{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1024,
    ).strip()


# ---------------------------------------------------------------- quiz


def quiz_question(topic: str, source_text: str = "") -> tuple[str, str]:
    """One short quiz question about the topic, from the material
    when there is one. Returns (question, expected answer) — the
    answer stays on the server, never shown to the user."""
    if source_text:
        prompt = (
            "Ask ONE short quiz question about this study material. "
            "Reply with exactly two lines, nothing else:\n"
            "Q: <the question>\n"
            "A: <the correct answer, short — a few words>\n\n"
            f"Topic: {topic}\n\nMaterial:\n{source_text[:8000]}"
        )
    else:
        prompt = (
            "Ask ONE short quiz question about this topic. Reply with "
            "exactly two lines, nothing else:\n"
            "Q: <the question>\n"
            "A: <the correct answer, short — a few words>\n\n"
            f"Topic: {topic}"
        )
    raw = llm_chat(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=256,
    )
    question, answer = "", ""
    for line in (raw or "").splitlines():
        line = line.strip()
        if line.lower().startswith("q:"):
            question = line[2:].strip()
        elif line.lower().startswith("a:"):
            answer = line[2:].strip()
    if not question:
        question = raw.strip().splitlines()[0] if raw.strip() else f"What is {topic}?"
    return question, answer


def quiz_judge(question: str, expected: str, user_answer: str) -> tuple[bool, str]:
    """Is the user's answer right? One small call. Returns
    (correct, one-line gentle note). A wrong answer is corrected
    kindly, never mocked; a right one gets a short well done."""
    prompt = (
        "You are marking one quiz answer. Be kind and plain.\n"
        f"Question: {question}\n"
        f"Correct answer: {expected or '(not given — judge by whether the answer is right about the question)'}\n"
        f"User's answer: {user_answer}\n\n"
        "Reply with exactly two lines, nothing else:\n"
        "Correct: yes OR Correct: no\n"
        "Note: <one short line — a warm well done if right, or the "
        "correct answer in plain words if wrong>"
    )
    raw = llm_chat(
        [{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=256,
    )
    correct = False
    note = ""
    for line in (raw or "").splitlines():
        line = line.strip()
        if line.lower().startswith("correct:"):
            correct = "yes" in line.lower()
        elif line.lower().startswith("note:"):
            note = line[5:].strip()
    if not note:
        note = "Well done." if correct else (
            f"Not quite — the answer is: {expected}" if expected else "Not quite."
        )
    return correct, note


def get_active_quiz(db: Session, user_key: str) -> QuizSession | None:
    return (
        db.query(QuizSession)
        .filter_by(user_key=user_key, active=True)
        .order_by(QuizSession.updated_at.desc())
        .first()
    )


def end_quiz(db: Session, quiz: QuizSession) -> None:
    quiz.active = False
    quiz.updated_at = datetime.utcnow()
    db.commit()


# ---------------------------------------------------------------- exam countdown


def days_left(exam_date) -> int:
    """Whole days until the exam (today = 0). Negative when past."""
    if isinstance(exam_date, datetime):
        target = exam_date.date()
    else:
        target = exam_date
    return (target - date.today()).days


def exam_dict(row: ExamCountdown) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "subject": row.subject,
        "exam_date": row.exam_date.date().isoformat() if row.exam_date else None,
        "days_left": days_left(row.exam_date) if row.exam_date else None,
    }
