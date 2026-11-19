"""Register Korean caption tracks and editorial timestamps supplied by the operator."""

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
