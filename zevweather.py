#!/usr/bin/env python3
"""
zevWeather - real-time weather bulletin generator for zevRadio.

Fetches weather for Poznan, Poland from Open-Meteo and generates a WAV
announcement using the Windows built-in Microsoft Speech API (SAPI).

Requirements:
    Python 3.10+
    Windows (for built-in SAPI TTS)

No API key required.
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

LATITUDE = 52.4064
LONGITUDE = 16.9252
LOCATION_NAME = "Poznan"
OUTPUT_FILE = Path(__file__).with_name("zevweather.wav")
USER_AGENT = "zevWeather/1.0"


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
    conditions = {
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
    }
    return conditions.get(code, "zmienne warunki")


def polish_number(value: float | int) -> str:
    value = round(float(value))
    if value == 1:
        return "1"
    return str(value)


def make_script(data: dict) -> str:
    current = data["current"]
    daily = data["daily"]

    temp = polish_number(current["temperature_2m"])
    feels = polish_number(current["apparent_temperature"])
    humidity = polish_number(current["relative_humidity_2m"])
    wind = polish_number(current["wind_speed_10m"])
    now_condition = condition(current["weather_code"])

    today_high = polish_number(daily["temperature_2m_max"][0])
    today_low = polish_number(daily["temperature_2m_min"][0])
    today_rain = daily["precipitation_probability_max"][0]

    tomorrow_high = polish_number(daily["temperature_2m_max"][1])
    tomorrow_low = polish_number(daily["temperature_2m_min"][1])
    tomorrow_condition = condition(daily["weather_code"][1])
    tomorrow_rain = daily["precipitation_probability_max"][1]

    generated = datetime.now().strftime("%H:%M")

    return (
        f"Tu zevWeather. Jest godzina {generated}. "
        f"Najnowsza prognoza dla Poznania. "
        f"Obecnie mamy {temp} stopni Celsjusza, odczuwalna temperatura to {feels} stopni. "
        f"Warunki: {now_condition}. "
        f"Wilgotność wynosi około {humidity} procent, a wiatr wieje z prędkością około {wind} kilometrów na godzinę. "
        f"Dzisiaj temperatura maksymalna wyniesie około {today_high} stopni, "
        f"a minimalna około {today_low}. "
        f"Prawdopodobieństwo opadów wynosi do {today_rain} procent. "
        f"Jutro: {tomorrow_condition}, temperatura od {tomorrow_low} do {tomorrow_high} stopni, "
        f"z prawdopodobieństwem opadów do {tomorrow_rain} procent. "
        f"To był zevWeather. Miłego dnia i zostańcie z nami na zevRadio."
    )


def generate_tts(text: str, output: Path) -> None:
    # SAPI voice selection: use the first installed Polish voice if available,
    # otherwise fall back to the Windows default voice.
    ps_script = r'''
param([string]$Text, [string]$Output)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$polish = $synth.GetInstalledVoices() | Where-Object {
    $_.VoiceInfo.Culture.Name -like "pl-*"
} | Select-Object -First 1
if ($null -ne $polish) {
    $synth.SelectVoice($polish.VoiceInfo.Name)
}
$synth.Rate = -1
$synth.Volume = 100
$synth.SetOutputToWaveFile($Output)
$synth.Speak($Text)
$synth.Dispose()
'''
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        ps_file = Path(f.name)
        f.write(ps_script)

    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps_file),
                "-Text",
                text,
                "-Output",
                str(output.resolve()),
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
        print("[zevWeather] Generating TTS...")
        generate_tts(script, OUTPUT_FILE)
        print(f"[zevWeather] Created: {OUTPUT_FILE}")
        print("[zevWeather] Ready for broadcast.")
        return 0
    except Exception as exc:
        print(f"[zevWeather] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
