# zevRadio

A local web-controlled radio automation prototype for the Zev network.

## Features

- Web control panel
- Add/remove music, ads, promos, idents and sweepers directories
- Recursive audio-library scanning
- ON AIR / STOP controls
- Now-playing state
- JSON API
- Config file
- Prepared audio folders for news and weather
- Designed to grow into a real continuous FFmpeg radio stream

## Quick start

### Windows

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py zevRadio.py
```

Open http://127.0.0.1:8080

Put audio files into the folders under `audio/`, or add additional directories from the web panel.

## Current status

The control panel and library management are implemented. The `/radio` endpoint is currently a placeholder; the next backend step is a continuous FFmpeg/PyAV encoder with scheduling, gapless playback and metadata.

## Suggested station content

Use internal Zev promos instead of real commercial advertising:

- "You're listening to zevRadio."
- ZevTV cross-promos
- ZevOS / zevMobile promos
- station idents
- sweepers
- weather/news information spots
- special-event promos
