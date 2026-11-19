"""Load video captions and optional chapter boundaries."""

import html
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
TIMESTAMP_TEXT = r"(?:[0-9]{1,3}:)?[0-9]{1,3}:[0-5][0-9]"
CAPTION_TIMES = re.compile(
    rf"^({TIMESTAMP_TEXT}[.,][0-9]{{3}})\s*-->\s*"
    rf"({TIMESTAMP_TEXT}[.,][0-9]{{3}})(?:\s+.*)?$"
)
CHAPTER_LINE = re.compile(rf"^\s*({TIMESTAMP_TEXT})(?:[ \t]+(.*?))?\s*$")
HANGUL = re.compile(r"[가-힣]")
MIN_DURATION = 600
MAX_DURATION = 1200
MAX_TRANSCRIPT_SECONDS = 12 * 60 * 60
MAX_TRANSCRIPT_CUES = 12_000
MAX_CUE_TEXT_LENGTH = 1_000
MAX_TRANSCRIPT_CHAPTERS = 400


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Chapter:
    start: float
    title: str


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    duration: float
    language: str
    chapters: tuple[Chapter, ...]
    cues: tuple[Cue, ...]


def browser_transcript_video(video_id: str, title: object, cues: object, chapters: object = ()) -> Video:
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError("Video title must contain 1 to 300 characters")
    title.encode("utf-8")
    if not isinstance(cues, list) or not 1 <= len(cues) <= MAX_TRANSCRIPT_CUES:
        raise ValueError("Supply a nonempty timed transcript")

    parsed_cues = []
    previous_start = -1
    for cue in cues:
        if not isinstance(cue, dict):
            raise ValueError("Each caption cue must be an object")
        start, end, text = cue.get("start"), cue.get("end"), cue.get("text")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not 0 <= start < end <= MAX_TRANSCRIPT_SECONDS or start < previous_start):
            raise ValueError("Caption cues must have valid, chronological times in seconds")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_CUE_TEXT_LENGTH:
            raise ValueError("Caption cues require text")
        text.encode("utf-8")
        parsed_cues.append(Cue(start, end, " ".join(text.split())))
        previous_start = start

    duration = max(cue.end for cue in parsed_cues)
    if not isinstance(chapters, (list, tuple)) or len(chapters) > MAX_TRANSCRIPT_CHAPTERS:
        raise ValueError("Invalid transcript chapters")
    parsed_chapters = []
    previous_start = -1
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Each chapter must be an object")
        start, chapter_title = chapter.get("start"), chapter.get("title")
        if (type(start) not in (int, float) or not 0 <= start <= MAX_TRANSCRIPT_SECONDS
                or start <= previous_start or not isinstance(chapter_title, str) or len(chapter_title) > 300):
            raise ValueError("Chapters must have distinct, chronological times and titles")
        chapter_title.encode("utf-8")
        if start < duration:
            parsed_chapters.append(Chapter(start, chapter_title.strip()))
        previous_start = start

    return Video(video_id, title.strip(), duration, "und", tuple(parsed_chapters), tuple(parsed_cues))


def timestamp_seconds(timestamp: str) -> float:
    parts = timestamp.replace(",", ".").split(":")
    if len(parts) == 3 and int(parts[1]) > 59:
        raise ValueError(f"Invalid timestamp: {timestamp}")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def parse_captions(contents: str) -> tuple[Cue, ...]:
    blocks = re.split(r"\n[ \t]*\n", contents.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff"))
    cues = []
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timing_index = 0 if "-->" in lines[0] else 1
        if len(lines) <= timing_index:
            raise ValueError("Malformed VTT/SRT cue")
        times = CAPTION_TIMES.fullmatch(lines[timing_index].strip())
        if not times:
            raise ValueError(f"Malformed VTT/SRT timing: {lines[timing_index]}")
        start, end = map(timestamp_seconds, times.groups())
        cue_text = html.unescape(re.sub(r"<[^>]*>", "", " ".join(lines[timing_index + 1:])))
        cue_text = " ".join(cue_text.split())
        if cue_text:
            cues.append(Cue(start, end, cue_text))
    return tuple(sorted(cues, key=lambda cue: cue.start))


def parse_chapters(contents: str) -> tuple[Chapter, ...]:
    chapters = []
    for line in contents.splitlines():
        if not line.strip():
            continue
        match = CHAPTER_LINE.fullmatch(line)
        if not match:
            raise ValueError(f"Expected a timestamp followed by its title: {line}")
        start, title = match.groups()
        chapters.append(Chapter(timestamp_seconds(start), (title or "").lstrip("-–—: ")))
    return tuple(sorted(chapters, key=lambda chapter: chapter.start))


def validate_video(video: Video) -> None:
    if not isinstance(video.video_id, str) or not VIDEO_ID.fullmatch(video.video_id):
        raise ValueError("Expected an 11-character YouTube video ID")
    if not isinstance(video.title, str) or not video.title.strip() or len(video.title) > 300:
        raise ValueError("Video title must contain 1 to 300 characters")
    if not isinstance(video.language, str) or not re.fullmatch(r"ko(?:-[A-Za-z0-9]+)*", video.language):
        raise ValueError("Only Korean videos and Korean captions are supported")
    if not MIN_DURATION <= video.duration <= MAX_DURATION:
        raise ValueError("Video duration must be 600 to 1200 seconds, inclusive")
    if not video.chapters or not any(chapter.start > 0 for chapter in video.chapters):
        raise ValueError("Supply at least one authored timestamp after 0:00")
    previous = -1.0
    for chapter in video.chapters:
        if not previous < chapter.start < video.duration or not isinstance(chapter.title, str):
            raise ValueError("Authored timestamps must be distinct and within the video")
        previous = chapter.start
    if not video.cues or not any(isinstance(cue.text, str) and HANGUL.search(cue.text) for cue in video.cues):
        raise ValueError("Supply a timed caption track containing Korean dialogue")
    previous = -1.0
    for cue in video.cues:
        if not 0 <= cue.start < cue.end <= video.duration or cue.start < previous:
            raise ValueError("Caption cues must be chronologically ordered and within the video")
        if not isinstance(cue.text, str) or not cue.text.strip():
            raise ValueError("Caption cues require text")
        previous = cue.start


def load_video(data_dir: Path, video_id: str) -> Video:
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Invalid video ID")
    data = json.loads((data_dir / f"{video_id}.json").read_text(encoding="utf-8"))
    video = Video(
        video_id=data["video_id"],
        title=data["title"],
        duration=data["duration"],
        language=data["language"],
        chapters=tuple(Chapter(**item) for item in data["chapters"]),
        cues=tuple(Cue(**item) for item in data["cues"]),
    )
    validate_video(video)
    if video.video_id != video_id:
        raise ValueError("Registered video ID does not match its filename")
    return video


def save_video(data_dir: Path, video: Video) -> None:
    validate_video(video)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{video.video_id}.json"
    temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=data_dir, delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            json.dump(asdict(video), temporary, ensure_ascii=False, allow_nan=False)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
