const API_ORIGIN = "http://127.0.0.1:8876";
const VIDEO_ID = /^[\w-]{11}$/;
const MAX_BATCH = 8;
const MAX_COMMENT_LENGTH = 5_000;
const METADATA_TIMEOUT_MS = 4_000;
const JUDGE_TIMEOUT_MS = 27_000;

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!sender.url || new URL(sender.url).origin !== "https://www.youtube.com") {
    return false;
  }
  if (
    !message ||
    typeof message.videoId !== "string" ||
    !VIDEO_ID.test(message.videoId) ||
    !["zoro-video", "zoro-judge"].includes(message.type)
  ) {
    return false;
  }

  request(message).then(
    (data) => respond({ ok: true, data }),
    () => respond({ ok: false }),
  );
  return true;
});

async function request({ type, videoId, comments }) {
  if (type === "zoro-video") {
    const signal = AbortSignal.timeout(METADATA_TIMEOUT_MS);
    return readJson(await fetch(`${API_ORIGIN}/v1/videos/${videoId}`, { signal }));
  }

  if (
    !Array.isArray(comments) ||
    comments.length < 1 ||
    comments.length > MAX_BATCH ||
    !comments.every(
      (comment) =>
        comment &&
        typeof comment.id === "string" &&
        /^c\d+$/.test(comment.id) &&
        typeof comment.text === "string" &&
        comment.text.length > 0 &&
        comment.text.length <= MAX_COMMENT_LENGTH,
    )
  ) {
    throw new TypeError("Invalid comment batch");
  }

  return readJson(
    await fetch(`${API_ORIGIN}/v1/judge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ videoId, comments }),
      signal: AbortSignal.timeout(JUDGE_TIMEOUT_MS),
    }),
  );
}

async function readJson(response) {
  if (!response.ok) {
    throw new Error(`Judge API returned ${response.status}`);
  }
  return response.json();
}
