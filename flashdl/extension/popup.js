const API = "http://127.0.0.1:9615";

let currentTabId = null;
let currentTabUrl = null;

// Vérifier si Roch Xd est en ligne
async function checkStatus() {
    try {
        const r = await fetch(API + "/ping");
        const state = r.ok ? await r.json() : null;
        const online = state?.app === "Roch Xd" && state?.version === "1.2.0";
        updateStatus(online);
        return online;
    } catch (e) {
        updateStatus(false);
        return false;
    }
}

function updateStatus(online) {
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    
    if (online) {
        dot.className = 'status-dot online';
        text.textContent = 'Connecté';
    } else {
        dot.className = 'status-dot offline';
        text.textContent = 'Hors ligne';
    }
}

// Formater la taille
function formatSize(bytes) {
    if (!bytes) return '?';
    const units = ['o', 'Ko', 'Mo', 'Go'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return `${bytes.toFixed(1)} ${units[i]}`;
}

// Obtenir le label du type
function getTypeLabel(kind) {
    const labels = {
        'video': 'Vidéo',
        'hls': 'HLS',
        'dash': 'DASH',
        'audio': 'Audio',
        'image': 'Image',
        'file': 'Fichier'
    };
    return labels[kind] || kind;
}

// Charger les médias détectés
async function loadMedia() {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs[0]) return;
    
    currentTabId = tabs[0].id;
    currentTabUrl = tabs[0].url;

    let host = "";
    try { host = new URL(currentTabUrl).hostname.toLowerCase(); } catch (e) {}
    if (host === "youtu.be" || host.endsWith(".youtube.com") || host === "youtube.com") {
        displayExtractedVideo(currentTabUrl);
        return;
    }
    
    chrome.runtime.sendMessage(
        { type: "getMedia", tabId: currentTabId },
        (response) => {
            if (response && response.media && response.media.length) {
                displayMedia(response.media);
            } else {
                displayExtractedVideo(currentTabUrl);
            }
        }
    );
}

function displayExtractedVideo(url) {
    const content = document.getElementById('content');
    content.innerHTML = '<div class="empty">Analyse des formats vidéo…</div>';
    chrome.runtime.sendMessage({ type: "extractVideo", url, referer: url }, result => {
        if (!result || !result.ok || !Array.isArray(result.formats) || !result.formats.length) {
            content.innerHTML = '';
            const error = document.createElement('div');
            error.className = 'empty';
            error.textContent = result?.error || 'Aucun format vidéo disponible sur cette page.';
            content.appendChild(error);
            return;
        }

        const item = document.createElement('div');
        item.className = 'media-item';
        const info = document.createElement('div');
        info.className = 'media-info';
        const name = document.createElement('div');
        name.className = 'media-name';
        name.textContent = result.title || 'Vidéo détectée';
        const meta = document.createElement('div');
        meta.className = 'media-meta';
        meta.textContent = `Formats extraits par yt-dlp • ${new URL(url).hostname}`;
        const controls = document.createElement('div');
        controls.className = 'variant-controls';
        const select = document.createElement('select');
        select.className = 'variant-select';
        const details = document.createElement('div');
        details.className = 'variant-size';
        const button = document.createElement('button');
        button.className = 'download-variant';
        button.textContent = 'Télécharger cette qualité';

        const formats = result.formats;
        formats.forEach((format, index) => {
            const sizeText = format.size ? formatSize(format.size) : 'taille inconnue';
            select.add(new Option(`${format.label} • ${sizeText}`, String(index)));
        });
        const updateDetails = () => {
            const format = formats[Number(select.value)];
            if (format) details.textContent = `Qualité : ${format.label} • Taille estimée : ${format.size ? formatSize(format.size) : 'indisponible'}`;
        };
        select.addEventListener('change', updateDetails);
        updateDetails();
        button.addEventListener('click', () => {
            button.disabled = true;
            button.textContent = 'Ajout en cours…';
            chrome.runtime.sendMessage({
                type: 'sendVideo', url, referer: url,
                formatIndex: Number(select.value)
            }, response => {
                button.disabled = false;
                button.textContent = 'Télécharger cette qualité';
                if (response?.ok) alert(`Vidéo ajoutée à Roch Xd : ${result.title || 'vidéo'}`);
                else alert(response?.error || 'Erreur lors de l’ajout à Roch Xd.');
            });
        });

        controls.append(select, details, button);
        info.append(name, meta, controls);
        item.append(info);
        content.replaceChildren(item);
    });
}

function inspectHls(media) {
    return new Promise(resolve => {
        chrome.runtime.sendMessage({
            type: "inspectHls",
            url: media.url,
            referer: media.referer || currentTabUrl || ""
        }, response => resolve(response || { ok: false }));
    });
}

