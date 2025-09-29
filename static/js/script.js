async function download() {
  const url = document.getElementById("urlInput").value;
  const status = document.getElementById("status");

  status.innerText = "⏳ Fetching download link...";

  try {
    let response = await fetch("/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });

    let data = await response.json();

    if (data.error) {
      status.innerText = "❌ " + data.error;
    } else {
      status.innerHTML = `
        <div class="download-options">
          <a href="${data.file}" download>⬇️ Download MP4</a>
        </div>`;
    }
  } catch (err) {
    status.innerText = "⚠️ Something went wrong.";
  }
}
