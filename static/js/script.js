// Page-language strings, set by the template as window.I18N.
// Falls back to English when a page has no translations (e.g. /privacy).
const I18N = window.I18N || {};
function tr(key, fallback) {
  return I18N[key] || fallback;
}

function isXUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname === "x.com" ||
      parsed.hostname === "www.x.com" ||
      parsed.hostname === "twitter.com" ||
      parsed.hostname === "www.twitter.com" ||
      parsed.hostname.endsWith(".x.com") ||
      parsed.hostname.endsWith(".twitter.com");
  } catch (err) {
    return false;
  }
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function pasteLink() {
  const input = document.getElementById("urlInput");
  const status = document.getElementById("status");
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      input.value = text.trim();
      input.focus();
      status.innerText = "";
      status.classList.remove("error");
    } else {
      status.innerText = tr("clipboard_empty", "Clipboard is empty — copy an X link first, then tap Paste.");
      status.classList.add("error");
    }
  } catch (err) {
    status.innerText = tr("clipboard_error", "Could not read the clipboard. Please paste the X link manually (Ctrl+V / long-press → Paste).");
    status.classList.add("error");
    input.focus();
  }
}

function showResults(data) {
  const status = document.getElementById("status");
  let html = "";

  if (data.thumbnail) {
    html += `<div class="video-thumb"><img src="${escapeHtml(data.thumbnail)}" alt="${escapeHtml(tr("thumb_alt", "Video thumbnail"))}"></div>`;
  }

  if (data.videos && data.videos.length) {
    const count = data.videos.length;
    html += `<p class="result-title">${escapeHtml(tr("found", "Video found — choose a quality ({n} available):").replace("{n}", count))}</p>`;
    html += `<div class="download-options">`;
    data.videos.forEach(function (video) {
      // Route the download through TwitterDown so it saves as a file even
      // where X's video server blocks direct links, and so the browser
      // downloads instead of opening its video player.
      const via = "/get?url=" + encodeURIComponent(video.url) + "&label=" + encodeURIComponent(video.label);
      html += `<a href="${escapeHtml(via)}" download>${escapeHtml(tr("download_quality", "Download {label}: mp4").replace("{label}", video.label))}</a>`;
    });
    html += `</div>`;
  } else if (data.file) {
    html += `<div class="download-options">
      <a href="${escapeHtml(data.file)}" download>${escapeHtml(tr("download_mp4", "Download MP4"))}</a>
    </div>`;
  }

  html += `<p><a href="#" class="download-another" onclick="downloadAnother(event)">${escapeHtml(tr("download_another", "Download Another Video"))}</a></p>`;
  status.innerHTML = html;
}

function downloadAnother(event) {
  event.preventDefault();
  const input = document.getElementById("urlInput");
  const status = document.getElementById("status");
  input.value = "";
  status.innerHTML = "";
  status.classList.remove("error");
  input.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function download() {
  const urlInput = document.getElementById("urlInput");
  const url = urlInput.value.trim();
  const status = document.getElementById("status");
  const downloadBtn = document.getElementById("downloadBtn");

  status.classList.remove("error");
  status.innerHTML = "";

  if (!url) {
    status.innerText = tr("paste_first", "Please paste an X video link first.");
    status.classList.add("error");
    urlInput.focus();
    return;
  }

  if (!isXUrl(url)) {
    status.innerText = tr("x_only", "TwitterDown is for X only. Please paste a link from x.com or twitter.com.");
    status.classList.add("error");
    return;
  }

  status.innerText = tr("fetching", "Fetching download links...");
  downloadBtn.disabled = true;

  try {
    let response = await fetch("/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });

    let data = await response.json();

    if (data.error) {
      status.innerText = tr("could_not", "Could not download that X video: ") + data.error;
      status.classList.add("error");
    } else {
      showResults(data);
    }
  } catch (err) {
    status.innerText = tr("went_wrong", "Something went wrong. Please try again.");
    status.classList.add("error");
  } finally {
    downloadBtn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", function () {
  const pasteBtn = document.getElementById("pasteBtn");
  if (pasteBtn) {
    pasteBtn.addEventListener("click", pasteLink);
  }
  const urlInput = document.getElementById("urlInput");
  if (urlInput) {
    urlInput.addEventListener("input", function () {
      const status = document.getElementById("status");
      status.innerText = "";
      status.classList.remove("error");
    });
    urlInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        download();
      }
    });
  }
});
