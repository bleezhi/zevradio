import json
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
AUDIO = BASE / "audio"
DATA = BASE / "data"
FFMPEG_DIR = BASE / "ffmpeg"

# IMPORTANT: only use this device. Do not fall back to CABLE In 16ch.
DEVICE_NAME = "CABLE Input"

SAMPLE_RATE = 48000
CHANNELS = 2

SUPPORTED_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac",
    ".opus", ".wma", ".aiff", ".aif"
}

# Weighted radio rotation.
ROTATION = [
    ("music", 1.00),
    ("music", 1.00),
    ("music", 1.00),
    ("idents", 0.15),
    ("ads", 0.25),
    ("promos", 0.15),
    ("sweepers", 0.20),
]

running = True
current_process = None


def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def find_ffmpeg():
    candidates = [
        FFMPEG_DIR / "ffmpeg.exe",
        FFMPEG_DIR / "bin" / "ffmpeg.exe",
        BASE / "ffmpeg.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return "ffmpeg"


def find_audio_device():
    try:
        import sounddevice as sd
    except ImportError:
        log("ERROR: sounddevice is not installed.")
        log("Run: pip install -r requirements.txt")
        sys.exit(1)

    devices = sd.query_devices()

    # Exact match only. Never use CABLE In 16ch as a fallback.
    for index, device in enumerate(devices):
        if device["name"].strip().lower() == DEVICE_NAME.lower() and device["max_output_channels"] > 0:
            log(
                f"Audio output: {device['name']} "
                f"(device {index}, {device['max_output_channels']}ch, "
                f"default {device['default_samplerate']} Hz)"
            )
            return index

    log(f"ERROR: exact output device '{DEVICE_NAME}' was not found.")
    log("Available output devices:")
    for index, device in enumerate(devices):
        if device["max_output_channels"] > 0:
            log(f"  [{index}] {device['name']}")
    sys.exit(1)


def write_now_playing(path, category):
    DATA.mkdir(parents=True, exist_ok=True)

    payload = {
        "title": path.stem,
        "file": path.name,
        "category": category,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    (DATA / "now_playing.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def clear_now_playing():
    target = DATA / "now_playing.json"
    try:
        if target.exists():
            target.unlink()
    except OSError:
        pass


def append_history(path, category):
    DATA.mkdir(parents=True, exist_ok=True)

    history_file = DATA / "history.json"

    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
        if not isinstance(history, list):
            history = []
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    history.append({
        "title": path.stem,
        "file": path.name,
        "category": category,
        "played_at": datetime.now(timezone.utc).isoformat(),
    })

    history = history[-1000:]

    history_file.write_text(
        json.dumps(history, indent=2),
        encoding="utf-8",
    )


def files_in(category):
    folder = AUDIO / category

    if not folder.exists():
        return []

    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def choose_file(category, recent):
    files = files_in(category)

    if not files:
        return None

    available = [path for path in files if path not in recent]

    # If there is only one file in a category, repeating it is unavoidable.
    if not available:
        available = files

    return random.choice(available)


def choose_category():
    available = [(category, weight) for category, weight in ROTATION if files_in(category)]

    if not available:
        return "music"

    categories = [item[0] for item in available]
    weights = [item[1] for item in available]

    return random.choices(categories, weights=weights, k=1)[0]


def play_file(ffmpeg, device_index, path, category):
    global current_process

    log(f"▶ {category.upper()}: {path.name}")
    write_now_playing(path, category)

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        # CRITICAL: decode at real-time speed instead of as fast as possible.
        "-re",
        "-i", str(path),
        "-vn",
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "pipe:1",
    ]

    try:
        import sounddevice as sd

        current_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        block_bytes = 4096

        with sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            device=device_index,
            blocksize=1024,
        ) as output:
            while running:
                data = current_process.stdout.read(block_bytes)

                if not data:
                    break

                output.write(data)

        return_code = current_process.wait()

        if return_code != 0 and running:
            error = current_process.stderr.read().decode(
                errors="replace"
            ).strip()
            log(f"FFmpeg failed ({return_code}): {error or 'unknown error'}")
            return False

        if running:
            append_history(path, category)
            return True

        return False

    except Exception as exc:
        log(f"Playback error: {type(exc).__name__}: {exc}")
        return False

    finally:
        if current_process is not None:
            try:
                if current_process.poll() is None:
                    current_process.terminate()
                    current_process.wait(timeout=2)
            except Exception:
                try:
                    current_process.kill()
                except Exception:
                    pass

            current_process = None

        clear_now_playing()


def stop(*_):
    global running
    running = False

    if current_process is not None:
        try:
            current_process.terminate()
        except Exception:
            pass


def main():
    global running

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    log("Starting zevRadio...")
    log("Mixed audio formats supported through FFmpeg.")
    log(f"Output device locked to: {DEVICE_NAME}")

    ffmpeg = find_ffmpeg()
    device_index = find_audio_device()

    recent = []

    log("Radio automation is live.")

    while running:
        category = choose_category()
        path = choose_file(category, recent)

        if path is None:
            category = "music"
            path = choose_file("music", recent)

        if path is None:
            log("No playable audio files found in audio/music/.")
            log("Add music files and retrying in 5 seconds...")
            time.sleep(5)
            continue

        success = play_file(ffmpeg, device_index, path, category)

        if success:
            recent.append(path)
            recent = recent[-10:]
        else:
            # Do not immediately hammer a broken file/device.
            if running:
                log("Playback failed; retrying in 2 seconds...")
                time.sleep(2)

    clear_now_playing()
    log("zevRadio stopped.")


if __name__ == "__main__":
    main()