// Afficher les médias
function displayMedia(mediaList) {
    const content = document.getElementById('content');
    
    if (!mediaList || mediaList.length === 0) {
        content.innerHTML = '<div class="empty">Aucun média détecté sur cette page</div>';
        return;
    }
    
    const listDiv = document.createElement('div');
    listDiv.className = 'media-list';
    
    mediaList.forEach((media, index) => {
        const item = document.createElement('div');
        item.className = 'media-item';
        
        const name = media.cdName || (media.kind === 'hls' ? 'Vidéo en streaming' : media.urlName) || `Média ${index + 1}`;
        const size = formatSize(media.size);
        const typeClass = `type-${media.kind}`;
        
        const info = document.createElement('div');
        info.className = 'media-info';
        const nameNode = document.createElement('div');
        nameNode.className = 'media-name';
        nameNode.title = name;
        nameNode.textContent = name;
        const meta = document.createElement('div');
        meta.className = 'media-meta';
        let host = '';
        try { host = new URL(media.url).hostname; } catch (e) {}
        meta.textContent = `${size} • ${host}`;
        info.append(nameNode, meta);

        const type = document.createElement('span');
        type.className = `media-type ${typeClass}`;
        type.textContent = getTypeLabel(media.kind);
        item.append(info, type);

        if (media.kind === 'hls') {
            const controls = document.createElement('div');
            controls.className = 'variant-controls';
            const select = document.createElement('select');
            select.className = 'variant-select';
            select.disabled = true;
            select.add(new Option('Analyse des qualités…', ''));
            const details = document.createElement('div');
            details.className = 'variant-size';
            details.textContent = 'Résolution et taille en cours d’analyse…';
            const button = document.createElement('button');
            button.className = 'download-variant';
            button.textContent = 'Télécharger cette qualité';
            button.disabled = true;
            controls.append(select, details, button);
            info.appendChild(controls);

            let variants = [];
            const updateDetails = () => {
                const variant = variants[Number(select.value)];
                if (!variant) return;
                const sizeText = variant.size ? formatSize(variant.size) : 'non estimée';
                details.textContent = `Qualité : ${variant.label} • Taille estimée : ${sizeText}`;
            };
            select.addEventListener('change', updateDetails);
            button.addEventListener('click', () => {
                const variant = variants[Number(select.value)];
                if (variant) sendToFlashDL({ ...media, url: variant.url });
            });

            inspectHls(media).then(result => {
                if (!result.ok || !Array.isArray(result.formats) || !result.formats.length) {
                    details.textContent = 'Impossible de lire cette playlist HLS. Vérifiez que Roch Xd est démarré.';
                    return;
                }
                variants = result.formats;
                select.replaceChildren();
                variants.forEach((variant, variantIndex) => {
                    const sizeText = variant.size ? formatSize(variant.size) : 'taille inconnue';
                    select.add(new Option(`${variant.label} • ${sizeText}`, String(variantIndex)));
                });
                select.disabled = false;
                button.disabled = false;
                updateDetails();
                if (!result.variants_found && !variants.some(variant => variant.size)) {
                    details.textContent = 'La playlist ne fournit qu’une qualité et sa taille ne peut pas être estimée.';
                }
            });
        } else {
            item.addEventListener('click', () => sendToFlashDL(media));
            if (!media.size && (media.kind === 'video' || media.kind === 'audio')) {
                inspectHls(media).then(result => {
                    if (!result.ok || !result.size) return;
                    media.size = result.size;
                    meta.textContent = `${formatSize(result.size)} • ${host}`;
                });
            }
        }
        
        listDiv.appendChild(item);
    });
    
    content.innerHTML = '';
    content.appendChild(listDiv);
}

// Envoyer à Roch Xd
async function sendToFlashDL(media) {
    const online = await checkStatus();
    if (!online) {
        alert("Roch Xd n'est pas démarré. Lance l'application d'abord.");
        return;
    }
    
    const filename = media.cdName || media.urlName || `download.${media.ext}`;
    const isStream = media.kind === 'hls' || media.kind === 'dash';
    
    chrome.runtime.sendMessage({
        type: "send",
        url: media.url,
        filename: filename,
        referer: media.referer || currentTabUrl || '',
        stream: isStream,
        mediaType: media.kind
    }, (response) => {
        if (response && response.ok) {
            // Feedback visuel
            alert(`Ajouté à Roch Xd : ${filename}`);
        } else if (response && response.cancelled) {
            alert("Téléchargement annulé dans Roch Xd.");
        } else {
            alert("Erreur lors de l'ajout à Roch Xd");
        }
    });
}

// Effacer la liste
function clearMedia() {
    if (currentTabId) {
        chrome.runtime.sendMessage(
            { type: "clearMedia", tabId: currentTabId },
            () => loadMedia()
        );
    }
}

// Initialisation
document.addEventListener('DOMContentLoaded', () => {
    checkStatus();
    loadMedia();
    
    document.getElementById('refreshBtn').addEventListener('click', () => {
        checkStatus();
        loadMedia();
    });
    
    document.getElementById('clearBtn').addEventListener('click', clearMedia);
});
