"""Locate episode-specific caption ranges from chapters and transcript transitions."""

import re
import unicodedata
from itertools import groupby

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
_CAPTION_OPENING_SECONDS = 180
_CONNECTOR = re.compile(r"^(?:그리고|이어서|이제|다음은|다음으로|그런데)\s+")
_PREVIEW = re.compile(r"^\s*(?:의\s*)?[\s:·,–—-]*(?:예고|티저|트레일러|미리보기|선공개)")
_TRANSITION = re.compile(
    r"^(?:부터(?:는)?|로\s*(?:넘어|가)|에서(?:는|부터)?|에선|에는|엔|는|은|도|입니다)"
    r"(?=$|\s|[.,!?：:·–—\-\]])"
)
_EXPLICIT_TRANSITION = re.compile(
    r"^(?:[:：·–—-]|부터(?:는)?(?=\s|$)|로\s*(?:넘어|가)|가\s*되면서)"
)
_MID_CUE_TRANSITION = re.compile(r"^(?:부터(?:는)?(?=\s|$)|로\s*(?:넘어|가))")


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


def _caption_opening(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip()
    return _CONNECTOR.sub("", text, count=1)


def _mid_cue_transition(text: str, expected: int) -> bool:
    return any(
        match.start() > 0 and text[match.start() - 1].isspace()
        and _episode_pair(match) == (expected, expected)
        and _MID_CUE_TRANSITION.match(text[match.end():].lstrip())
        for match in _KOREAN_EPISODE.finditer(text)
    )


def _ranged_anchors(video: Video, start: float, end: float, first: int, last: int) -> list[tuple[float, float]]:
    """Accept ordered transitions within the chapter, never mentions outside it."""
    anchors = []
    for cue in video.cues:
        if not (anchors[-1][1] if anchors else start) <= cue.start < end:
            continue
        text = _caption_opening(cue.text)
        match = _leading_episode(text)
        expected = first + len(anchors)
        if match and _episode_pair(match) == (expected, expected):
            suffix = text[match.end():].lstrip()
            if _PREVIEW.match(suffix):
                continue
            if not anchors:
                if cue.start > start + _CHAPTER_OPENING_SECONDS:
                    break
            elif suffix and not (suffix.startswith((":", "·", "–", "—", "-")) or _TRANSITION.match(suffix)):
                continue
            anchors.append((cue.start, cue.start))
        elif anchors and cue.end < end and _mid_cue_transition(text, expected):
            # YouTube can group text across the transition without giving its exact time.
            # Exclude the mixed cue from both episodes instead of guessing that time.
            anchors.append((cue.start, cue.end))
        else:
            continue
        if expected == last:
            return anchors
    return []


def _caption_spans(video: Video) -> dict[int, tuple[tuple[float, float], ...]]:
    headings = []
    for cue in video.cues:
        text = _caption_opening(cue.text)
        match = _leading_episode(text)
        if not match:
            continue
        first, last = _episode_pair(match)
        suffix = text[match.end():]
        if first != last or _PREVIEW.match(suffix):
            continue
        explicit = bool(_EXPLICIT_TRANSITION.match(suffix.lstrip()))
        if explicit or not suffix or suffix[0].isspace() or _TRANSITION.match(suffix):
            headings.append((first, cue.start, explicit))

    # Without chapters, require a consecutive episode boundary and an explicit introduction.
    groups = [(episode, list(entries)) for episode, entries in groupby(headings, key=lambda entry: entry[0])]
    spans: dict[int, list[tuple[float, float]]] = {}
    for (episode, entries), (next_episode, following) in zip(groups, groups[1:]):
        if next_episode != episode + 1:
            continue
        explicit_starts = [start for _, start, explicit in entries if explicit]
        if len(explicit_starts) == 1:
            start = explicit_starts[0]
        elif not explicit_starts and len(entries) == 1 and following[0][2]:
            start = entries[0][1]
            if following[0][1] - start > _CAPTION_OPENING_SECONDS:
                continue
        else:
            continue
        end = following[0][1]
        if start < end:
            spans.setdefault(episode, []).append((start, end))
    return {episode: tuple(segments) for episode, segments in spans.items()}


def episode_spans(video: Video) -> dict[int, tuple[tuple[float, float], ...]]:
    """Return episode ranges supported by chapter labels or caption boundaries."""
    spans: dict[int, list[tuple[float, float]]] = {}
    has_episode_chapters = False
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
        has_episode_chapters = True
        anchors = [(start, start)] if first == last else _ranged_anchors(video, start, end, first, last)
        ends = [anchor[0] for anchor in anchors[1:]] + [end]
        for episode, (_, left), right in zip(range(first, last + 1), anchors, ends):
            if left < right:
                spans.setdefault(episode, []).append((left, right))
    if not has_episode_chapters:
        return _caption_spans(video)
    return {episode: tuple(segments) for episode, segments in spans.items()}
