const API = "http://127.0.0.1:9614";
const ignore = new Set();

let queue = Promise.resolve();
function serialized(fn) {
    queue = queue.then(fn).catch(() => {});
    return queue;
}

// Types de flux supportés
const HLS_TYPES = [
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
    "video/mp2t"
];

const DASH_TYPES = [
    "application/dash+xml"
];

const EXT = { 
    "video/mp4": "mp4", 
    "video/webm": "webm", 
    "video/x-matroska": "mkv",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a", 
    "audio/webm": "weba", 
    "image/jpeg": "jpg",
    "image/png": "png", 
    "image/webp": "webp", 
    "image/gif": "gif",
    "application/zip": "zip", 
    "application/pdf": "pdf",
    "application/x-rar-compressed": "rar",
    "application/x-7z-compressed": "7z"
};

function kindOf(type) {
    if (HLS_TYPES.includes(type)) return "hls";
    if (DASH_TYPES.includes(type)) return "dash";
    if (type.startsWith("video/")) return "video";
    if (type.startsWith("audio/")) return "audio";
    if (type.startsWith("image/")) return "image";
    if (["application/zip", "application/pdf", "application/x-rar-compressed",
          "application/x-7z-compressed", "application/octet-stream",
          "application/x-msdownload"].includes(type)) return "file";
    return null;
}

function extFromUrl(url) {
    try {
        const m = new URL(url).pathname.match(
            /\.(mp4|webm|mkv|mp3|m4a|jpg|jpeg|png|webp|gif|zip|pdf|rar|7z|exe|iso|m3u8|mpd)$/i);
        return m ? m[1].toLowerCase() : null;
    } catch (e) { return null; }
}

function nameFromUrl(url) {
    try {
        const p = decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
        if (p) return p;
    } catch (e) {}
    return "";
}

function nameFromDisposition(cd) {
    if (!cd) return "";
    let m = cd.match(/filename\*\s*=\s*[^']*''([^;]+)/i);
    if (m) { 
        try { return decodeURIComponent(m[1].trim()); } catch (e) {} 
    }
    m = cd.match(/filename\s*=\s*"?([^";]+)"?/i);
    return m ? m[1].trim() : "";
}

async function alive() {
    try { 
        return (await fetch(API + "/ping")).ok; 
    } catch (e) { 
        return false; 
    }
}

async function sendToFlashDL(url, filename, referer, stream, mediaType = "file") {
    try {
        const cookies = (await chrome.cookies.getAll({ url }))
            .map(c => c.name + "=" + c.value).join("; ");
        
        const r = await fetch(API + "/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
                url, 
                filename, 
                referer, 
                cookies, 
                stream: !!stream,
                userAgent: navigator.userAgent,
                mediaType: mediaType
            })
        });
        return r.ok;
    } catch (e) { 
        return false; 
    }
}

async function inspectHlsWithFlashDL(url, referer) {
    try {
        const cookies = (await chrome.cookies.getAll({ url }))
            .map(c => c.name + "=" + c.value).join("; ");
        const r = await fetch(API + "/inspect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                url,
                referer: referer || "",
                cookies,
                userAgent: navigator.userAgent
            })
        });
        return r.ok ? await r.json() : { ok: false };
    } catch (e) {
        return { ok: false };
    }
}

async function youtubeRequest(path, url, referer, formatIndex) {
    try {
        const cookies = (await chrome.cookies.getAll({ url }))
            .map(c => c.name + "=" + c.value).join("; ");
        const r = await fetch(API + path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                url, referer: referer || "", cookies,
                userAgent: navigator.userAgent,
                ...(formatIndex === undefined ? {} : { formatIndex })
            })
        });
        const body = await r.json();
        return r.ok ? body : { ok: false, error: body.error || "FlashDL a refusé la requête." };
    } catch (e) {
        return { ok: false, error: "FlashDL ne répond pas. Lance l’application puis réessaie." };
    }
}

