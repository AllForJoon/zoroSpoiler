import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from .decision import REVEAL_QUESTION, decision_request
from .local_model import LocalZoroDecisionClient, load_local_decision_client
from .videos import browser_transcript_video


class LocalModelTests(unittest.TestCase):
    def test_local_adapter_uses_typed_requests_and_shared_judgment(self):
        model = Mock()
        model.decide.return_value = {"answers": {"reveals": {"type": "noul", "noul": 0.10}}}
        client = LocalZoroDecisionClient(model)
        self.addCleanup(client.close)
        video = browser_transcript_video("fixture1234", "A movie", [{"start": 0, "end": 5, "text": "A scene"}])
        results, failed = client.judge(video, [{"id": "hello", "text": "Nice review"}])
        self.assertEqual(results, [{"id": "hello", "status": "safe"}])
        self.assertEqual(failed, 0)
        expected = decision_request(
            '{"review_title": "A movie", "viewer_comment": "Nice review"}', "reveals", REVEAL_QUESTION
        )
        model.decide.assert_called_once_with(expected["request"], reasoning=False)
        model.decide.reset_mock()
        with self.assertRaises(TimeoutError):
            client.ask_probability("state", "reveals", REVEAL_QUESTION, deadline=0)
        model.decide.assert_not_called()

    def test_local_snapshot_directory_restores_import_path(self):
        with tempfile.TemporaryDirectory() as root:
            checkpoint = Path(root)
            with self.assertRaisesRegex(ValueError, "official Zoro-1 decision scripts"):
                load_local_decision_client(checkpoint)
            (checkpoint / "decide_request.py").write_text("", encoding="utf-8")
            (checkpoint / "zoro_decide.py").write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).parent))\n"
                "class Decider:\n"
                "    def __init__(self, checkpoint):\n"
                "        self.checkpoint = checkpoint\n",
                encoding="utf-8",
            )
            original_path = sys.path[:]
            client = load_local_decision_client(checkpoint)
            self.addCleanup(client.close)
            self.assertEqual(client.decider.checkpoint, str(checkpoint.resolve()))
            self.assertEqual(sys.path, original_path)
