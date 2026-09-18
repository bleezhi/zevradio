from __future__ import annotations

import json
import random
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
FFMPEG_NAME = "ffmpeg.exe" if __import__("os").name == "nt" else "ffmpeg"
FFMPEG_CANDIDATES = [
    ROOT / "ffmpeg" / FFMPEG_NAME,
    ROOT.parent / "ffmpeg" / FFMPEG_NAME,
]

def find_ffmpeg():
    for path in FFMPEG_CANDIDATES:
        if path.is_file():
            return path
    return None


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


def ffmpeg_command(filename):
    absolute = (ROOT / filename).resolve()

    return [
        str(find_ffmpeg()), "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-re", "-i", str(absolute), "-vn",
        "-ac", "2", "-ar", "44100",
        "-c:a", "libmp3lame", "-b:a", "128k",
        "-f", "mp3", "pipe:1",
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
    stream_url = request.host_url.rstrip("/") + "/stream"
    body = "#EXTM3U\n#EXTINF:-1,zevRadio\n" + stream_url + "\n"
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

    if not FFMPEG_PATH.is_file():
        return "FFmpeg was not found. Put ffmpeg.exe in the zevRadio/ffmpeg/ folder.", 500

    def generate():
        encoder = None

        try:
            encoder = subprocess.Popen(
                [
                    str(ffmpeg_path), "-hide_banner", "-loglevel", "warning",
                    "-nostdin",
                    "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0",
                    "-c:a", "libmp3lame", "-b:a", "128k",
                    "-f", "mp3", "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            while True:
                with lock:
                    if not state["on_air"]:
                        break

                rotation = build_rotation()
                if not rotation:
                    break

                for track in rotation:
                    with lock:
                        if not state["on_air"]:
                            break
                        state["now_playing"] = track

                    decoder = None
                    try:
                        decoder = subprocess.Popen(
                            [
                                str(ffmpeg_path), "-hide_banner", "-loglevel", "warning",
                                "-nostdin", "-re", "-i", str((ROOT / track).resolve()),
                                "-vn", "-f", "s16le", "-ar", "44100", "-ac", "2", "pipe:1",
                            ],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            bufsize=0,
                        )

                        while True:
                            pcm = decoder.stdout.read(32768)
                            if not pcm:
                                break
                            encoder.stdin.write(pcm)

                            with lock:
                                if not state["on_air"]:
                                    break

                        decoder.wait()

                        if decoder.returncode != 0:
                            error = decoder.stderr.read().decode("utf-8", errors="replace").strip()
                            if error:
                                print("[zevRadio] FFmpeg decoder failed for " + track + ":\\n" + error)

                    except (BrokenPipeError, ConnectionResetError):
                        break
                    finally:
                        if decoder is not None and decoder.poll() is None:
                            decoder.kill()
                            decoder.wait()

                    with lock:
                        if not state["on_air"]:
                            break

                if encoder.poll() is not None:
                    break

        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if encoder is not None:
                if encoder.poll() is None:
                    try:
                        encoder.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
                    encoder.wait()

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
