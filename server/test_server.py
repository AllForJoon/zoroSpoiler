import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .__main__ import SpoilerServer
from .decision import ZoroDecisionClient, candidate_windows, parse_probability, transcript_windows
from .episodes import episode_numbers, episode_spans
from .videos import Video, parse_captions, parse_chapters, save_video, validate_video

VIDEO_ID = "fixture1234"
VIDEO = Video(
    VIDEO_ID,
    "추리 예능",
    780,
    "ko",
    parse_chapters("0:00 시작\n12:40 - 반전"),
    parse_captions("WEBVTT\n\n12:40.000 --> 12:45.000\n범인은 옆집 사람이었습니다.\n"),
)
EPISODE_VIDEO = Video(
    VIDEO_ID,
    "추리 드라마 총정리",
    780,
    "ko",
    parse_chapters("00:00 소개\n01:00 3화 장면\n03:00 9~10화 장면\n04:00 후기"),
    parse_captions(
        "WEBVTT\n\n00:59.000 --> 01:05.000\n작품 소개입니다.\n\n"
        "01:05.000 --> 01:10.000\n범인은 옆집 사람이라는 3화 장면입니다.\n\n"
        "02:55.000 --> 02:58.000\n9화 예고에서 범인이 보입니다.\n\n"
        "03:00.000 --> 03:02.000\n9화에서 주인공이 문을 엽니다.\n\n"
        "03:02.000 --> 03:12.000\n9화의 엔딩에서 범인은 옆집 사람이었다는 사실이 밝혀집니다.\n\n"
        "03:20.000 --> 03:25.000\n10화부터는 새로운 단서가 제시됩니다.\n"
    ),
)


