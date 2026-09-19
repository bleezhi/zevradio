import json
import random
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
AUDIO = BASE / "audio"
DATA = BASE / "data"
FFMPEG_DIR = BASE / "ffmpeg"

# EXACTLY ONE output device. Never use CABLE In 16ch or Windows default.
DEVICE_NAME = "CABLE Input"

SAMPLE_RATE = 48000
CHANNELS = 2

SUPPORTED_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac",
    ".opus", ".wma", ".aiff", ".aif"
}

# Simple weighted rotation for now.
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

    for index, device in enumerate(sd.query_devices()):
        if (
            device["max_output_channels"] > 0
            and device["name"].strip().lower() == DEVICE_NAME.lower()
        ):
            log(
                f"Audio output: {device['name']} "
                f"(device {index}, {device['max_output_channels']}ch, "
                f"default {device['default_samplerate']} Hz)"
            )
            return index

    log(f"ERROR: exact output device '{DEVICE_NAME}' was not found.")
    log("Available output devices:")
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] > 0:
            log(f"  [{index}] {device['name']}")
    sys.exit(1)


def write_now_playing(path, category, duration=None):
    DATA.mkdir(parents=True, exist_ok=True)

    payload = {
        "playing": True,
        "title": path.stem,
        "file": path.name,
        "category": category,
        "duration_seconds": duration,
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

    history_file.write_text(
        json.dumps(history[-1000:], indent=2),
        encoding="utf-8",
    )


def files_in(category):
    folder = AUDIO / category
    if not folder.exists():
        return []

    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def choose_file(category, recent):
    files = files_in(category)
    if not files:
        return None

    available = [p for p in files if p not in recent]
    if not available:
        available = files

    return random.choice(available)


def choose_category():
    available = [
        (category, weight)
        for category, weight in ROTATION
        if weight > 0 and files_in(category)
    ]

    if not available:
        return "music"

    categories = [x[0] for x in available]
    weights = [x[1] for x in available]
    return random.choices(categories, weights=weights, k=1)[0]


def probe_duration(ffmpeg, path):
    command = [
        ffmpeg,
        "-hide_banner",
        "-i", str(path),
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        import re
        match = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
            result.stderr,
        )

        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            seconds = float(match.group(3))
            return hours * 3600 + minutes * 60 + seconds
    except Exception:
        pass

    return None


def play_file(ffmpeg, device_index, path, category):
    global current_process

    duration = probe_duration(ffmpeg, path)

    if duration is not None:
        log(
            f"▶ {category.upper()}: {path.name} "
            f"({int(duration // 60)}:{int(duration % 60):02d})"
        )
    else:
        log(f"▶ {category.upper()}: {path.name}")

    write_now_playing(path, category, duration)

    # Decode to a temporary WAV first. This intentionally avoids piping
    # FFmpeg's raw stdout directly into the audio callback. The previous
    # pipe approach was causing the scheduler to advance almost instantly.
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix="zevradio_",
            suffix=".wav",
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)

        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-i", str(path),
            "-vn",
            "-ac", str(CHANNELS),
            "-ar", str(SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            "-y",
            str(temp_path),
        ]

        current_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        _, stderr = current_process.communicate()

        if current_process.returncode != 0:
            error = stderr.decode(errors="replace").strip()
            log(f"FFmpeg decode failed ({current_process.returncode}): {error}")
            return False

        current_process = None

        # Use soundfile's blocking reader + sounddevice output stream.
        # Each write represents real audio frames, and the loop cannot
        # advance to the next track until every frame has been played.
        import sounddevice as sd
        import soundfile as sf

        with sf.SoundFile(str(temp_path), mode="r") as audio_file:
            actual_samplerate = audio_file.samplerate
            actual_channels = audio_file.channels

            log(
                f"   playing at {actual_samplerate} Hz / "
                f"{actual_channels}ch on CABLE Input"
            )

            with sd.OutputStream(
                samplerate=actual_samplerate,
                channels=actual_channels,
                dtype="float32",
                device=device_index,
                blocksize=2048,
            ) as output:
                while running:
                    data = audio_file.read(
                        2048,
                        dtype="float32",
                        always_2d=True,
                    )

                    if len(data) == 0:
                        break

                    output.write(data)

        if running:
            append_history(path, category)
            log(f"✓ FINISHED: {path.name}")
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

        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

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
    log(f"Output device LOCKED to: {DEVICE_NAME}")

    ffmpeg = find_ffmpeg()
    log(f"FFmpeg: {ffmpeg}")

    device_index = find_audio_device()
    recent = []

    log("Radio automation is live.")
    log("A track is not considered finished until every audio frame has played.")

    while running:
        category = choose_category()
        path = choose_file(category, recent)

        if path is None:
            category = "music"
            path = choose_file("music", recent)

        if path is None:
            log("No playable audio files found in audio/music/.")
            log("Retrying in 5 seconds...")
            time.sleep(5)
            continue

        success = play_file(ffmpeg, device_index, path, category)

        if success:
            recent.append(path)
            recent = recent[-10:]
        elif running:
            log("Playback failed; retrying in 2 seconds...")
            time.sleep(2)

    clear_now_playing()
    log("zevRadio stopped.")


if __name__ == "__main__":
    main()
