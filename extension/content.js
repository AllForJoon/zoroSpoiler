(() => {
  const MIN_COMMENT_COUNT = 30;
  const MIN_DURATION_SECONDS = 600;
  const MAX_DURATION_SECONDS = 1_200;
  const MAX_BATCH = 8;
  const MAX_COMMENT_LENGTH = 5_000;
  const COMMENT_BODY = '[id="content-text"]';
  const WATCH_ROOT = "ytd-watch-flexy:not([hidden])";
  const VIDEO_ID = /^[\w-]{11}$/;
  const RANGE_TOLERANCE_SECONDS = 0.05;
  const COUNT_MULTIPLIERS = { k: 1_000, m: 1_000_000, b: 1_000_000_000, 천: 1_000, 만: 10_000, 억: 100_000_000 };

  let page;
  let navigating = false;
  let navigationTimeout;

  function videoIdAt(url = new URL(location.href)) {
    const id = url.searchParams.get("v");
    return url.pathname === "/watch" && id && VIDEO_ID.test(id) ? id : null;
  }

  function setProtection(mode) {
    const root = document.documentElement;
    if (root && root.dataset.zoroProtection !== mode) {
      root.dataset.zoroProtection = mode;
    }
  }

  function disposeComment(entry) {
    delete entry.body.dataset.zoroVisible;
    entry.button.remove();
  }

  function openPage(videoId) {
    page?.notice?.remove();
    for (const entry of page?.comments.values() ?? []) {
      disposeComment(entry);
    }
    const opened = {
      videoId,
      metadata: "pending",
      comments: new Map(),
      judgments: new Map(),
      nextCommentId: 1,
      inFlight: false,
      duration: null,
      mode: videoId ? "pending" : "off",
    };
    page = opened;
    setProtection(opened.mode);

    if (videoId) {
      loadVideoInfo(opened);
    }
  }

  async function loadVideoInfo(opened) {
    if (page === opened && opened.metadata !== "pending") {
      opened.metadata = "pending";
      syncPage();
    }
    try {
      const response = await chrome.runtime.sendMessage({ type: "zoro-video", videoId: opened.videoId });
      const data = response?.data;
      if (!response?.ok || data?.videoId !== opened.videoId || typeof data?.eligible !== "boolean") {
        opened.metadata = "error";
      } else {
        opened.metadata = data.eligible ? "eligible" : "ineligible";
      }
    } catch {
      opened.metadata = "error";
    }
    if (page === opened) {
      syncPage();
    }
  }

  function parseCommentCount(text) {
    const match = text.match(/(\d+(?:[.,\u00a0\u202f ]\d+)*)\s*([KMB천만억])?/i);
    if (!match) {
      return null;
    }
    const [, raw, suffix] = match;
    const normalized = raw.replace(/[\u00a0\u202f ]/g, "");
    if (!suffix) {
      return Number(normalized.replace(/[.,]/g, ""));
    }
    const number = /,\d{1,2}$/.test(normalized) && !normalized.includes(".")
      ? normalized.replace(",", ".")
      : normalized.replace(/,/g, "");
    return Math.floor(Number(number) * COUNT_MULTIPLIERS[suffix.toLowerCase()]);
  }

  function countComments(commentsRoot) {
    if (!commentsRoot) {
      return null;
    }
    const heading = commentsRoot.querySelector("ytd-comments-header-renderer [id='count']");
    const count = parseCommentCount(heading?.textContent ?? "");
    if (Number.isFinite(count)) {
      return count;
    }
    const renderedThreads = commentsRoot.querySelectorAll("ytd-comment-thread-renderer").length;
    return renderedThreads >= MIN_COMMENT_COUNT ? renderedThreads : null;
  }

  function isAdPlaying(video) {
    return Boolean(video?.closest(".ad-showing"));
  }

  function pageMode(opened, commentCount) {
    if (
      !opened.videoId ||
      opened.metadata === "ineligible" ||
      opened.metadata === "error" ||
      (opened.duration !== null &&
        (opened.duration < MIN_DURATION_SECONDS || opened.duration > MAX_DURATION_SECONDS)) ||
      (commentCount !== null && commentCount < MIN_COMMENT_COUNT)
    ) {
      return "off";
    }
    if (opened.metadata === "eligible" && opened.duration !== null && commentCount !== null) {
      return "active";
    }
    return "pending";
  }

  function syncPage() {
    const videoId = videoIdAt();
    if (videoId !== page?.videoId) {
      navigating = false;
      openPage(videoId);
    }
    if (!page) {
      openPage(videoId);
    }
    if (navigating || !videoId) {
      return;
    }

    const roots = [...document.querySelectorAll(WATCH_ROOT)];
    const watchRoot = roots.find((root) => root.getAttribute("video-id") === videoId)
      ?? roots.find((root) => !root.hasAttribute("video-id"));
    const video = watchRoot?.querySelector("#movie_player video.html5-main-video, #movie_player video");
    const playingAd = isAdPlaying(video);
    if (video && !playingAd && Number.isFinite(video.duration) && video.duration > 0) {
      page.duration = video.duration;
    }
    page.video = video;

    const commentsRoot = watchRoot?.querySelector("ytd-comments");
    const commentCount = countComments(commentsRoot);
    page.mode = pageMode(page, commentCount);
    setProtection(page.mode);
    showConnectionNotice(page, commentsRoot, commentCount);

    if (page.mode === "off") {
      for (const entry of page.comments.values()) {
        disposeComment(entry);
      }
      page.comments.clear();
      return;
    }
    scanComments(page, commentsRoot);
    renderComments(page);
    if (page.mode === "active") {
      submitNextBatch(page);
    }
  }

  function showConnectionNotice(opened, commentsRoot, commentCount) {
    if (
      opened.metadata !== "error" ||
      !commentsRoot ||
      opened.duration === null ||
      opened.duration < MIN_DURATION_SECONDS ||
      opened.duration > MAX_DURATION_SECONDS ||
      commentCount === null ||
      commentCount < MIN_COMMENT_COUNT
    ) {
      opened.notice?.remove();
      return;
    }
    if (!opened.notice) {
      const notice = document.createElement("div");
      notice.className = "zoro-connection-notice";
      const message = document.createElement("span");
      message.setAttribute("role", "status");
      message.textContent = "ZoroSpoiler에 연결할 수 없어 이 영상의 댓글 보호가 꺼져 있어요.";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "다시 확인";
      retry.addEventListener("click", () => loadVideoInfo(opened));
      notice.append(message, retry);
      opened.notice = notice;
    }
    if (opened.notice.parentElement !== commentsRoot) {
      commentsRoot.prepend(opened.notice);
    }
  }

  function pickJudgment(opened, text) {
    let judgment = opened.judgments.get(text);
    if (!judgment) {
      judgment = {
        id: `c${opened.nextCommentId++}`,
        text,
        status: text.length <= MAX_COMMENT_LENGTH ? "queued" : "uncertain",
      };
      opened.judgments.set(text, judgment);
    }
    return judgment;
  }

  function scanComments(opened, commentsRoot) {
    for (const [body, entry] of opened.comments) {
      if (!commentsRoot?.contains(body)) {
        disposeComment(entry);
        opened.comments.delete(body);
      }
    }
    if (!commentsRoot) {
      return;
    }

    for (const body of commentsRoot.querySelectorAll(COMMENT_BODY)) {
      const text = body.textContent.trim();
      const existing = opened.comments.get(body);
      if (!text) {
        if (existing) {
          disposeComment(existing);
          opened.comments.delete(body);
        }
        continue;
      }
      if (text === existing?.judgment.text) {
        if (!existing.button.isConnected) {
          body.after(existing.button);
        }
        continue;
      }
      if (existing) {
        disposeComment(existing);
      }
      addComment(opened, body, pickJudgment(opened, text));
    }
  }

  function addComment(opened, body, judgment) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zoro-spoiler-cover";
    const entry = { body, button, judgment, manuallyRevealed: false };
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      entry.manuallyRevealed = !entry.manuallyRevealed;
      renderComments(opened);
    });
    opened.comments.set(body, entry);
    body.after(button);
  }

  function hasPlayedRange(played, start, end) {
    for (let i = 0; played && i < played.length; i++) {
      if (
        played.start(i) <= start + RANGE_TOLERANCE_SECONDS &&
        played.end(i) >= end - RANGE_TOLERANCE_SECONDS
      ) {
        return true;
      }
    }
    return false;
  }

  function renderComments(opened) {
    if (page !== opened || navigating || opened.mode === "off") {
      return;
    }
    const played = isAdPlaying(opened.video) ? null : opened.video?.played;
    for (const { body, button, judgment, manuallyRevealed } of opened.comments.values()) {
      if (
        !judgment.viewed &&
        judgment.status === "spoiler" &&
        hasPlayedRange(played, judgment.startSec, judgment.endSec)
      ) {
        judgment.viewed = true;
      }
      const automaticallyVisible = judgment.status === "safe" || judgment.viewed === true;
      body.toggleAttribute("data-zoro-visible", automaticallyVisible || manuallyRevealed);
      if (button.hidden !== automaticallyVisible) {
        button.hidden = automaticallyVisible;
      }
      const label = coverLabel(opened, judgment, manuallyRevealed);
      if (button.textContent !== label) {
        button.textContent = label;
      }
    }
  }

  function coverLabel(opened, judgment, manuallyRevealed) {
    if (manuallyRevealed) {
      return "댓글 다시 가리기";
    }
    if (judgment.status === "spoiler") {
      return "아직 보지 않은 장면의 댓글 · 지금 보기";
    }
    if (["uncertain", "error"].includes(judgment.status)) {
      return "스포일러 확인 불가 · 지금 보기";
    }
    return "스포일러 확인 중 · 지금 보기";
  }

  async function submitNextBatch(opened) {
    if (opened.inFlight) {
      return;
    }
    const batch = [...new Set([...opened.comments.values()].map((entry) => entry.judgment))]
      .filter((judgment) => judgment.status === "queued")
      .slice(0, MAX_BATCH);
    if (!batch.length) {
      return;
    }

    opened.inFlight = true;
    for (const judgment of batch) {
      judgment.status = "sending";
    }
    try {
      const response = await chrome.runtime.sendMessage({
        type: "zoro-judge",
        videoId: opened.videoId,
        comments: batch.map(({ id, text }) => ({ id, text })),
      });
      if (!response?.ok || !Array.isArray(response.data?.decisions)) {
        throw new Error("Invalid judge response");
      }
      const decisions = new Map(response.data.decisions.filter(Boolean).map((decision) => [decision.id, decision]));
      for (const judgment of batch) {
        applyDecision(judgment, decisions.get(judgment.id), opened.duration);
      }
    } catch {
      for (const judgment of batch) {
        judgment.status = "error";
      }
    } finally {
      opened.inFlight = false;
      if (page === opened && !navigating && opened.mode === "active") {
        renderComments(opened);
        submitNextBatch(opened);
      }
    }
  }

  function applyDecision(judgment, decision, duration) {
    if (decision?.status === "safe") {
      judgment.status = "safe";
      return;
    }
    const { status, startSec, endSec } = decision ?? {};
    if (
      status === "spoiler" &&
      Number.isFinite(startSec) &&
      Number.isFinite(endSec) &&
      0 <= startSec &&
      startSec < endSec &&
      endSec <= duration
    ) {
      Object.assign(judgment, { status, startSec, endSec });
      return;
    }
    judgment.status = "uncertain";
  }

  function protectDuringNavigation() {
    navigating = true;
    setProtection("pending");
    for (const { body } of page?.comments.values() ?? []) {
      delete body.dataset.zoroVisible;
    }
    clearTimeout(navigationTimeout);
    navigationTimeout = setTimeout(finishNavigation, 1_000);
  }

  function finishNavigation() {
    clearTimeout(navigationTimeout);
    navigating = false;
    syncPage();
  }

  function affectsWatchPage({ type, target, addedNodes, removedNodes }) {
    const element = target.nodeType === Node.ELEMENT_NODE ? target : target.parentElement;
    if (type === "attributes") {
      return element?.matches("ytd-watch-flexy, ytd-comments");
    }
    if (element?.matches(WATCH_ROOT) || element?.closest("ytd-comments")) {
      return true;
    }
    return [...addedNodes, ...removedNodes].some(
      (node) =>
        node.matches?.("ytd-watch-flexy, ytd-comments, video") ||
        node.querySelector?.("ytd-watch-flexy, ytd-comments, video"),
    );
  }

  openPage(videoIdAt());
  const observer = new MutationObserver((mutations) => {
    if (
      !document.documentElement?.hasAttribute("data-zoro-protection") ||
      videoIdAt() !== page.videoId ||
      mutations.some(affectsWatchPage)
    ) {
      if (document.documentElement && !document.documentElement.hasAttribute("data-zoro-protection")) {
        setProtection(navigating ? "pending" : page.mode);
      }
      syncPage();
    }
  });
  observer.observe(document, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["video-id", "hidden"],
  });

  document.addEventListener("yt-navigate-start", protectDuringNavigation);
  document.addEventListener("yt-navigate-finish", finishNavigation);
  window.addEventListener("popstate", finishNavigation);
  window.navigation?.addEventListener("navigate", ({ destination }) => {
    if (videoIdAt(new URL(destination.url)) !== page.videoId) {
      protectDuringNavigation();
    }
  });
  for (const eventName of ["loadedmetadata", "durationchange", "playing", "emptied"]) {
    document.addEventListener(eventName, syncPage, true);
  }
  for (const eventName of ["timeupdate", "seeked", "ended"]) {
    document.addEventListener(eventName, (event) => {
      if (page.mode !== "active" || event.target !== page.video) {
        syncPage();
      } else {
        renderComments(page);
      }
    }, true);
  }
})();
