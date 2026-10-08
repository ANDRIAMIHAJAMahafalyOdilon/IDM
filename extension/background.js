const API = "http://127.0.0.1:9614";
const ignore = new Set();

const EXT = { "video/mp4": "mp4", "video/webm": "webm", "audio/mpeg": "mp3",
              "audio/mp4": "m4a", "audio/webm": "weba", "image/jpeg": "jpg",
              "image/png": "png", "image/webp": "webp", "image/gif": "gif",
              "application/zip": "zip", "application/pdf": "pdf" };

function kindOf(type) {
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
      /\.(mp4|webm|mkv|mp3|m4a|jpg|jpeg|png|webp|gif|zip|pdf|rar|7z|exe|iso)$/i);
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

// nom proposé par le serveur (Content-Disposition)
function nameFromDisposition(cd) {
  if (!cd) return "";
  let m = cd.match(/filename\*\s*=\s*[^']*''([^;]+)/i);
  if (m) { try { return decodeURIComponent(m[1].trim()); } catch (e) {} }
  m = cd.match(/filename\s*=\s*"?([^";]+)"?/i);
  return m ? m[1].trim() : "";
}

async function alive() {
  try { return (await fetch(API + "/ping")).ok; } catch (e) { return false; }
}

async function sendToFlashDL(url, filename, referer) {
  try {
    const cookies = (await chrome.cookies.getAll({ url }))
      .map(c => c.name + "=" + c.value).join("; ");
    const r = await fetch(API + "/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, filename, referer, cookies,
                             userAgent: navigator.userAgent })
    });
    return r.ok;
  } catch (e) { return false; }
}

// ---------- 1. détection des fichiers de la page ----------
chrome.webRequest.onHeadersReceived.addListener(async (d) => {
  if (d.tabId < 0 || d.url.startsWith(API)) return;
  const h = {};
  for (const x of d.responseHeaders || []) h[x.name.toLowerCase()] = x.value;

  const type = (h["content-type"] || "").split(";")[0].trim().toLowerCase();
  const kind = kindOf(type);
  if (!kind || type === "video/mp2t") return;

  let size = parseInt(h["content-length"] || "0", 10) || 0;
  const cr = h["content-range"];
  if (cr && cr.includes("/")) size = parseInt(cr.split("/").pop(), 10) || size;

  if ((kind === "video" || kind === "audio") && size < 300 * 1024) return;
  if (kind === "image" && size < 50 * 1024) return;
  if (kind === "file" && size < 1024 * 1024) return;

  const key = "tab_" + d.tabId;
  const store = await chrome.storage.session.get(key);
  const list = store[key] || [];
  if (list.some(x => x.url === d.url)) return;

  list.push({
    url: d.url, type, kind, size,
    ext: extFromUrl(d.url) || EXT[type] || "bin",
    cdName: nameFromDisposition(h["content-disposition"]),
    urlName: nameFromUrl(d.url)
  });
  await chrome.storage.session.set({ [key]: list });
  chrome.action.setBadgeText({ tabId: d.tabId, text: String(list.length) });
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "image", "other"] },
   ["responseHeaders"]);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading" && info.url) {
    chrome.storage.session.remove("tab_" + tabId);
    chrome.action.setBadgeText({ tabId, text: "" });
  }
});

// ---------- 2. interception des téléchargements du navigateur ----------
chrome.downloads.onCreated.addListener(async (item) => {
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url) || ignore.has(url)) return;
  if (!(await alive())) return;

  // nom choisi par le navigateur (sans le dossier), sinon nom dans l'adresse
  const browserName = (item.filename || "").split(/[\\/]/).pop();
  const name = browserName || nameFromUrl(url);

  chrome.downloads.cancel(item.id, () => chrome.downloads.erase({ id: item.id }));
  const ok = await sendToFlashDL(url, name, item.referrer || "");
  if (!ok) {
    ignore.add(url);
    chrome.downloads.download({ url });
  }
});

// ---------- 3. demandes venant du popup ----------
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "send") {
    sendToFlashDL(msg.url, msg.filename, msg.referer).then(ok => reply({ ok }));
    return true;
  }
  if (msg.type === "alive") {
    alive().then(ok => reply({ ok }));
    return true;
  }
});