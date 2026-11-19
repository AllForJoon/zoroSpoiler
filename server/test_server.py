import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .__main__ import SpoilerServer
from .decision import ZoroDecisionClient, parse_answer, request_for, transcript_windows
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

    def test_late_caption_is_grounded_and_weak_response_is_covered(self):
        body, scenes = request_for(VIDEO, "범인이 옆집 사람이었네", transcript_windows(VIDEO))
        self.assertIn("범인은 옆집 사람이었습니다", body["request"]["state"])
        self.assertEqual(set(scenes), {"scene_720"})
        envelope = {"answers": {"spoiler": {"type": "choice", "probabilities": {
            "safe": 0.01, "uncertain": 0.09, "scene_720": 0.90,
        }}}}
        self.assertEqual(parse_answer(envelope, scenes), {"status": "spoiler", "startSec": 720, "endSec": 780})
        envelope["answers"]["spoiler"]["probabilities"] = {"safe": 0.75, "uncertain": 0.20, "scene_720": 0.05}
        self.assertEqual(parse_answer(envelope, scenes), {"status": "uncertain"})
        with self.assertRaisesRegex(ValueError, "choice response"):
            parse_answer(envelope, {})


class StubZoroHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        size = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(size))
        if self.path != "/v1/decide" or payload["reasoning"] is not False:
            self.send_error(400)
            return
        question = payload["request"]["questions"]["spoiler"]
        labels = question["criteria"]
        winner = next((label for label in labels if label.startswith("scene_")), "safe")
        other = 0.05 / (len(labels) - 1)
        response = json.dumps({"answers": {"spoiler": {
            "type": "choice",
            "probabilities": {label: 0.95 if label == winner else other for label in labels},
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
            {"id": "spoil", "status": "spoiler", "startSec": 720, "endSec": 780},
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
