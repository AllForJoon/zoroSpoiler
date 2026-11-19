import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .__main__ import SpoilerServer
from .decision import ZoroDecisionClient, candidate_windows, parse_probability, transcript_windows
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

    def test_upstream_failure_does_not_return_a_safe_verdict(self):
        self.decider.url += "/missing"
        with self.assertRaises(HTTPError) as failure:
            self.request("/v1/judge", {"videoId": VIDEO_ID, "comments": [{"id": "x", "text": "재밌어요"}]})
        self.assertEqual(failure.exception.code, 503)
        with failure.exception:
            self.assertEqual(json.load(failure.exception)["decisions"], [{"id": "x", "status": "uncertain"}])


if __name__ == "__main__":
    unittest.main()
