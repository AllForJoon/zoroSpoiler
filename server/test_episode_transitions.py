import unittest
from dataclasses import replace

from .decision import episode_windows
from .episodes import episode_spans
from .videos import Chapter, Cue, Video


class MixedTranscriptCueTest(unittest.TestCase):
    def test_explicit_mid_cue_transition_keeps_mixed_text_out_of_both_episodes(self):
        mixed = Cue(513, 521, "아직 이야기가 남았죠 10화부터는 새로운 사건이 생겼습니다")
        video = Video("CaptionDemo", "시리즈 리뷰", 680, "ko", (
            Chapter(479, "9~11화 · 재회"), Chapter(596, "12화 · 마지막"),
        ), (
            Cue(479, 488, "9화 재회에서 민재가 걸어갔습니다"),
            Cue(488, 497, "음악이 시작됐습니다"),
            mixed,
            Cue(521, 533, "유진과 형사가 새 단서를 발견합니다"),
            Cue(554, 564, "11화도 사건이 이어집니다"),
        ))
        spans = episode_spans(video)
        self.assertEqual(spans[9], ((479, 513),))
        self.assertEqual(spans[10], ((521, 554),))
        self.assertEqual(spans[11], ((554, 596),))
        for episode in (9, 10):
            self.assertTrue(all(mixed not in window.cues for window in episode_windows(video, spans[episode][0])))

        preview = Cue(513, 521, "아직 이야기가 남았죠 10화 예고에서 새 사건이 생깁니다")
        self.assertNotIn(9, episode_spans(replace(video, cues=(*video.cues[:2], preview, *video.cues[3:]))))

    def test_coarse_native_captions_use_complete_adjacent_cues(self):
        video = Video("CaptionDemo", "시리즈 리뷰", 680, "ko", (), (
            Cue(479, 488, "9화 재회에서 민재가 걸어갔습니다"),
            Cue(488, 497, "음악이 시작됐습니다"),
            Cue(497, 505, "음악의 배경을 설명합니다"),
            Cue(513, 521, "10화부터는 새로운 사건이 생겼습니다"),
        ))
        windows = episode_windows(video, (479, 513))
        self.assertEqual([(window.start, window.end) for window in windows], [(479, 497), (488, 505)])
        self.assertEqual(windows[0].cues, video.cues[:2])
        self.assertTrue(all(window.grouped_captions for window in windows))
