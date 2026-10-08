function fmt(n) {
  if (!n) return "taille inconnue";
  const u = ["o", "Ko", "Mo", "Go"];
  let i = 0;
  while (n >= 1024 && i < 3) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + " " + u[i];
}

function clean(s) {
  return (s || "").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").replace(/\s+/g, " ")
    .trim().slice(0, 120);
}

// nom final : serveur > titre de la page (vidéo/audio) > nom dans l'adresse
function finalName(it, tabTitle) {
  if (it.cdName) return clean(it.cdName);
  if ((it.kind === "video" || it.kind === "audio") && clean(tabTitle)) {
    return clean(tabTitle) + "." + it.ext;
  }
  return clean(it.urlName) || ("fichier." + it.ext);
}

const ICON = { video: "🎬", audio: "🎵", image: "🖼", file: "📦" };

(async () => {
  const status = document.getElementById("status");
  const list = document.getElementById("list");
  const empty = document.getElementById("empty");

  const alive = await chrome.runtime.sendMessage({ type: "alive" });
  status.textContent = alive.ok ? "FlashDL connecté" : "FlashDL fermé : ouvre l'application";

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const key = "tab_" + tab.id;
  const store = await chrome.storage.session.get(key);
  const items = (store[key] || []).sort((a, b) => b.size - a.size);

  if (!items.length) { empty.style.display = "block"; return; }

  for (const it of items) {
    const name = finalName(it, tab.title);
    const row = document.createElement("div");
    row.className = "row";

    const info = document.createElement("div");
    info.className = "info";
    const nameEl = document.createElement("div");
    nameEl.className = "name";
    nameEl.textContent = ICON[it.kind] + " " + name;
    nameEl.title = it.url;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = fmt(it.size) + "  •  " + it.type;
    info.append(nameEl, meta);

    const btn = document.createElement("button");
    btn.textContent = "Télécharger";
    btn.disabled = !alive.ok;
    btn.onclick = async () => {
      btn.disabled = true;
      const r = await chrome.runtime.sendMessage({
        type: "send", url: it.url, filename: name, referer: tab.url });
      btn.textContent = r.ok ? "Envoyé ✓" : "Échec";
    };

    row.append(info, btn);
    list.append(row);
  }
})();