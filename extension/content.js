(() => {
  const MAX_BATCH = 8;
  const MAX_COMMENT_LENGTH = 5_000;
  const COMMENT_BODY = '[id="content-text"]';
  const WATCH_ROOT = "ytd-watch-flexy:not([hidden])";
  const VIDEO_ID = /^[\w-]{11}$/;

  let page;
  let modelSource;
  let navigating = false;
  let navigationTimeout;

  function videoIdAt(url = new URL(location.href)) {
    const id = url.searchParams.get("v");
    return url.pathname === "/watch" && id && VIDEO_ID.test(id) ? id : null;
  }

  function setProtection(active) {
    const root = document.documentElement;
    if (root?.hasAttribute("data-zoro-protection") !== active) {
      root?.toggleAttribute("data-zoro-protection", active);
    }
  }

  function disposeComment({ body, frame, button }) {
    body.removeAttribute("data-zoro-visible");
    frame.removeAttribute("data-zoro-concealed");
    button.remove();
  }

  function clearComments(opened) {
    for (const entry of opened.comments.values()) {
      disposeComment(entry);
    }
    opened.comments.clear();
  }

  function openPage(videoId) {
    if (page) {
      page.transcriptRequest?.abort();
      page.toolbar?.root.remove();
      clearComments(page);
    }
    const opened = {
      videoId,
      transcript: null,
      transcriptState: "loading",
      hidden: true,
      comments: new Map(),
      judgments: new Map(),
      nextCommentId: 1,
      inFlight: false,
      connectionFailed: false,
      connectionVersion: 0,
    };
    page = opened;
    setProtection(Boolean(videoId));
    if (videoId) {
      loadTranscript(opened);
    }
  }

  async function loadTranscript(opened) {
    const request = new AbortController();
    opened.transcriptRequest = request;
    opened.transcriptState = "loading";
    syncPage();
    try {
      opened.transcript = await globalThis.ZoroTranscript.load(opened.videoId, { signal: request.signal });
      opened.transcriptState = opened.transcript ? "ready" : "unavailable";
    } catch {
      opened.transcriptState = "error";
    }
    if (page === opened && !request.signal.aborted) {
      syncPage();
    }
  }

  function protectionActive(opened) {
    return opened.transcriptState === "ready" || opened.transcriptState === "loading";
  }

  function syncPage() {
    const videoId = videoIdAt();
    if (videoId !== page.videoId) {
      navigating = false;
      openPage(videoId);
    }
    if (navigating || !videoId) {
      return;
    }
    const roots = [...document.querySelectorAll(WATCH_ROOT)];
    const watchRoot = roots.find((root) => root.getAttribute("video-id") === videoId)
      ?? roots.find((root) => !root.hasAttribute("video-id"));
    const commentsRoot = watchRoot?.querySelector("ytd-comments");
    const active = protectionActive(page);

    setProtection(active);
    syncToolbar(page, commentsRoot);
    if (!active) {
      clearComments(page);
      return;
    }
    scanComments(page, commentsRoot);
    renderComments(page);
    if (page.transcriptState === "ready") {
      submitNextBatch(page);
    }
  }

  function createToolbar(opened) {
    const root = document.createElement("div");
    root.className = "zoro-toolbar";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "zoro-global-toggle";
    toggle.addEventListener("click", () => {
      opened.hidden = !opened.hidden;
      for (const entry of opened.comments.values()) {
        entry.override = null;
      }
      syncPage();
    });

    const status = document.createElement("span");
    status.setAttribute("role", "status");

    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "다시 시도";
    retry.addEventListener("click", () => {
      if (opened.transcriptState === "error") {
        loadTranscript(opened);
      } else {
        opened.connectionFailed = false;
        syncPage();
      }
    });

    root.append(toggle, status, retry);
    return { root, toggle, status, retry };
  }

  function syncToolbar(opened, commentsRoot) {
    if (!commentsRoot) {
      opened.toolbar?.root.remove();
      return;
    }
    opened.toolbar ??= createToolbar(opened);
    const { root, toggle, status, retry } = opened.toolbar;
    if (root.parentElement !== commentsRoot || root !== commentsRoot.firstElementChild) {
      commentsRoot.prepend(root);
    }
    const active = protectionActive(opened);
    const message = {
      loading: "이 영상의 스크립트를 확인 중입니다.",
      unavailable: "스크립트가 있는 영상에서만 스포일러 댓글을 가릴 수 있습니다.",
      error: "이 영상의 스크립트를 불러오지 못했습니다.",
      ready: opened.connectionFailed
        ? "Zoro-1에 연결하지 못했습니다. 실행 상태와 확장 프로그램의 연결 설정을 확인하세요."
        : "",
    }[opened.transcriptState];
    setText(toggle, opened.hidden ? "스포일러 댓글 보기" : "스포일러 댓글 가리기");
    setText(status, message);
    const disableToggle = !active;
    if (toggle.disabled !== disableToggle) {
      toggle.disabled = disableToggle;
    }
    const hideRetry = opened.transcriptState !== "error" && !opened.connectionFailed;
    if (retry.hidden !== hideRetry) {
      retry.hidden = hideRetry;
    }
  }

  function setText(element, text) {
    if (element.textContent !== text) {
      element.textContent = text;
    }
  }

  function pickJudgment(opened, text) {
    if (!opened.judgments.has(text)) {
      opened.judgments.set(text, {
        id: `c${opened.nextCommentId++}`,
        text,
        status: text.length <= MAX_COMMENT_LENGTH ? "queued" : "uncertain",
      });
    }
    return opened.judgments.get(text);
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
      if (text === existing?.judgment.text) {
        attachCommentButton(existing);
        continue;
      }
      if (existing) {
        disposeComment(existing);
        opened.comments.delete(body);
      }
      if (text) {
        addComment(opened, body, pickJudgment(opened, text));
      }
    }
  }

  function addComment(opened, body, judgment) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zoro-spoiler-cover";
    const entry = { body, button, judgment, frame: body, override: null };
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      entry.override = isConcealed(opened, entry) ? "show" : "hide";
      renderComment(opened, entry);
    });
    opened.comments.set(body, entry);
    attachCommentButton(entry);
  }

  function attachCommentButton(entry) {
    const { body, button } = entry;
    // Place the control outside YouTube's collapsed comment text and native More button.
    const frame = body.closest("ytd-expander") ?? body.closest("#expander") ?? body;
    if (frame !== entry.frame) {
      entry.frame.removeAttribute("data-zoro-concealed");
      entry.frame = frame;
    }
    if (frame.nextElementSibling !== button) {
      frame.after(button);
    }
  }

  function isConcealed(opened, entry) {
    return entry.judgment.status !== "safe" &&
      (entry.override === null ? opened.hidden : entry.override === "hide");
  }

  function renderComment(opened, entry) {
    const { body, button, frame, judgment } = entry;
    const concealed = isConcealed(opened, entry);
    body.toggleAttribute("data-zoro-visible", !concealed);
    frame.toggleAttribute("data-zoro-concealed", concealed);
    const safe = judgment.status === "safe";
    if (button.hidden !== safe) {
      button.hidden = safe;
    }
    const labels = {
      spoiler: "스포일러 보기",
      uncertain: "스포일러 확인 불가 · 보기",
      queued: opened.connectionFailed ? "스포일러 확인 불가 · 보기" : "스포일러 확인 중 · 보기",
      sending: "스포일러 확인 중 · 보기",
    };
    setText(button, concealed ? labels[judgment.status] : "스포일러 다시 가리기");
  }

  function renderComments(opened) {
    for (const entry of opened.comments.values()) {
      renderComment(opened, entry);
    }
  }

  function resetConnection(opened) {
    opened.connectionVersion++;
    opened.connectionFailed = false;
    for (const judgment of opened.judgments.values()) {
      judgment.status = judgment.text.length <= MAX_COMMENT_LENGTH ? "queued" : "uncertain";
    }
    syncPage();
  }

  async function submitNextBatch(opened) {
    if (!modelSource || opened.inFlight || opened.connectionFailed) {
      return;
    }
    const batch = [...new Set([...opened.comments.values()].map((entry) => entry.judgment))]
      .filter((judgment) => judgment.status === "queued")
      .slice(0, modelSource === "local" ? 1 : MAX_BATCH);
    if (!batch.length) {
      return;
    }
    opened.inFlight = true;
    const version = opened.connectionVersion;
    for (const judgment of batch) {
      judgment.status = "sending";
    }
    try {
      const { title, cues, chapters } = opened.transcript;
      const response = await chrome.runtime.sendMessage({
        type: "zoro-judge",
        videoId: opened.videoId,
        title,
        cues,
        chapters,
        comments: batch.map(({ id, text }) => ({ id, text })),
      });
      if (version !== opened.connectionVersion) {
        return;
      }
      if (!response?.ok || !Array.isArray(response.data?.decisions)) {
        throw new Error("Invalid judge response");
      }
      const decisions = new Map(response.data.decisions.filter(Boolean).map(({ id, status }) => [id, status]));
      for (const judgment of batch) {
        const status = decisions.get(judgment.id);
        judgment.status = status === "safe" || status === "spoiler" ? status : "uncertain";
      }
    } catch {
      if (version === opened.connectionVersion) {
        opened.connectionFailed = true;
        for (const judgment of batch) {
          judgment.status = "queued";
        }
      }
    } finally {
      opened.inFlight = false;
      if (page === opened && !navigating) {
        syncPage();
      }
    }
  }

  function protectDuringNavigation() {
    navigating = true;
    setProtection(true);
    for (const { body } of page.comments.values()) {
      body.removeAttribute("data-zoro-visible");
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
        node.matches?.("ytd-watch-flexy, ytd-comments") ||
        node.querySelector?.("ytd-watch-flexy, ytd-comments"),
    );
  }

  openPage(videoIdAt());
  const observer = new MutationObserver((mutations) => {
    if (videoIdAt() !== page.videoId || mutations.some(affectsWatchPage)) {
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
  function useModelSource(value) {
    const source = value === "local" ? "local" : "brev";
    if (modelSource === source) {
      return;
    }
    const previous = modelSource;
    modelSource = source;
    if (previous && page.videoId) {
      resetConnection(page);
    } else {
      syncPage();
    }
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.modelSource) {
      useModelSource(changes.modelSource.newValue);
    }
  });
  chrome.storage.local.get("modelSource").then(
    (settings) => { if (!modelSource) useModelSource(settings.modelSource); },
    () => { if (!modelSource) useModelSource(); },
  );
})();
