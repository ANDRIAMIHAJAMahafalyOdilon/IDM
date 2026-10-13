(() => {
    const EXT_RE = /\.(mp4|webm|mkv|avi|mov|m4v|m3u8|mpd)(?:[?#]|$)/i;
    const panels = new WeakMap();
    const knownVideos = new Set();
    const refreshTimers = new WeakMap();
    const resourceCache = new Map();
    const RESOURCE_TTL = 60_000;
    const isYouTubePage = () => /^https?:\/\/(?:www\.|m\.)?(?:youtube\.com|youtube-nocookie\.com|youtu\.be)\//i
        .test(location.href);

    const sendMessage = (message, timeoutMs = 10_000) => new Promise(resolve => {
        let settled = false;
        const timer = setTimeout(() => {
            settled = true;
            resolve(null);
        }, timeoutMs);
        chrome.runtime.sendMessage(message, response => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            if (chrome.runtime.lastError) resolve(null);
            else resolve(response || null);
        });
    });

    const safeName = (resource, index) => {
        const candidate = resource.title || resource.cdName || resource.urlName;
        if (candidate) return candidate.replace(/[\\/:*?"<>|\x00-\x1f]/g, "_");
        try {
            const name = decodeURIComponent(new URL(resource.url).pathname.split("/").pop());
            if (name && /\.[a-z0-9]{2,5}$/i.test(name)) return name;
        } catch (_) {}
        return `video_${index + 1}.${resource.ext || "mp4"}`;
    };

    const compactLabel = resource => {
        const parts = [resource.ext && resource.ext.toUpperCase(), resource.label];
        if (resource.bitrateKbps) parts.push(`${resource.bitrateKbps} kb/s`);
        return parts.filter(Boolean).join("  ");
    };

    const resourceKey = resource => `${resource.url}:${resource.formatIndex ?? ""}`;
    const isGenericName = name => !String(name || "").trim() ||
        /^(download|file|video|media|index|master|stream|playlist|manifest|default|untitled)(?:[-_. ].*)?$/i
            .test(String(name || "").replace(/\.[a-z0-9]{2,5}$/i, "").trim());
    const sizeLabel = resource => {
        const size = Number(resource.size);
        if (!Number.isFinite(size) || size <= 0) return "Taille inconnue";
        const text = formatSize(size);
        return resource.sizeEstimated || resource.kind === "hls"
            ? `Taille estimée : ${text}` : `Taille exacte : ${text}`;
    };

    function formatSize(bytes) {
        let n = Number(bytes);
        if (!Number.isFinite(n) || n <= 0) return "";
        const units = ["o", "Ko", "Mo", "Go", "To"];
        let unit = 0;
        while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit++; }
        return `${n.toFixed(1)} ${units[unit]}`;
    }

    function createPanel(video) {
        const host = document.createElement("div");
        host.dataset.flashdlVideoPanel = "1";
        host.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;";
        const shadow = host.attachShadow({ mode: "closed" });
        shadow.innerHTML = `
          <style>
            :host{all:initial;font:13px/1.35 "Segoe UI",Arial,sans-serif;color:#202124}
            *{box-sizing:border-box}
            .panel{width:min(320px,calc(100vw - 24px));max-height:min(250px,40vh);overflow:hidden;
              background:#fff;border:1px solid #c8cdd4;border-radius:8px;box-shadow:0 5px 22px #0005;
              pointer-events:auto}
            .head{height:34px;display:flex;align-items:center;gap:8px;padding:0 9px;
              background:#f1f4f8;border-bottom:1px solid #dce1e7;cursor:move;user-select:none}
            .logo{width:18px;height:18px;border-radius:50%;background:#1976d2;color:#fff;
              display:grid;place-items:center;font-weight:700;font-size:11px}
            .title{font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
            button{font:inherit;cursor:pointer;border:0;border-radius:4px;padding:5px 8px;background:#e8edf3;color:#263238}
            button:hover{filter:brightness(.96)}
            .icon{padding:2px 7px;background:transparent;font-size:16px}
            .list{padding:4px;overflow:auto;max-height:150px}
            .row{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:6px;align-items:center;
              padding:6px 4px;border-bottom:1px solid #edf0f3}
            .row:last-child{border-bottom:0}
            .check{margin:0;accent-color:#1976d2}
            .info{min-width:0}
            .name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
            .meta{font-size:11px;color:#5f6368;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
            .size{font-size:10px;color:#5f6368;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
            .detail{display:none;font-size:10px;color:#687078;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
            .panel.detailed .detail{display:block}
            .one{font-size:11px;background:#1976d2;color:white;padding:5px 7px}
            .empty{padding:8px 6px;color:#62676d;font-size:11px}
            .foot{display:flex;align-items:center;gap:6px;padding:6px;border-top:1px solid #e5e8ec}
            .foot .hint{flex:1;color:#62676d;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
            .all{background:#1976d2;color:white;font-weight:600}
            .pill{pointer-events:auto;border:0;border-radius:18px;background:#1976d2;color:#fff;
              padding:8px 12px;box-shadow:0 3px 12px #0005;font-weight:600;cursor:pointer}
            .hidden{display:none}
          </style>
          <div class="panel hidden">
            <div class="head"><span class="logo">↓</span><span class="title">Roch Xd</span>
              <button class="icon detailsToggle" title="Détails">⋯</button>
              <button class="icon minimize" title="Réduire">−</button><button class="icon close" title="Masquer">×</button>
            </div>
            <div class="list"></div>
            <div class="foot"><span class="hint"></span><button class="all">Télécharger tout</button></div>
          </div>
          <button class="pill hidden">↓ Vidéo détectée</button>`;
        (document.documentElement || document.body).appendChild(host);

        const panel = shadow.querySelector(".panel");
        const list = shadow.querySelector(".list");
        const pill = shadow.querySelector(".pill");
        let pageMetadata = {};
        let currentResources = [];
        const submitting = new Set();
        let detailed = false;
        const detectionStartedAt = performance.now();
        let resourceTimingLogged = false;
        let titleTimingLogged = false;
        chrome.storage.local.get({ videoPanelDetailed: false }, value => {
            detailed = !!value.videoPanelDetailed;
            panel.classList.toggle("detailed", detailed);
        });
        shadow.querySelector(".detailsToggle").addEventListener("click", () => {
            detailed = !detailed;
            panel.classList.toggle("detailed", detailed);
            chrome.storage.local.set({ videoPanelDetailed: detailed });
        });
        const closePanel = () => { panel.classList.add("hidden"); pill.classList.remove("hidden"); };
        shadow.querySelector(".close").addEventListener("click", closePanel);
        shadow.querySelector(".minimize").addEventListener("click", closePanel);
        pill.addEventListener("click", () => { pill.classList.add("hidden"); panel.classList.remove("hidden"); });

        // Drag only from the compact title bar. The overlay never captures
        // pointer events outside its own visible controls.
        const head = shadow.querySelector(".head");
        head.addEventListener("pointerdown", event => {
            if (event.target.closest("button")) return;
            const rect = host.getBoundingClientRect();
            const dx = event.clientX - rect.left, dy = event.clientY - rect.top;
            head.setPointerCapture(event.pointerId);
            const move = e => {
                host.style.left = `${Math.max(0, Math.min(innerWidth - 40, e.clientX - dx))}px`;
                host.style.top = `${Math.max(0, Math.min(innerHeight - 30, e.clientY - dy))}px`;
                host.style.right = "auto"; host.style.bottom = "auto";
            };
            const up = () => { head.removeEventListener("pointermove", move); head.removeEventListener("pointerup", up); };
            head.addEventListener("pointermove", move); head.addEventListener("pointerup", up);
        });

        const position = () => {
            if (!host.isConnected) return;
            const rect = video.getBoundingClientRect();
            if (!rect.width || !rect.height || host.dataset.userMoved === "1") return;
            host.style.display = rect.bottom < 0 || rect.top > innerHeight ? "none" : "block";
            const width = Math.min(320, innerWidth - 24);
            const left = Math.max(12, Math.min(innerWidth - width - 12, rect.right - width - 12));
            const top = Math.max(12, Math.min(innerHeight - 60, rect.top + 12));
            host.style.left = `${left}px`; host.style.top = `${top}px`;
            host.style.right = "auto"; host.style.bottom = "auto";
        };
        head.addEventListener("pointerdown", () => { host.dataset.userMoved = "1"; }, { once: true });
        const observer = new ResizeObserver(position);
        observer.observe(video);
        window.addEventListener("scroll", position, { passive: true });
        window.addEventListener("resize", position, { passive: true });
        position();
        return {
            host, shadow, video, list,
            addCapturedResource(resource, metadata = {}) {
                if (!resource?.url || (video.mediaKeys && !isYouTubePage())) return;
                const merged = new Map(currentResources.map(item => [resourceKey(item), item]));
                merged.set(resourceKey(resource), resource);
                this.show([...merged.values()], false, { ...pageMetadata, ...metadata });
            },
            show(resources, loading = false, metadata = {}, emptyText = "") {
                const previous = new Map([...list.querySelectorAll(".row")].map(row =>
                    [row.dataset.key, { checked: row.querySelector(".check")?.checked,
                        sent: row.querySelector(".one")?.dataset.sent === "1" }]));
                const unique = new Map((resources || []).filter(r => r?.url && /^https?:/i.test(r.url))
                    .map(r => [resourceKey(r), r]));
                currentResources = [...unique.values()];
                if (!unique.size && !loading && !emptyText) {
                    panel.classList.add("hidden"); pill.classList.add("hidden"); return;
                }
                pageMetadata = metadata;
                if (panel.classList.contains("hidden") && pill.classList.contains("hidden"))
                    panel.classList.remove("hidden");
                list.replaceChildren();
                [...unique.values()].forEach((resource, index) => {
                    const key = resourceKey(resource);
                    const row = document.createElement("div"); row.className = "row"; row.dataset.key = key;
                    const checkbox = document.createElement("input"); checkbox.type = "checkbox";
                    checkbox.className = "check"; checkbox.checked = previous.get(key)?.checked ?? true;
                    const info = document.createElement("div"); info.className = "info";
                    const name = document.createElement("div"); name.className = "name";
                    const remoteName = resource.title || resource.cdName || resource.urlName || "";
                    const title = !isGenericName(remoteName) ? remoteName :
                        (metadata.videoTitle || metadata.pageTitle || safeName(resource, index));
                    name.textContent = title; name.title = title;
                    const meta = document.createElement("div"); meta.className = "meta";
                    meta.textContent = compactLabel(resource) || resource.kind || "Vidéo détectée";
                    const size = document.createElement("div"); size.className = "size";
                    size.textContent = sizeLabel(resource);
                    const detail = document.createElement("div"); detail.className = "detail";
                    const duration = Number(metadata.duration);
                    detail.textContent = [resource.url,
                        Number.isFinite(duration) && duration > 0
                            ? `${Math.round(duration)} s` : ""]
                        .filter(Boolean).join(" · ");
                    info.append(name, meta, size, detail);
                    const one = document.createElement("button"); one.className = "one";
                    one.textContent = previous.get(key)?.sent ? "Ajouté" : "Télécharger";
                    one.dataset.sent = previous.get(key)?.sent ? "1" : "0";
                    one.disabled = one.dataset.sent === "1" || submitting.has(key);
                    one.addEventListener("click", () => start(resource, one, pageMetadata, key, submitting));
                    row.append(checkbox, info, one); list.appendChild(row);
                });
                shadow.querySelector(".title").textContent = unique.size
                    ? `${unique.size} ressource${unique.size > 1 ? "s" : ""} vidéo`
                    : (loading ? "Analyse vidéo" : "Ressources vidéo");
                if (unique.size && !resourceTimingLogged) {
                    resourceTimingLogged = true;
                    console.debug(`[Roch Xd] première ressource visible en ${(performance.now() - detectionStartedAt).toFixed(0)} ms`);
                }
                if (unique.size && !titleTimingLogged &&
                        (metadata.videoTitle || metadata.pageTitle)) {
                    titleTimingLogged = true;
                    console.debug(`[Roch Xd] titre disponible en ${(performance.now() - detectionStartedAt).toFixed(0)} ms`);
                }
                let empty = list.querySelector(".empty");
                if (loading || emptyText) {
                    empty = document.createElement("div"); empty.className = "empty";
                    empty.textContent = emptyText || "Recherche des formats vidéo compatibles…";
                    list.appendChild(empty);
                }
                shadow.querySelector(".hint").textContent = loading
                    ? "Informations complémentaires en cours…"
                    : "Sélectionnez les ressources à ajouter";
                const all = shadow.querySelector(".all");
                all.textContent = "Télécharger la sélection";
                all.disabled = unique.size === 0;
                all.onclick = async () => {
                    const selected = [...list.querySelectorAll(".row")]
                        .filter(row => row.querySelector(".check").checked);
                    if (!selected.length) return;
                    all.disabled = true;
                    for (const row of selected) {
                        const button = row.querySelector(".one");
                        const resource = unique.get(row.dataset.key);
                        await start(resource, button, pageMetadata, row.dataset.key, submitting);
                    }
                    all.disabled = ![...list.querySelectorAll(".row")]
                        .some(row => row.querySelector(".check").checked &&
                            row.querySelector(".one")?.dataset.sent !== "1");
                };
                position();
            }
        };
    }

    async function start(resource, button, pageMetadata = {}, key = resourceKey(resource), submitting = new Set()) {
        if (button.disabled || submitting.has(key)) return;
        submitting.add(key);
        const original = button.textContent;
        button.disabled = true; button.textContent = "Envoi…";
        const technicalName = safeName(resource, 0);
        const pageTitle = pageMetadata.pageTitle || document.title || "";
        const rawVideoTitle = resource.title || pageMetadata.videoTitle || pageMetadata.ogTitle || "";
        const videoTitle = isGenericName(rawVideoTitle) ? "" : rawVideoTitle;
        const preferredTitle = videoTitle;
        const ext = resource.ext || technicalName.match(/\.([a-z0-9]{2,5})$/i)?.[1] || "";
        const suggestedFilename = preferredTitle && ext && !isGenericName(preferredTitle)
            ? (/\.[a-z0-9]{2,5}$/i.test(preferredTitle) ? preferredTitle : `${preferredTitle}.${ext}`)
            : "";
        const metadata = {
            page_url: pageMetadata.pageUrl || location.href,
            page_title: pageTitle.slice(0, 500),
            video_title: videoTitle.slice(0, 500),
            suggested_filename: suggestedFilename.slice(0, 500),
            original_filename: technicalName.slice(0, 300),
            media_type: "video",
            format: String(ext).slice(0, 12),
            media_size: Number(resource.size) > 0 ? Number(resource.size) : null,
            size_estimated: !!resource.sizeEstimated || resource.kind === "hls",
            duration_seconds: Number.isFinite(Number(pageMetadata.duration)) &&
                Number(pageMetadata.duration) > 0 ? Number(pageMetadata.duration) : null,
            resolution: (resource.label || "").match(/\b\d{3,4}p\b/i)?.[0] || "",
            season: (() => {
                const match = (videoTitle || pageTitle).match(/\b(?:S|Saison|Season)\s*0?(\d{1,2})\b/i);
                return match ? Number(match[1]) : null;
            })(),
            episode: (() => {
                const source = videoTitle || pageTitle;
                const match = source.match(/\bS\s*0?\d{1,2}\s*E\s*0?(\d{1,3})\b/i) ||
                    source.match(/\b(?:Episode|Ep|E)\s*0?(\d{1,3})\b/i);
                return match ? Number(match[1]) : null;
            })(),
            bitrate_kbps: Number.isFinite(Number(resource.bitrateKbps))
                ? Number(resource.bitrateKbps) : null,
            source_site: (() => { try { return new URL(pageMetadata.pageUrl || location.href).hostname; }
                catch (_) { return ""; } })()
        };
        let response;
        if (resource.formatIndex !== undefined) {
            response = await sendMessage({ type: "sendVideo", url: resource.pageUrl || location.href,
                referer: pageMetadata.frameUrl || pageMetadata.pageUrl || location.href,
                formatIndex: resource.formatIndex, metadata });
        } else {
            response = await sendMessage({ type: "send", url: resource.url,
                filename: technicalName,
                referer: pageMetadata.frameUrl || pageMetadata.pageUrl || location.href,
                stream: resource.kind === "hls" || /\.m3u8(?:[?#]|$)/i.test(resource.url),
                mediaType: resource.kind === "hls" ? "stream" : "video", metadata });
        }
        submitting.delete(key);
        button.disabled = !!response?.ok;
        button.dataset.sent = response?.ok ? "1" : "0";
        button.textContent = response?.ok ? "Ajouté" : (response?.cancelled ? "Annulé" : original);
        if (!response?.ok && !response?.cancelled) button.title = response?.error || "Roch Xd ne répond pas.";
    }

    function directResources(video) {
        const sources = new Set([video.currentSrc, video.src,
            ...[...video.querySelectorAll("source")].map(source =>
                source.src || source.getAttribute("src"))]
            .filter(url => url && /^https?:/i.test(url)));
        return [...sources].filter(url => EXT_RE.test(url)).map(url => {
            const hls = /\.m3u8(?:[?#]|$)/i.test(url);
            const dash = /\.mpd(?:[?#]|$)/i.test(url);
            const ext = hls || dash ? "mp4" : url.match(/\.([a-z0-9]{2,5})(?:[?#]|$)/i)?.[1] || "";
            return { url, kind: hls ? "hls" : dash ? "dash" : "video", ext,
                size: null, sizeEstimated: false };
        });
    }

    function metadataFor(video) {
        const videoTitle = video.getAttribute("title") || video.getAttribute("aria-label") || "";
        const ogTitle = document.querySelector('meta[property="og:title"]')?.content ||
            document.querySelector('meta[name="twitter:title"]')?.content || "";
        return { pageTitle: document.title, pageUrl: location.href, videoTitle, ogTitle,
            duration: Number.isFinite(video.duration) && isFinite(video.duration)
                ? video.duration : null };
    }

    function currentSourceKey(video) {
        return directResources(video).map(resourceKey).sort().join("|");
    }

    async function cachedRequest(key, message, ttl = RESOURCE_TTL) {
        const now = Date.now();
        for (const [oldKey, oldEntry] of resourceCache) {
            if (!oldKey.startsWith("pending:") && oldEntry.expiresAt <= now)
                resourceCache.delete(oldKey);
        }
        const entry = resourceCache.get(key);
        if (entry && entry.expiresAt > now) return entry.value;
        if (entry) resourceCache.delete(key);
        if (resourceCache.has(`pending:${key}`)) return resourceCache.get(`pending:${key}`);
        const pending = sendMessage(message).then(value => {
            if (value) resourceCache.set(key, { value, expiresAt: Date.now() + ttl });
            return value;
        }).finally(() => resourceCache.delete(`pending:${key}`));
        resourceCache.set(`pending:${key}`, pending);
        return pending;
    }

    async function loadResources(video, panel) {
        const isYouTube = isYouTubePage();
        // Some browsers attach MediaKeys to ordinary YouTube streams too. Let
        // yt-dlp handle YouTube instead of hiding its panel preemptively.
        if (video.mediaKeys && !isYouTube) { panel.show([]); return; }
        const direct = directResources(video);
        const metadata = metadataFor(video);
        const sourceKey = currentSourceKey(video);
        const currentPage = location.href;

        // Direct media is actionable before metadata or playlist inspection.
        if (direct.length) panel.show(direct, true, metadata);
        else panel.show([], true, metadata);

        const response = await cachedRequest(`resources:${currentPage}:${sourceKey}`, {
            type: "getVideoResources", sources: direct.map(({ url }) => ({ url })),
            pageUrl: currentPage
        });
        if (currentPage !== location.href || sourceKey !== currentSourceKey(video)) return;
        const resources = [...new Map([...direct, ...(response?.resources || [])]
            .filter(resource => resource?.url && /^https?:/i.test(resource.url))
            .map(resource => [resourceKey(resource), resource])).values()];
        const pageMetadata = { ...metadata, pageTitle: response?.pageTitle || metadata.pageTitle,
            pageUrl: response?.pageUrl || currentPage,
            frameUrl: response?.frameUrl || currentPage };

        if (resources.length) {
            panel.show(resources, false, pageMetadata);
            const hls = resources.filter(resource => resource.kind === "hls" ||
                /\.m3u8(?:[?#]|$)/i.test(resource.url));
            if (hls.length) {
                const variants = await Promise.all(hls.map(async media => {
                    const result = await cachedRequest(`hls:${media.url}`, {
                        type: "inspectHls", url: media.url,
                        referer: pageMetadata.frameUrl || pageMetadata.pageUrl
                    }, 30_000);
                    return result?.ok && result.formats?.length
                        ? result.formats.map(format => ({ ...media, ...format,
                            kind: "hls", ext: "mp4", sizeEstimated: !!format.size,
                            referer: pageMetadata.pageUrl }))
                        : [media];
                }));
                if (currentPage === location.href && sourceKey === currentSourceKey(video)) {
                    const expanded = [...resources.filter(item => !hls.includes(item)),
                        ...variants.flat()];
                    panel.show(expanded, false, pageMetadata);
                }
            }
            const dash = resources.find(resource => resource.kind === "dash" ||
                /\.mpd(?:[?#]|$)/i.test(resource.url));
            if (dash) {
                const info = await cachedRequest(`extract:${dash.url}`, {
                    type: "extractVideo", url: dash.url,
                    referer: pageMetadata.frameUrl || currentPage
                }, 30_000);
                if (currentPage !== location.href || sourceKey !== currentSourceKey(video)) return;
                if (info?.ok && Array.isArray(info.formats) && info.formats.length) {
                    const extracted = info.formats.map((format, index) => ({
                        url: dash.url, pageUrl: dash.url, title: info.title || pageMetadata.pageTitle,
                        kind: "extract", ext: format.ext, label: format.label,
                        size: format.size, sizeEstimated: !!format.size,
                        bitrateKbps: format.bitrateKbps, formatIndex: index,
                        referer: currentPage
                    }));
                    panel.show(extracted, false, {
                        ...pageMetadata, pageTitle: info.title || pageMetadata.pageTitle
                    });
                }
            }
            return;
        }

        const info = await cachedRequest(`extract:${currentPage}`, {
            type: "extractVideo", url: currentPage,
            referer: pageMetadata.frameUrl || currentPage
        }, 30_000);
        if (currentPage !== location.href || sourceKey !== currentSourceKey(video)) return;
        if (info?.ok && Array.isArray(info.formats) && info.formats.length) {
            const extracted = info.formats.map((format, index) => ({
                url: currentPage, pageUrl: currentPage,
                title: info.title || pageMetadata.pageTitle, kind: "extract",
                ext: format.ext, label: format.label, size: format.size,
                sizeEstimated: !!format.size, bitrateKbps: format.bitrateKbps,
                formatIndex: index, referer: currentPage
            }));
            panel.show(extracted, false, {
                ...pageMetadata, pageTitle: info.title || pageMetadata.pageTitle
            });
        } else {
            panel.show([], false, pageMetadata,
                info?.error || "Aucun format vidéo compatible n’a été trouvé.");
        }
    }

    function scan() {
        const videos = [...document.querySelectorAll("video")];
        for (const video of videos) {
            if (knownVideos.has(video)) continue;
            knownVideos.add(video);
            const panel = createPanel(video); panels.set(video, panel);
            const refresh = () => {
                clearTimeout(refreshTimers.get(video));
                refreshTimers.set(video, setTimeout(() => loadResources(video, panel), 100));
            };
            for (const event of ["loadstart", "loadedmetadata", "durationchange", "emptied"])
                video.addEventListener(event, refresh);
            video.addEventListener("encrypted", () => {
                if (isYouTubePage()) refresh();
                else panel.show([]);
            }, { once: true });
            new MutationObserver(refresh).observe(video, {
                attributes: true, subtree: true, childList: true, attributeFilter: ["src"]
            });
            refresh();
        }
        for (const video of [...knownVideos]) {
            if (!video.isConnected) {
                panels.get(video)?.host.remove(); knownVideos.delete(video);
            }
        }
    }

    chrome.runtime.onMessage.addListener(message => {
        if (message?.type !== "mediaResourceCaptured" || !message.resource) return;
        for (const video of document.querySelectorAll("video")) {
            const panel = panels.get(video);
            if (!panel || (video.mediaKeys && !isYouTubePage())) continue;
            panel.addCapturedResource(message.resource, {
                pageTitle: message.pageTitle || document.title || "",
                pageUrl: message.pageUrl || location.href,
                frameUrl: message.frameUrl || location.href,
                videoTitle: video.getAttribute("title") || video.getAttribute("aria-label") || "",
                duration: Number.isFinite(video.duration) ? video.duration : null
            });
        }
    });

    new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
    scan();
})();
