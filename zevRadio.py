from __future__ import annotations

import json
import random
import subprocess
import tempfile
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}

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


def build_rotation():
    """
    Build a small repeatable test rotation.

    This is intentionally simple for the first live backend:
      music -> ident -> music -> ad -> music -> sweeper -> ad

    Empty categories are skipped, so the station can still run with a
    tiny library while it is being tested locally.
    """
    lib = library()

    music = lib.get("music", [])
    ads = lib.get("ads", [])
    idents = lib.get("idents", [])
    sweepers = lib.get("sweepers", [])
    promos = lib.get("promos", [])

    if not music:
        return []

    rotation = []
    rotation.append(random.choice(music))

    if idents:
        rotation.append(random.choice(idents))

    rotation.append(random.choice(music))

    if ads:
        rotation.append(random.choice(ads))

    rotation.append(random.choice(music))

    if sweepers:
        rotation.append(random.choice(sweepers))
    elif promos:
        rotation.append(random.choice(promos))

    if ads:
        rotation.append(random.choice(ads))

    return rotation


def make_concat_file(files):
    temp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="zevradio_",
        delete=False,
        encoding="utf-8",
    )

    for filename in files:
        absolute = (ROOT / filename).resolve()
        escaped = str(absolute).replace("'", "'\\''")
        temp.write(f"file '{escaped}'\n")

    temp.close()
    return Path(temp.name)


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
    """
    Local HTTP audio stream.

    FFmpeg is started only when a client connects to /radio and the station
    is ON AIR. The process continuously loops the generated test rotation.
    This is deliberately local-first so the stream can be tested before
    connecting zevRadio to anything external such as SimTX.
    """
    with lock:
        if not state["on_air"]:
            return "zevRadio is OFF AIR. Start the station from the web control panel.", 503

    rotation = build_rotation()
    if not rotation:
        return "No music files found in the configured music directory.", 503

    concat_file = make_concat_file(rotation)

    def generate():
        process = None
        try:
            process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-re",
                    "-stream_loop", "-1",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_file),
                    "-vn",
                    "-ac", "2",
                    "-ar", "44100",
                    "-c:a", "libmp3lame",
                    "-b:a", "128k",
                    "-f", "mp3",
                    "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            while True:
                chunk = process.stdout.read(16384)
                if not chunk:
                    break

                # Update now-playing approximately from the generated
                # rotation. This is enough for the first test backend.
                yield chunk

                with lock:
                    if not state["on_air"]:
                        break

        except FileNotFoundError:
            yield b""
        finally:
            if process is not None:
                process.kill()
                process.wait()

            try:
                concat_file.unlink()
            except FileNotFoundError:
                pass

    return Response(
        stream_with_context(generate()),
        mimetype="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-ZevRadio-Stream": "local-test",
        },
    )


if __name__ == "__main__":
    config = load_config()
    for folder in config["folders"].values():
        path = Path(folder)
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)

    app.run(host=config["host"], port=int(config["port"]), debug=False, threaded=True)