class DecisionTests(unittest.TestCase):
    def test_parse_srt_and_check_eligibility(self):
        self.assertEqual(VIDEO.chapters[-1].title, "반전")
        srt = "1\r\n00:00:05,000 --> 00:00:07,500\r\n<i>단서 &amp; 추리</i>\r\n"
        cue, = parse_captions(srt)
        self.assertEqual((cue.start, cue.end, cue.text), (5, 7.5, "단서 & 추리"))
        validate_video(VIDEO)
        with self.assertRaisesRegex(ValueError, "authored timestamp"):
            validate_video(Video(VIDEO_ID, "추리 예능", 780, "ko", parse_chapters("0:00 시작"), VIDEO.cues))
        with self.assertRaisesRegex(ValueError, "600 to 1200"):
            validate_video(Video(VIDEO_ID, "추리 예능", 1201, "ko", VIDEO.chapters, VIDEO.cues))

    def test_late_caption_has_exact_release_bounds_and_weak_evidence_stays_covered(self):
        client = ZoroDecisionClient("http://127.0.0.1:0", timeout=2)
        self.addCleanup(client.close)
        windows = transcript_windows(VIDEO)
        comment = "연출이 좋았어요.\n범인이 옆집 사람이었네"
        with patch.object(client, "ask_probability", side_effect=[0.90, 0.91, 0.18]) as ask:
            decision = client.judge_one(VIDEO, windows, comment)
        self.assertEqual(decision, {"status": "spoiler", "startSec": 760, "endSec": 765})
        reveal_state, reveal_label, _, deadline = ask.call_args_list[0].args
        self.assertEqual(reveal_label, "reveals")
        self.assertEqual(json.loads(reveal_state)["viewer_comment"], comment)
        grounding_state, grounding_label, _, grounding_deadline = ask.call_args_list[1].args
        self.assertEqual(grounding_label, "grounded")
        self.assertEqual(grounding_deadline, deadline)
        self.assertIn(VIDEO.cues[0].text, grounding_state)
        self.assertEqual(ask.call_args_list[2].args[:2], (grounding_state, "undisclosed"))

        with patch.object(client, "ask_probability", side_effect=[0.93, 0.85]):
            self.assertEqual(client.judge_one(VIDEO, windows, comment), {"status": "uncertain"})
        with patch.object(client, "ask_probability", side_effect=[0.93, 0.98, 0.75]):
            self.assertEqual(client.judge_one(VIDEO, windows, comment), {"status": "uncertain"})

    def test_caption_candidates_prioritize_the_stronger_later_match(self):
        early_cues = parse_captions("WEBVTT\n\n00:10.000 --> 00:13.000\n반전 전개에 관한 이야기입니다.\n")
        video = Video(
            VIDEO_ID, VIDEO.title, VIDEO.duration, VIDEO.language, VIDEO.chapters, early_cues + VIDEO.cues,
        )
        comment = "반전 범인이 옆집 사람이었네"
        windows = candidate_windows(comment, transcript_windows(video))
        self.assertEqual(windows[0].cues, VIDEO.cues)

    def test_episode_scope_requires_ordered_transitions_and_normalizes_episode_references(self):
        self.assertEqual(episode_numbers("제９회 엔딩"), {9})
        self.assertEqual(episode_numbers("9, 10화"), {9, 10})
        self.assertEqual(episode_numbers("밤 9시, 9회차, 9화면"), set())
        self.assertEqual(episode_spans(EPISODE_VIDEO)[9], ((180.0, 200.0),))
        self.assertEqual(episode_spans(EPISODE_VIDEO)[10], ((200.0, 240.0),))
        missing_transition = replace(EPISODE_VIDEO, cues=EPISODE_VIDEO.cues[:-1])
        self.assertNotIn(9, episode_spans(missing_transition))

    def test_caption_only_episode_scope_excludes_preview_and_later_recap(self):
        video = replace(EPISODE_VIDEO, chapters=(), cues=parse_captions(
            "WEBVTT\n\n02:55.000 --> 02:58.000\n9화 예고에서 범인이 보입니다.\n\n"
            "03:00.000 --> 03:04.000\n9화 재회는 정말 대단했죠.\n\n"
            "03:04.000 --> 03:08.000\n엔딩에서 범인이 밝혀졌습니다.\n\n"
            "03:20.000 --> 03:25.000\n10화부터는 새로운 단서가 나옵니다.\n\n"
            "04:10.000 --> 04:15.000\n9화에서는 문을 열었고\n\n"
            "04:15.000 --> 04:19.000\n10화에서는 단서를 찾았습니다.\n"
        ))
        self.assertEqual(episode_spans(video)[9], ((180.0, 200.0),))
        self.assertNotIn(9, episode_spans(replace(video, cues=video.cues[:-3])))

    def test_explicit_episode_uses_only_that_episode_and_keeps_the_same_gates(self):
        client = ZoroDecisionClient("http://127.0.0.1:0", timeout=2)
        self.addCleanup(client.close)
        windows = transcript_windows(EPISODE_VIDEO)
        comment = "9회에서 범인이 옆집 사람이었다는 엔딩이었어요."
        with patch.object(client, "ask_probability", side_effect=[0.60, 0.90, 0.92, 0.20]) as ask:
            result = client.judge_one(EPISODE_VIDEO, windows, comment)
        self.assertEqual(result, {"status": "spoiler", "startSec": 180.0, "endSec": 192.0})
        for call in ask.call_args_list[1:]:
            state = json.loads(call.args[0])
            self.assertEqual(state["명시된 회차"], 9)
            self.assertEqual(state["자막 회차"], 9)
            self.assertIn("9화의 엔딩", state["자막"])
            for unrelated in ("3화", "예고", "10화"):
                self.assertNotIn(unrelated, state["자막"])

        accepted_scores = {"reveals": 0.90, "grounded": 0.92, "undisclosed": 0.20}
        for failed_label, failed_score in (("reveals", 0.79), ("grounded", 0.87), ("undisclosed", 0.46)):
            with self.subTest(failed_label=failed_label):
                scores = {**accepted_scores, failed_label: failed_score}
                with patch.object(client, "ask_probability", side_effect=lambda state, label, *_: scores[label]):
                    self.assertEqual(client.judge_one(EPISODE_VIDEO, windows, comment), {"status": "uncertain"})
        with patch.object(client, "ask_probability", side_effect=[0.60, 0.92, 0.95, 0.54]) as ask:
            self.assertEqual(client.judge_one(EPISODE_VIDEO, windows, comment), {"status": "uncertain"})
            self.assertEqual(ask.call_count, 4)
        with patch.object(client, "ask_probability") as ask:
            for unscoped in ("3화랑 9회 범인을 봤어요.", "15화에서 범인이 밝혀져요."):
                self.assertEqual(client.judge_one(EPISODE_VIDEO, windows, unscoped), {"status": "uncertain"})
            ask.assert_not_called()

    def test_typed_probability_requires_noul_schema_and_numeric_value(self):
        envelope = {"answers": {"grounded": {"type": "noul", "noul": 0.95}}}
        self.assertEqual(parse_probability(envelope, "grounded"), 0.95)
        for bad_value in (True, "0.95", float("nan"), 1.01):
            envelope["answers"]["grounded"]["noul"] = bad_value
            with self.assertRaises(ValueError):
                parse_probability(envelope, "grounded")
        with self.assertRaises(ValueError):
            parse_probability(envelope, "reveals")


class StubZoroHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        size = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(size))
        if self.path != "/v1/decide" or payload["reasoning"] is not False:
            self.send_error(400)
            return
        ((label, question),) = payload["request"]["questions"].items()
        if question["type"] != "noul" or label not in {"reveals", "grounded", "undisclosed"}:
            self.send_error(400)
            return
        is_positive = label != "undisclosed" and "범인" in payload["request"]["state"]
        probability = 0.95 if is_positive else 0.05
        response = json.dumps({"answers": {label: {
            "type": "noul", "noul": probability,
        }}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        data_dir = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        save_video(data_dir, VIDEO)
        fake = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), StubZoroHandler))
        self.start_server(fake)
        self.decider = ZoroDecisionClient(f"http://127.0.0.1:{fake.server_port}/v1/decide", timeout=2)
        self.addCleanup(self.decider.close)
        server = stack.enter_context(SpoilerServer(0, data_dir, self.decider))
        self.start_server(server)
        self.url = f"http://127.0.0.1:{server.server_port}"

    def start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

    def request(self, path, data=None):
        request = (Request(self.url + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
                   if data is not None else self.url + path)
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_register_gate_and_batch_decision(self):
        self.assertEqual(self.request("/health"), {"status": "ok"})
        self.assertEqual(self.request(f"/v1/videos/{VIDEO_ID}"), {"eligible": True, "videoId": VIDEO_ID})
        self.assertEqual(self.request("/v1/videos/missing1234")["reason"], "not_registered")
        response = self.request("/v1/judge", {"videoId": VIDEO_ID, "comments": [
            {"id": "spoil", "text": "범인이 옆집 사람이었네"},
            {"id": "safe", "text": "재밌어요"},
        ]})
        self.assertEqual(response["decisions"], [
            {"id": "spoil", "status": "spoiler", "startSec": 760, "endSec": 765},
            {"id": "safe", "status": "safe"},
        ])

    def test_browser_transcript_judges_unregistered_video_and_returns_status_only(self):
        video_id = "missing1234"
        response = self.request("/v1/judge", {
            "videoId": video_id,
            "title": "Short clip",
            "cues": [{"start": 0, "end": 5, "text": "범인 was the mayor."}],
            "comments": [{"id": "x", "text": "범인 was the mayor"}, {"id": "y", "text": "Great film"}],
        })
        self.assertEqual(response["decisions"], [{"id": "x", "status": "spoiler"}, {"id": "y", "status": "safe"}])
        self.assertFalse(self.request(f"/v1/videos/{video_id}")["eligible"])

    def test_invalid_browser_transcript_never_falls_back_to_registered_captions(self):
        payload = {
            "videoId": VIDEO_ID,
            "title": "A clip",
            "comments": [{"id": "x", "text": "범인"}],
        }
        for bad_cues in ([], [{"start": True, "end": 3, "text": "내용"}],
                         [{"start": 2, "end": float("nan"), "text": "내용"}],
                         [{"start": 3, "end": 4, "text": "첫 줄"}, {"start": 1, "end": 2, "text": "다음"}]):
            with self.subTest(bad_cues=bad_cues), self.assertRaises(HTTPError) as failure:
                self.request("/v1/judge", {**payload, "cues": bad_cues})
            self.assertEqual(failure.exception.code, 400)
            failure.exception.close()

    def test_upstream_failure_does_not_return_a_safe_verdict(self):
        self.decider.url += "/missing"
        with self.assertRaises(HTTPError) as failure:
            self.request("/v1/judge", {"videoId": VIDEO_ID, "comments": [{"id": "x", "text": "재밌어요"}]})
        self.assertEqual(failure.exception.code, 503)
        with failure.exception:
            self.assertEqual(json.load(failure.exception)["decisions"], [{"id": "x", "status": "uncertain"}])


if __name__ == "__main__":
    unittest.main()
