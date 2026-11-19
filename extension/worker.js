const API_ORIGINS = {
  remote: "http://127.0.0.1:8876",
  local: "http://127.0.0.1:8878",
};
const VIDEO_ID = /^[\w-]{11}$/;
const MAX_BATCH = 8;
const MAX_COMMENT_LENGTH = 5_000;
const MAX_TRANSCRIPT_CUES = 12_000;
const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const JUDGE_TIMEOUT_MS = 27_000;

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!sender.url || new URL(sender.url).origin !== "https://www.youtube.com") {
    return false;
  }
  if (message?.type !== "zoro-judge" || typeof message.videoId !== "string" || !VIDEO_ID.test(message.videoId)) {
    return false;
  }

  judge(message).then(
    (data) => respond({ ok: true, data }),
    () => respond({ ok: false }),
  );
  return true;
});

async function judge({ videoId, title, cues, chapters, comments }) {
  if (
    typeof title !== "string" || !title.trim() || [...title].length > 300 ||
    !Array.isArray(cues) || !cues.length || cues.length > MAX_TRANSCRIPT_CUES ||
    !Array.isArray(comments) || comments.length < 1 || comments.length > MAX_BATCH ||
    !comments.every((comment) =>
      comment &&
      typeof comment.id === "string" && /^c\d+$/.test(comment.id) &&
      typeof comment.text === "string" && comment.text.length > 0 && comment.text.length <= MAX_COMMENT_LENGTH
    )
  ) {
    throw new TypeError("Invalid caption or comment batch");
  }
  const body = JSON.stringify({ videoId, title, cues, chapters, comments });
  if (new TextEncoder().encode(body).length > MAX_REQUEST_BYTES) {
    throw new RangeError("Caption and comment batch is too large");
  }

  const { modelSource } = await chrome.storage.local.get("modelSource");
  const origin = modelSource === "local" ? API_ORIGINS.local : API_ORIGINS.remote;
  const response = await fetch(`${origin}/v1/judge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    signal: AbortSignal.timeout(JUDGE_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`Judge API returned ${response.status}`);
  }
  return response.json();
}
