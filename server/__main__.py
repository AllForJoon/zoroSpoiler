"""Command line entry point and private browser-facing API."""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .decision import DecisionClient, ZoroDecisionClient
from .videos import VIDEO_ID, Video, browser_transcript_video, load_video, parse_captions, parse_chapters, save_video

DEFAULT_PORT = 8876
LOCAL_MODEL_PORT = 8878
DEFAULT_URL = "http://127.0.0.1:30001/v1/decide"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_COMMENTS = 8


def validate_request(payload: object) -> tuple[str, list[dict]]:
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    video_id = payload.get("videoId")
    comments = payload.get("comments")
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Invalid videoId")
    if not isinstance(comments, list) or not 1 <= len(comments) <= MAX_COMMENTS:
        raise ValueError(f"Supply 1 to {MAX_COMMENTS} comments")
    ids = set()
    for comment in comments:
        if not isinstance(comment, dict):
            raise ValueError("Each comment must be an object")
        identifier = comment.get("id")
        text = comment.get("text")
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128 or identifier in ids:
            raise ValueError("Each comment requires a unique ID of 1 to 128 characters")
        if not isinstance(text, str):
            raise ValueError("Each comment requires a text string")
        ids.add(identifier)
    return video_id, comments


class SpoilerServer(ThreadingHTTPServer):
    def __init__(self, port: int, data_dir: Path, decider: DecisionClient):
        super().__init__(("127.0.0.1", port), Handler)
        self.data_dir = data_dir
        self.decider = decider

    def lookup(self, video_id: str) -> tuple[Video | None, str | None]:
        try:
            return load_video(self.data_dir, video_id), None
        except FileNotFoundError:
            return None, "not_registered"
        except (OSError, TypeError, KeyError, ValueError):
            return None, "invalid_video_data"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        # The browser is the only caller and its viewing history is not logged.
        pass

    def send_json(self, status: int, payload: dict) -> None:
        response = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def is_loopback_request(self) -> bool:
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") in allowed:
            return True
        self.send_json(403, {"error": "invalid_host"})
        return False

    def do_GET(self) -> None:
        if not self.is_loopback_request():
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        if path.startswith("/v1/videos/"):
            video_id = path.removeprefix("/v1/videos/")
            if not VIDEO_ID.fullmatch(video_id):
                self.send_json(400, {"error": "invalid_video_id"})
                return
            video, reason = self.server.lookup(video_id)
            if reason:
                self.send_json(200, {"videoId": video_id, "eligible": False, "reason": reason})
            else:
                self.send_json(200, {"videoId": video_id, "eligible": True})
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self.is_loopback_request():
            return
        if self.path != "/v1/judge":
            self.send_json(404, {"error": "not_found"})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "expected_application_json"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid_content_length"})
            return
        if not 1 <= size <= MAX_REQUEST_BYTES:
            self.send_json(413, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(size))
            video_id, comments = validate_request(payload)
            from_browser = "cues" in payload
            video = browser_transcript_video(
                video_id, payload.get("title"), payload["cues"], payload.get("chapters", ())
            ) if from_browser else None
        except (ValueError, RecursionError):
            self.send_json(400, {"error": "invalid_request"})
            return
        if video is None:
            video, reason = self.server.lookup(video_id)
            if video is None:
                self.send_json(404, {"error": reason})
                return
        decisions, failed = self.server.decider.judge(video, comments)
        if from_browser:
            decisions = [{"id": verdict["id"], "status": verdict["status"]} for verdict in decisions]
        if failed == len(comments):
            self.send_json(503, {"error": "decision_service_unavailable", "decisions": decisions})
            return
        self.send_json(200, {"decisions": decisions})


def parse_args() -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("ZORO_DATA_DIR", "data/videos"))
    )
    parser = argparse.ArgumentParser(description="Use Zoro-1 to judge YouTube spoiler comments.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    register = subcommands.add_parser("register", parents=[common], help="Register a Korean video.")
    register.add_argument("--video-id", required=True)
    register.add_argument("--title", required=True)
    register.add_argument("--duration", required=True, type=float, help="Video duration in seconds.")
    register.add_argument("--language", default="ko")
    register.add_argument("--captions", required=True, type=Path, help="Timed Korean SRT or VTT file.")
    register.add_argument("--timestamps", required=True, type=Path, help="Text file with one timestamp per line.")
    serve = subcommands.add_parser("serve", parents=[common], help="Start the private loopback API.")
    serve.add_argument("--port", type=int, help=f"Loopback port (default {DEFAULT_PORT}, local model {LOCAL_MODEL_PORT}).")
    model = serve.add_mutually_exclusive_group()
    model.add_argument("--zoro-url", help="Use an existing Zoro-1 typed decision HTTP endpoint.")
    model.add_argument("--local-model", action="store_true", help="Load the official Zoro-1 checkpoint in this process.")
    serve.add_argument("--checkpoint", type=Path, help="Local Zoro-1 snapshot directory for --local-model.")
    args = parser.parse_args()
    if args.command == "serve" and args.checkpoint is not None and not args.local_model:
        serve.error("--checkpoint requires --local-model")
    return args


def main() -> int:
    args = parse_args()
    if args.command == "register":
        try:
            video = Video(
                video_id=args.video_id,
                title=args.title,
                duration=args.duration,
                language=args.language.lower(),
                chapters=parse_chapters(args.timestamps.read_text(encoding="utf-8-sig")),
                cues=parse_captions(args.captions.read_text(encoding="utf-8-sig")),
            )
            save_video(args.data_dir, video)
        except (OSError, ValueError) as error:
            print(f"Could not register the video: {error}", file=sys.stderr)
            return 1
        print(f"Registered {video.video_id} with {len(video.cues)} cues.")
        return 0

    if args.local_model:
        from .local_model import load_local_decision_client

        print("Loading local Zoro-1 checkpoint…", flush=True)
        try:
            decider = load_local_decision_client(args.checkpoint)
        except (OSError, ValueError, RuntimeError) as error:
            print(f"Could not load local Zoro-1: {error}", file=sys.stderr)
            return 1
    else:
        url = args.zoro_url or os.environ.get("ZORO_DECIDE_URL", DEFAULT_URL)
        decider = ZoroDecisionClient(url, timeout=12)
    port = args.port if args.port is not None else LOCAL_MODEL_PORT if args.local_model else DEFAULT_PORT
    try:
        with SpoilerServer(port, args.data_dir, decider) as server:
            mode = "local checkpoint" if args.local_model else "Zoro-1 HTTP endpoint"
            print(f"ZoroSpoiler listening at http://127.0.0.1:{server.server_port} ({mode})", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        decider.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
