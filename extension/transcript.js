(() => {
  const VIDEO_ID = /^[\w-]{11}$/;
  const MAX_CUES = 12_000;
  const MAX_DURATION = 43_200;
  const MAX_TRANSCRIPT_BYTES = 1_750_000;
  const REQUEST_TIMEOUT = 7_000;

  // Read just the bootstrap JSON. Never execute script text from the watch page.
  function readObject(source, marker) {
    const match = marker.exec(source);
    if (!match) return undefined;
    const start = match.index + match[0].length - 1;
    let depth = 0;
    let quoted = false;
    for (let index = start; index < source.length; index += 1) {
      const char = source[index];
      if (quoted && char === "\\") index += 1;
      else if (char === '"') quoted = !quoted;
      else if (!quoted && char === "{") depth += 1;
      else if (!quoted && char === "}" && --depth === 0) {
        try { return JSON.parse(source.slice(start, index + 1)); }
        catch { return undefined; }
      }
    }
    return undefined;
  }

  function bootData(sources, videoId) {
    let player;
    let watch;
    let client;
    for (const source of sources) {
      player ??= readObject(source, /(?:\bvar\s+ytInitialPlayerResponse|\bwindow\["ytInitialPlayerResponse"\])\s*=\s*\{/u);
      watch ??= readObject(source, /(?:\bvar\s+ytInitialData|\bwindow\["ytInitialData"\])\s*=\s*\{/u);
      client ??= readObject(source, /"INNERTUBE_CONTEXT"\s*:\s*\{/u)?.client;
      if (player && watch && client) break;
    }
    if (player?.videoDetails?.videoId !== videoId || !watch) return undefined;
    const actualId = watch.currentVideoEndpoint?.watchEndpoint?.videoId;
    if (actualId && actualId !== videoId) return undefined;
    const context = client && {
      client: {
        clientName: client.clientName,
        clientVersion: client.clientVersion,
        hl: client.hl,
        gl: client.gl,
        visitorData: client.visitorData,
      },
    };
    return { player, watch, context };
  }

  async function fetchWatch(videoId, signal) {
    const url = new URL("/watch", location.origin);
    url.searchParams.set("v", videoId);
    const response = await fetch(url, { signal: requestSignal(signal), redirect: "error" });
    if (!response.ok) throw new Error("YouTube watch metadata is unavailable");
    const html = await response.text();
    return bootData([html], videoId);
  }

  function* objects(root) {
    const pending = [root];
    while (pending.length) {
      const item = pending.pop();
      if (!item || typeof item !== "object") continue;
      yield item;
      const children = Object.values(item);
      for (let index = children.length - 1; index >= 0; index -= 1) {
        pending.push(children[index]);
      }
    }
  }

  function requestSignal(signal) {
    const timeout = AbortSignal.timeout(REQUEST_TIMEOUT);
    return signal ? AbortSignal.any([signal, timeout]) : timeout;
  }

  function readText(value) {
    if (typeof value === "string") return value;
    if (typeof value?.simpleText === "string") return value.simpleText;
    return Array.isArray(value?.runs) ? value.runs.map(run => run.text ?? "").join("") : "";
  }

  function milliseconds(value) {
    if (typeof value === "number" || (typeof value === "string" && /^\d+$/u.test(value))) {
      return Number(value) / 1_000;
    }
    return NaN;
  }

  function timestamp(value) {
    const match = /^(?:(\d+):)?(\d+):(\d{2})$/u.exec(value ?? "");
    return match ? Number(match[1] ?? 0) * 3_600 + Number(match[2]) * 60 + Number(match[3]) : NaN;
  }

  function normalize(rows, duration) {
    const cues = rows
      .filter(row => Number.isFinite(row.start) && row.start >= 0 && row.start < duration && row.text.trim())
      .sort((first, second) => first.start - second.start);
    const merged = [];
    for (const cue of cues) {
      const text = cue.text.replace(/\s+/gu, " ").trim();
      const previous = merged.at(-1);
      if (previous?.start === cue.start) {
        if (previous.text !== text) previous.text += ` ${text}`;
        if (Number.isFinite(cue.end)) previous.end = Math.max(previous.end || cue.end, cue.end);
      } else {
        merged.push({ ...cue, text });
      }
    }
    // The modern transcript displays segment starts. Use the next segment boundary when no end was sent.
    for (const [index, cue] of merged.entries()) {
      const nextStart = merged[index + 1]?.start ?? Math.min(cue.start + 30, duration);
      cue.end = Math.min(duration, Number.isFinite(cue.end) && cue.end > cue.start ? cue.end : nextStart);
      if ([...cue.text].length > 1_000) throw new Error("A YouTube transcript segment is too long");
    }
    if (merged.length > MAX_CUES || new TextEncoder().encode(JSON.stringify(merged)).length > MAX_TRANSCRIPT_BYTES) {
      throw new Error("YouTube transcript exceeds the supported size");
    }
    return merged;
  }

  function transcriptRows(response, videoId) {
    const rows = [];
    for (const item of objects(response)) {
      const modern = item.macroMarkersPanelItemViewModel;
      const legacy = item.transcriptSegmentRenderer;
      if (modern) {
        const target = modern.onTap?.innertubeCommand?.watchEndpoint;
        if (target?.videoId && target.videoId !== videoId) continue;
        const timeline = modern.item?.timelineItemViewModel;
        for (const { transcriptSegmentViewModel: segment } of timeline?.contentItems ?? []) {
          if (!segment) continue;
          rows.push({
            start: target?.startTimeSeconds ?? timestamp(segment.timestamp ?? timeline.timestamp),
            end: milliseconds(segment.endMs),
            text: readText(segment.simpleText ?? segment.content),
          });
        }
      }
      if (legacy) rows.push({
        start: milliseconds(legacy.startMs),
        end: milliseconds(legacy.endMs),
        text: readText(legacy.snippet),
      });
    }
    return rows;
  }

  function chapterRows(watch, duration) {
    const bar = watch.playerOverlays?.playerOverlayRenderer?.decoratedPlayerBarRenderer
      ?.decoratedPlayerBarRenderer?.playerBar?.multiMarkersPlayerBarRenderer;
    const chapters = bar?.markersMap?.find(marker => marker.key === "DESCRIPTION_CHAPTERS")?.value?.chapters ?? [];
    return chapters.slice(0, 400).map(({ chapterRenderer }) => ({
      start: milliseconds(chapterRenderer?.timeRangeStartMillis),
      title: [...readText(chapterRenderer?.title)].slice(0, 300).join("").trim(),
    })).filter(chapter => Number.isFinite(chapter.start) && chapter.start >= 0 && chapter.start < duration && chapter.title)
      .sort((first, second) => first.start - second.start)
      .filter((chapter, index, ordered) => !index || chapter.start !== ordered[index - 1].start);
  }

  function transcriptRequests(watch) {
    let panel;
    let legacy;
    for (const item of objects(watch.engagementPanels)) {
      const content = item.updateEngagementPanelContentCommand;
      if (content?.contentSourcePanelIdentifier?.tag === "PAmodern_transcript_view" && content.globalConfiguration?.params) {
        panel ??= { panelId: "PAmodern_transcript_view", params: content.globalConfiguration.params };
      }
      if (item.getTranscriptEndpoint?.params) legacy ??= { params: item.getTranscriptEndpoint.params };
      if (panel && legacy) break;
    }
    return [["get_panel", panel], ["get_transcript", legacy]].filter(([, payload]) => payload);
  }

  async function timedText(track, videoId, signal) {
    let url;
    try { url = new URL(track.baseUrl); }
    catch { return []; }
    if (url.origin !== location.origin || url.pathname !== "/api/timedtext" || url.searchParams.get("v") !== videoId) return [];
    url.searchParams.set("fmt", "json3");
    const response = await fetch(url, { signal: requestSignal(signal), redirect: "error" });
    if (!response.ok) return [];
    const events = (await response.json()).events;
    return Array.isArray(events) ? events.map(event => ({
      start: milliseconds(event.tStartMs),
      end: milliseconds(event.tStartMs) + milliseconds(event.dDurationMs),
      text: Array.isArray(event.segs) ? event.segs.map(segment => segment.utf8 ?? "").join("") : "",
    })) : [];
  }

  async function load(videoId, { signal } = {}) {
    if (!VIDEO_ID.test(videoId)) throw new Error("Invalid YouTube video ID");
    signal?.throwIfAborted();
    const sources = Array.from(document.scripts, script => script.src ? "" : script.textContent);
    const data = bootData(sources, videoId) ?? await fetchWatch(videoId, signal);
    if (!data) throw new Error("Could not read the current YouTube video metadata");
    const { player, watch, context } = data;
    const title = [...readText(player.videoDetails.title).trim()].slice(0, 300).join("");
    if (!title) throw new Error("Could not read the current YouTube video title");
    const duration = Number(player.videoDetails.lengthSeconds) || MAX_DURATION;
    if (duration <= 0 || duration > MAX_DURATION) throw new Error("YouTube transcript exceeds the supported duration");
    const chapterData = chapterRows(watch, duration);
    const requests = transcriptRequests(watch);
    const tracks = player.captions?.playerCaptionsTracklistRenderer?.captionTracks ?? [];
    if (!requests.length && !tracks.length) return null;

    for (const [endpoint, payload] of context ? requests : []) {
      try {
        const response = await fetch(`/youtubei/v1/${endpoint}?prettyPrint=false`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ context, ...payload }),
          signal: requestSignal(signal),
          redirect: "error",
        });
        if (!response.ok) continue;
        const cues = normalize(transcriptRows(await response.json(), videoId), duration);
        if (cues.length) return { title, cues, chapters: chapterData };
      } catch {
        signal?.throwIfAborted();
      }
    }

    // Keep the original track order within each priority. Prefer Korean and human-authored captions when offered.
    const rank = track => (track.languageCode?.split("-")[0] === "ko" ? 2 : 0) + (track.kind === "asr" ? 0 : 1);
    for (const track of [...tracks].sort((first, second) => rank(second) - rank(first)).slice(0, 3)) {
      try {
        const cues = normalize(await timedText(track, videoId, signal), duration);
        if (cues.length) return { title, cues, chapters: chapterData, language: track.languageCode };
      } catch {
        signal?.throwIfAborted();
      }
    }
    throw new Error("Could not retrieve the available YouTube transcript");
  }

  globalThis.ZoroTranscript = Object.freeze({ load });
})();
