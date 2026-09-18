from __future__ import annotations

import json
import random
import shutil
import subprocess
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
        folder_path = Path(folder)
        if not folder_path.is_absolute():
            folder_path = ROOT / folder_path
        result[category] = scan_directory(folder_path)
    return result


def build_rotation():
    lib = library()

    music = lib.get("music", [])
    ads = lib.get("ads", [])
    idents = lib.get("idents", [])
    sweepers = lib.get("sweepers", [])
    promos = lib.get("promos", [])

    if not music:
        return []

    rotation = [random.choice(music)]

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


def ffmpeg_command(files):
    inputs = []
    filters = []

    for index, filename in enumerate(files):
        absolute = (ROOT / filename).resolve()
        inputs += ["-i", str(absolute)]
        filters.append(
            f"[{index}:a]aresample=44100,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
        )

    labels = "".join(f"[a{i}]" for i in range(len(files)))
    filters.append(f"{labels}concat=n={len(files)}:v=0:a=1[out]")

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-nostdin",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-f", "mp3",
        "pipe:1",
    ]


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
            "stream": "/stream",
            "playlist": "/radio.m3u",
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
def radio_page():
    return render_template("radio.html")


@app.get("/radio.m3u")
def radio_m3u():
    # VLC and other players can open this playlist directly.
    body = "#EXTM3U\n#EXTINF:-1,zevRadio\nhttp://127.0.0.1:8080/stream\n"
    return Response(
        body,
        mimetype="audio/x-mpegurl",
        headers={"Content-Disposition": "inline; filename=zevRadio.m3u"},
    )


@app.get("/stream")
def stream():
    with lock:
        if not state["on_air"]:
            return "zevRadio is OFF AIR. Start the station from the web control panel.", 503

    if shutil.which("ffmpeg") is None:
        return "FFmpeg was not found. Install FFmpeg and put ffmpeg.exe on PATH.", 500

    def generate():
        while True:
            with lock:
                if not state["on_air"]:
                    break

            rotation = build_rotation()
            if not rotation:
                break

            process = None

            try:
                process = subprocess.Popen(
                    ffmpeg_command(rotation),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )

                while True:
                    chunk = process.stdout.read(16384)

                    if not chunk:
                        break

                    yield chunk

                    with lock:
                        if not state["on_air"]:
                            break

            except (BrokenPipeError, ConnectionResetError):
                break

            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait()

            with lock:
                if not state["on_air"]:
                    break

    return Response(
        stream_with_context(generate()),
        mimetype="audio/mpeg",
        headers={
            "Content-Type": "audio/mpeg",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Accept-Ranges": "none",
            "icy-name": "zevRadio",
        },
    )


if __name__ == "__main__":
    config = load_config()

    for folder in config["folders"].values():
        path = Path(folder)
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)

    app.run(
        host=config["host"],
        port=int(config["port"]),
        debug=False,
        threaded=True,
    )
