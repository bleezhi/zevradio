from __future__ import annotations

import json
import random
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}

# The UI lives under web/, so explicitly tell Flask where to find templates
# and static assets. Flask otherwise looks for templates/ and static/ beside
# this Python file.
app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static",
    static_url_path="/static",
)

lock = threading.Lock()
state = {"on_air": False, "now_playing": None}


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(config):
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def scan_directory(path: Path):
    if not path.exists():
        return []
    return sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


def library():
    config = load_config()
    result = {}
    for category, folder in config["folders"].items():
        result[category] = scan_directory(ROOT / folder)
    return result


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    with lock:
        return jsonify({
            "station": load_config()["station_name"],
            "on_air": state["on_air"],
            "now_playing": state["now_playing"],
            "stream": "/radio",
        })


@app.get("/api/config")
def api_config():
    return jsonify(load_config())


@app.get("/api/library")
def api_library():
    return jsonify(library())


@app.post("/api/directories")
def add_directory():
    data = request.get_json(silent=True) or {}
    category = str(data.get("category", "")).strip().lower()
    directory = str(data.get("directory", "")).strip()

    config = load_config()
    if category not in config["folders"]:
        return jsonify({"error": "Unknown category"}), 400
    if not directory:
        return jsonify({"error": "Directory is required"}), 400

    path = Path(directory)
    if not path.is_absolute():
        path = ROOT / path

    config["folders"][category] = str(path)
    save_config(config)
    return jsonify({"ok": True, "category": category, "directory": str(path)})


@app.delete("/api/directories")
def remove_directory():
    data = request.get_json(silent=True) or {}
    category = str(data.get("category", "")).strip().lower()

    config = load_config()
    if category not in config["folders"]:
        return jsonify({"error": "Unknown category"}), 400

    defaults = {
        "music": "audio/music",
        "ads": "audio/ads",
        "promos": "audio/promos",
        "idents": "audio/idents",
        "sweepers": "audio/sweepers",
        "news": "audio/news",
        "weather": "audio/weather",
    }
    config["folders"][category] = defaults[category]
    save_config(config)
    return jsonify({"ok": True})


@app.post("/api/radio")
def radio_control():
    data = request.get_json(silent=True) or {}
    action = data.get("action")

    with lock:
        if action == "start":
            state["on_air"] = True
            return jsonify({"ok": True, "on_air": True})
        if action == "stop":
            state["on_air"] = False
            state["now_playing"] = None
            return jsonify({"ok": True, "on_air": False})

    return jsonify({"error": "Use action=start or action=stop"}), 400


@app.post("/api/test/next")
def test_next():
    tracks = library().get("music", [])
    if not tracks:
        return jsonify({"error": "No music files found"}), 404

    with lock:
        state["now_playing"] = random.choice(tracks)
    return jsonify({"ok": True, "now_playing": state["now_playing"]})


@app.get("/radio")
def radio():
    return (
        "zevRadio audio backend is not enabled yet. "
        "The web control/API is ready for the FFmpeg streaming backend."
    ), 501


if __name__ == "__main__":
    config = load_config()
    for folder in config["folders"].values():
        (ROOT / folder).mkdir(parents=True, exist_ok=True)
    app.run(host=config["host"], port=int(config["port"]), debug=False)
