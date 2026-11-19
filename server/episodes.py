"""Locate episode-specific caption ranges using authored chapter labels."""

import re
import unicodedata

from .videos import Video


_SEPARATOR = r"[~∼〜–—-]"
_KOREAN_EPISODE = re.compile(
    rf"(?<![0-9.:])(?:제\s*)?(?P<first>[0-9]{{1,3}})"
    rf"(?:\s*(?:화|회)?\s*{_SEPARATOR}\s*(?:제\s*)?(?P<last>[0-9]{{1,3}}))?"
    r"\s*(?:화|회)(?!\s*차|면|전|사|담|복|의록)"
)
_KOREAN_EPISODE_LIST = re.compile(
    r"(?<![0-9.:])(?:제\s*)?(?P<items>[0-9]{1,3}"
    r"(?:\s*(?:화|회)?\s*(?:[,·/]|와|과|및)\s*(?:제\s*)?[0-9]{1,3})+)"
    r"\s*(?:화|회)(?!\s*차|면|전|사|담|복|의록)"
)
_ENGLISH_EPISODE = re.compile(
    rf"(?<![A-Za-z0-9])(?:episode|ep)\.?\s*(?P<first>[0-9]{{1,3}})"
    rf"(?:\s*{_SEPARATOR}\s*(?P<last>[0-9]{{1,3}}))?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MAX_EPISODES_PER_CHAPTER = 50
_CHAPTER_OPENING_SECONDS = 20
_CONNECTOR = re.compile(r"^(?:그리고|이어서|이제|다음은|다음으로)\s+")
_PREVIEW = re.compile(r"^\s*(?:의\s*)?[\s:·,–—-]*(?:예고|티저|트레일러|미리보기|선공개)")
_TRANSITION = re.compile(
    r"^(?:부터(?:는)?|로\s*(?:넘어|가)|에서(?:는|부터)?|에선|에는|엔|는|은|도|입니다)"
    r"(?=$|\s|[.,!?：:·–—\-\]])"
)


def _episode_pair(match: re.Match[str]) -> tuple[int, int]:
    first = int(match["first"])
    last = match["last"]
    return first, int(last) if last is not None else first


def episode_numbers(text: str) -> frozenset[int]:
    """Read explicit episode references while preserving numeric ranges."""
    normalized = unicodedata.normalize("NFKC", text)
    numbers = set()
    for pattern in (_KOREAN_EPISODE, _ENGLISH_EPISODE):
        for match in pattern.finditer(normalized):
            first, last = _episode_pair(match)
            if first <= last and last - first < _MAX_EPISODES_PER_CHAPTER:
                numbers.update(range(first, last + 1))
            else:
                numbers.update((first, last))
    for match in _KOREAN_EPISODE_LIST.finditer(normalized):
        numbers.update(int(item) for item in re.findall(r"[0-9]+", match["items"]))
    return frozenset(numbers)


def _leading_episode(text: str) -> re.Match[str] | None:
    return _KOREAN_EPISODE.match(text) or _ENGLISH_EPISODE.match(text)


def _ranged_anchors(video: Video, start: float, end: float, first: int, last: int) -> list[float]:
    """Accept ordered transitions within the chapter, never mentions outside it."""
    anchors = []
    for cue in video.cues:
        if not start <= cue.start < end:
            continue
        text = unicodedata.normalize("NFKC", cue.text).strip()
        opening = _CONNECTOR.match(text)
        if opening:
            text = text[opening.end():]
        match = _leading_episode(text)
        expected = first + len(anchors)
        if not match or _episode_pair(match) != (expected, expected):
            continue
        suffix = text[match.end():]
        if _PREVIEW.match(suffix):
            continue
        if not anchors:
            if cue.start > start + _CHAPTER_OPENING_SECONDS:
                break
        elif not (
            not suffix.strip()
            or suffix.lstrip().startswith((":", "·", "–", "—", "-"))
            or _TRANSITION.match(suffix.lstrip())
        ):
            continue
        anchors.append(cue.start)
        if expected == last:
            return anchors
    return []


def episode_spans(video: Video) -> dict[int, tuple[tuple[float, float], ...]]:
    """Return only ranges backed by chapter labels and unambiguous transitions."""
    spans: dict[int, list[tuple[float, float]]] = {}
    for index, chapter in enumerate(video.chapters):
        title = unicodedata.normalize("NFKC", chapter.title).lstrip(" [(")
        match = _leading_episode(title)
        if not match or _PREVIEW.match(title[match.end():]):
            continue
        start = chapter.start
        end = video.chapters[index + 1].start if index + 1 < len(video.chapters) else video.duration
        first, last = _episode_pair(match)
        if first > last or last - first >= _MAX_EPISODES_PER_CHAPTER:
            continue
        anchors = [start] if first == last else _ranged_anchors(video, start, end, first, last)
        for episode, left, right in zip(range(first, last + 1), anchors, [*anchors[1:], end]):
            spans.setdefault(episode, []).append((left, right))
    return {episode: tuple(segments) for episode, segments in spans.items()}
