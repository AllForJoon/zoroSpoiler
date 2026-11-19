"""Optional in-process runner for the official Zoro-1 decision checkpoint."""

import importlib.util
import sys
import time
from pathlib import Path

from .decision import DecisionClient, decision_request, parse_probability

MODEL_ID = "trillionlabs/Zoro-1"
MODEL_REVISION = "bf1b76b9b4df03b05fcbb73444e1cd4db0f566da"
LOCAL_TIMEOUT_SECONDS = 24
INSTALL_COMMAND = "python3 -m pip install -r server/requirements-local.txt"


class LocalZoroDecisionClient(DecisionClient):
    def __init__(self, decider, timeout: float = LOCAL_TIMEOUT_SECONDS):
        # One worker owns the loaded model, including across concurrent HTTP requests.
        super().__init__(timeout, workers=1)
        self.decider = decider

    def ask_probability(self, state: str, label: str, instructions: str, deadline: float) -> float:
        if time.monotonic() >= deadline:
            raise TimeoutError("Local Zoro-1 decision exceeded its time limit")
        body = decision_request(state, label, instructions)
        try:
            result = self.decider.decide(body["request"], reasoning=body["reasoning"])
        except RuntimeError as error:
            raise ValueError("Local Zoro-1 could not complete the decision") from error
        if time.monotonic() >= deadline:
            raise TimeoutError("Local Zoro-1 decision exceeded its time limit")
        return parse_probability(result, label)


def load_local_decision_client(checkpoint: Path | None) -> LocalZoroDecisionClient:
    if checkpoint is None:
        if any(importlib.util.find_spec(name) is None for name in ("torch", "transformers", "PIL")):
            raise ValueError(f"Install local model dependencies with `{INSTALL_COMMAND}`.")
        try:
            from huggingface_hub import snapshot_download
            from httpx import HTTPError
        except ImportError as error:
            raise ValueError(f"Install local model dependencies with `{INSTALL_COMMAND}`.") from error
        try:
            checkpoint = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION))
        except (OSError, HTTPError) as error:
            raise ValueError(
                f"Could not download {MODEL_ID}. Request access on Hugging Face and run `hf auth login`, "
                "or supply an existing snapshot with `--checkpoint`."
            ) from error

    script = checkpoint / "zoro_decide.py"
    if not script.is_file() or not (checkpoint / "decide_request.py").is_file():
        raise ValueError("The checkpoint directory must contain the official Zoro-1 decision scripts and weights.")
    spec = importlib.util.spec_from_file_location("_zoro_checkpoint_decider", script)
    module = importlib.util.module_from_spec(spec)
    original_path = sys.path[:]
    try:
        # The upstream script temporarily adds its own directory to import its companion module.
        spec.loader.exec_module(module)
        model = module.Decider(str(checkpoint.resolve()))
    except ImportError as error:
        raise ValueError(f"Install local model dependencies with `{INSTALL_COMMAND}`. {error}") from error
    finally:
        sys.path[:] = original_path
    return LocalZoroDecisionClient(model)