// ---------- 1. Détection des flux vidéo ----------
chrome.webRequest.onHeadersReceived.addListener(async (d) => {
    if (d.tabId < 0 || d.url.startsWith(API)) return;
    // YouTube expose des miniatures et des morceaux temporaires. La popup
    // interroge yt-dlp pour obtenir les vrais formats au lieu de les lister.
    const pageUrl = d.documentUrl || d.initiator || "";
    if (/^https?:\/\/(?:www\.|m\.)?(?:youtube\.com|youtube-nocookie\.com|youtu\.be)\//i.test(pageUrl)) return;
    
    const h = {};
    for (const x of d.responseHeaders || []) {
        h[x.name.toLowerCase()] = x.value;
    }

    const type = (h["content-type"] || "").split(";")[0].trim().toLowerCase();
    const isHls = HLS_TYPES.includes(type) || /\.m3u8(\?|$)/i.test(d.url);
    const isDash = DASH_TYPES.includes(type) || /\.mpd(\?|$)/i.test(d.url);
    
    let kind = isHls ? "hls" : (isDash ? "dash" : kindOf(type));
    if (!kind || type === "video/mp2t") return;

    // on ignore les morceaux de flux : seule la playlist nous intéresse
    if (!isHls && /\.(ts|m4s|m4f|cmfv|cmfa)(\?|$)/i.test(d.url)) return;

    let size = 0;
    if (!isHls && !isDash) {
        size = parseInt(h["content-length"] || "0", 10) || 0;
        const cr = h["content-range"];
        if (cr && cr.includes("/")) {
            size = parseInt(cr.split("/").pop(), 10) || size;
        }
        // Filtres de taille
        if ((kind === "video" || kind === "audio") && size && size < 300 * 1024) return;
        if (kind === "image" && size < 50 * 1024) return;
        if (kind === "file" && size < 1024 * 1024) return;
    }

    // Stockage dans le cache de l'onglet
    await serialized(async () => {
        const key = "tab_" + d.tabId;
        const store = await chrome.storage.session.get(key);
        const list = store[key] || [];
        if (list.some(x => x.url === d.url)) return;
        list.push({
            url: d.url, type, kind, size,
            ext: isHls ? "mp4" : (extFromUrl(d.url) || EXT[type] || "bin"),
            cdName: nameFromDisposition(h["content-disposition"]),
            urlName: nameFromUrl(d.url),
            referer: d.documentUrl || d.initiator || ""
        });
        await chrome.storage.session.set({ [key]: list });
        chrome.action.setBadgeText({ tabId: d.tabId, text: String(list.length) });
    });
    chrome.action.setBadgeBackgroundColor({ tabId: d.tabId, color: "#4CAF50" });
    
}, { 
    urls: ["<all_urls>"], 
    types: ["media", "xmlhttprequest", "image", "other", "main_frame", "sub_frame"] 
}, ["responseHeaders"]);

// Nettoyage lors du changement de page
const lastHost = {};
chrome.tabs.onUpdated.addListener((tabId, info) => {
    if (info.status === "loading" && info.url) {
        let host = "";
        try { host = new URL(info.url).host; } catch (e) {}
        if (lastHost[tabId] === host) return;     // même site : on garde la liste
        lastHost[tabId] = host;
        serialized(() => chrome.storage.session.remove("tab_" + tabId));
        chrome.action.setBadgeText({ tabId, text: "" });
    }
});

// ---------- 2. Interception des téléchargements ----------
chrome.downloads.onCreated.addListener(async (item) => {
    const url = item.finalUrl || item.url;
    if (!/^https?:/i.test(url) || ignore.has(url)) return;
    if (!(await alive())) return;

    const browserName = (item.filename || "").split(/[\\/]/).pop();
    const name = browserName || nameFromUrl(url);

    // Annuler le téléchargement navigateur
    chrome.downloads.cancel(item.id, () => {
        chrome.downloads.erase({ id: item.id });
    });
    
    // Envoyer à FlashDL
    const ok = await sendToFlashDL(url, name, item.referrer || "", false, "file");
    if (!ok) {
        ignore.add(url);
        chrome.downloads.download({ url });
    }
});

// ---------- 3. Menu contextuel (clic droit) ----------
chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
        id: "downloadWithFlashDL",
        title: "Télécharger avec FlashDL",
        contexts: ["link", "video", "audio"]
    });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
    if (!(await alive())) {
        chrome.notifications.create({
            type: "basic",
            iconUrl: "icon.png",
            title: "FlashDL",
            message: "FlashDL n'est pas démarré. Lance l'application d'abord."
        });
        return;
    }
    
    const url = info.linkUrl || info.srcUrl || info.pageUrl;
    const name = nameFromUrl(url);
    const isStream = info.mediaType === "video" || /\.(m3u8|mpd)$/i.test(url);
    
    await sendToFlashDL(url, name, tab.url, isStream, isStream ? "stream" : "file");
});

// ---------- 4. Communication avec le popup ----------
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg.type === "inspectHls") {
        inspectHlsWithFlashDL(msg.url, msg.referer)
            .then(result => reply(result));
        return true;
    }
    if (msg.type === "extractVideo") {
        youtubeRequest("/extract", msg.url, msg.referer)
            .then(result => reply(result));
        return true;
    }
    if (msg.type === "sendVideo") {
        youtubeRequest("/add", msg.url, msg.referer, msg.formatIndex)
            .then(result => reply(result));
        return true;
    }
    if (msg.type === "send") {
        sendToFlashDL(msg.url, msg.filename, msg.referer, msg.stream, msg.mediaType || "file")
            .then(ok => reply({ ok }));
        return true;
    }
    if (msg.type === "alive") {
        alive().then(ok => reply({ ok }));
        return true;
    }
    if (msg.type === "getMedia") {
        const key = "tab_" + msg.tabId;
        chrome.storage.session.get(key).then(store => {
            reply({ media: store[key] || [] });
        });
        return true;
    }
    if (msg.type === "clearMedia") {
        const key = "tab_" + msg.tabId;
        chrome.storage.session.remove(key).then(() => {
            chrome.action.setBadgeText({ tabId: msg.tabId, text: "" });
            reply({ ok: true });
        });
        return true;
    }
});
