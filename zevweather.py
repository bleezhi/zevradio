#!/usr/bin/env python3
"""
zevWeather - live weather bulletin + direct VB-CABLE playback.

The generated bulletin is played directly to:
    CABLE Input (VB-Audio Virtual Cable)

It never falls back to the Windows default speakers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
import wave

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    print("[zevWeather] Missing dependencies.")
    print("[zevWeather] Run:")
    print("    python -m pip install sounddevice numpy")
    raise

LATITUDE = 52.4064
LONGITUDE = 16.9252
OUTPUT_FILE = Path(__file__).with_name("zevweather.wav")
USER_AGENT = "zevWeather/1.2"
CABLE_DEVICE = "CABLE Input"


def find_cable_device() -> tuple[int, str]:
    """Find the actual playback endpoint for VB-CABLE, preferably WASAPI."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    candidates = []

    for index, device in enumerate(devices):
        name = device["name"]

        # IMPORTANT: We want CABLE Input, not CABLE In 16ch or CABLE Output.
        if name.strip().lower() != CABLE_DEVICE.lower():
            continue
        if device["max_output_channels"] < 1:
            continue

        hostapi_name = hostapis[device["hostapi"]]["name"]
        candidates.append((index, name, hostapi_name))

    if not candidates:
        print("[zevWeather] ERROR: CABLE Input playback device not found.")
        print("[zevWeather] Output devices detected:")
        for index, device in enumerate(devices):
            if device["max_output_channels"] > 0:
                hostapi_name = hostapis[device["hostapi"]]["name"]
                print(f"    [{index}] {device['name']}  ({hostapi_name})")
        raise RuntimeError(
            "CABLE Input was not found. Check that VB-CABLE is installed and enabled."
        )

    # WASAPI is the most reliable Windows backend for routing to VB-CABLE.
    for index, name, hostapi_name in candidates:
        if "WASAPI" in hostapi_name.upper():
            return index, name

    return candidates[0][0], candidates[0][1]


def play_to_cable(wav_file: Path) -> None:
    """Play WAV exclusively to VB-CABLE CABLE Input."""
    device_index, device_name = find_cable_device()
    device = sd.query_devices(device_index)
    hostapi = sd.query_hostapis(device["hostapi"])["name"]

    print(f"[zevWeather] Direct output device: {device_name}")
    print(f"[zevWeather] Audio backend: {hostapi}")
    print("[zevWeather] Sending bulletin to VB-CABLE...")

    with wave.open(str(wav_file), "rb") as wav:
        channels = wav.getnchannels()
        samplerate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2:
        raise RuntimeError(
            f"Generated WAV is {sample_width * 8}-bit; expected 16-bit PCM."
        )

    audio = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels)

    # Force the chosen device. WASAPI shared mode lets Windows convert the
    # SAPI WAV sample rate/channels to whatever VB-CABLE currently accepts.
    extra = None
    if "WASAPI" in hostapi.upper():
        extra = sd.WasapiSettings(exclusive=False, auto_convert=True)

    sd.play(
        audio,
        samplerate=samplerate,
        device=device_index,
        blocking=True,
        extra_settings=extra,
    )
    sd.stop()
    print("[zevWeather] Bulletin sent to CABLE Input.")


def fetch_weather() -> dict:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "Europe/Warsaw",
        "forecast_days": 2,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def condition(code: int) -> str:
    return {
        0: "bezchmurnie",
        1: "przeważnie bezchmurnie",
        2: "częściowe zachmurzenie",
        3: "pochmurno",
        45: "mgła",
        48: "mgła osadzająca szadź",
        51: "słaba mżawka",
        53: "umiarkowana mżawka",
        55: "silna mżawka",
        61: "słaby deszcz",
        63: "umiarkowany deszcz",
        65: "silny deszcz",
        71: "słabe opady śniegu",
        73: "umiarkowane opady śniegu",
        75: "silne opady śniegu",
        80: "słabe przelotne opady deszczu",
        81: "umiarkowane przelotne opady deszczu",
        82: "silne przelotne opady deszczu",
        95: "burze",
        96: "burze z gradem",
        99: "silne burze z gradem",
    }.get(code, "zmienne warunki")


def number(value: float | int) -> str:
    return str(round(float(value)))


def make_script(data: dict) -> str:
    current = data["current"]
    daily = data["daily"]

    return (
        f"Tu zevWeather. Jest godzina {datetime.now().strftime('%H:%M')}. "
        "Najnowsza prognoza dla Poznania. "
        f"Obecnie mamy {number(current['temperature_2m'])} stopni Celsjusza, "
        f"odczuwalna temperatura to {number(current['apparent_temperature'])} stopni. "
        f"Warunki: {condition(current['weather_code'])}. "
        f"Wilgotność wynosi około {number(current['relative_humidity_2m'])} procent, "
        f"a wiatr wieje z prędkością około {number(current['wind_speed_10m'])} kilometrów na godzinę. "
        f"Dzisiaj temperatura maksymalna wyniesie około {number(daily['temperature_2m_max'][0])} stopni, "
        f"a minimalna około {number(daily['temperature_2m_min'][0])}. "
        f"Prawdopodobieństwo opadów wynosi do {daily['precipitation_probability_max'][0]} procent. "
        f"Jutro: {condition(daily['weather_code'][1])}, temperatura od "
        f"{number(daily['temperature_2m_min'][1])} do {number(daily['temperature_2m_max'][1])} stopni, "
        f"z prawdopodobieństwem opadów do {daily['precipitation_probability_max'][1]} procent. "
        "To był zevWeather. Miłego dnia i zostańcie z nami na zevRadio."
    )


def generate_tts(text: str, output: Path) -> None:
    ps_script = r'''
param([string]$Text, [string]$Output)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$polish = $synth.GetInstalledVoices() | Where-Object {
    $_.VoiceInfo.Culture.Name -like "pl-*"
} | Select-Object -First 1
if ($null -ne $polish) { $synth.SelectVoice($polish.VoiceInfo.Name) }
$synth.Rate = -1
$synth.Volume = 100
$synth.SetOutputToWaveFile($Output)
$synth.Speak($Text)
$synth.Dispose()
'''

    with tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as f:
        ps_file = Path(f.name)
        f.write(ps_script)

    try:
        subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(ps_file), "-Text", text, "-Output", str(output.resolve()),
            ],
            check=True,
        )
    finally:
        ps_file.unlink(missing_ok=True)


def main() -> int:
    try:
        print("[zevWeather] Fetching live weather...")
        data = fetch_weather()
        script = make_script(data)

        print(f"[zevWeather] Bulletin: {script}")
        print("[zevWeather] Generating TTS...")
        generate_tts(script, OUTPUT_FILE)
        print(f"[zevWeather] Created: {OUTPUT_FILE}")

        # Automatically play immediately after TTS generation.
        play_to_cable(OUTPUT_FILE)
        return 0

    except Exception as exc:
        print(f"[zevWeather] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
