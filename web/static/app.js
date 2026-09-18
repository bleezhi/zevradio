async function getJSON(url, options) {
  const r = await fetch(url, options);
  return r.json();
}

async function refresh() {
  const [status, config, lib] = await Promise.all([
    getJSON("/api/status"),
    getJSON("/api/config"),
    getJSON("/api/library")
  ]);

  document.querySelector("#station").textContent = status.station;
  document.querySelector("#now").textContent =
    status.now_playing ? "Now playing: " + status.now_playing : "Nothing playing";

  const badge = document.querySelector("#status");
  badge.textContent = status.on_air ? "ON AIR" : "OFF AIR";
  badge.className = "status " + (status.on_air ? "on" : "off");

  document.querySelector("#toggle").textContent =
    status.on_air ? "STOP RADIO" : "START RADIO";

  const folders = document.querySelector("#folders");
  folders.innerHTML = Object.entries(config.folders)
    .map(([k, v]) => '<div class="folder"><b>' + k + '</b>: ' + v + '</div>')
    .join("");

  const library = document.querySelector("#library");
  library.innerHTML = Object.entries(lib).map(([k, files]) => {
    const list = files.length
      ? "<ul>" + files.map(f => "<li>" + f + "</li>").join("") + "</ul>"
      : "<div class=\"muted\">No audio files found.</div>";
    return "<div class=\"item\"><h3>" + k + " (" + files.length + ")</h3>" + list + "</div>";
  }).join("");
}

document.querySelector("#toggle").onclick = async () => {
  const status = await getJSON("/api/status");
  await getJSON("/api/radio", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: status.on_air ? "stop" : "start"})
  });
  refresh();
};

document.querySelector("#next").onclick = async () => {
  await getJSON("/api/test/next", {method: "POST"});
  refresh();
};

document.querySelector("#save").onclick = async () => {
  const category = document.querySelector("#category").value;
  const directory = document.querySelector("#directory").value;
  await getJSON("/api/directories", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({category, directory})
  });
  refresh();
};

refresh();
setInterval(refresh, 3000);
