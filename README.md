# soniox-dictate

Real-time speech-to-text dictation tool for **WSL2 + Windows** development environments. Speak in Estonian (or any Soniox-supported language), and the transcribed text is automatically pasted at your cursor position — in any application.

Built for developers who use WSL2 and want voice input in terminals, editors, chat apps, etc.

## How It Works

Two-process architecture connected via TCP:

```
┌─────────────────────────┐     TCP :19876      ┌──────────────────────────┐
│   Windows (PowerShell)  │ ──────────────────── │     WSL2 (Python)        │
│                         │                      │                          │
│  Global hotkeys         │   commands/audio ──> │  Soniox WebSocket API    │
│  Audio capture (waveIn) │                      │  Real-time transcription │
│  Auto-paste (SendKeys)  │ <── responses ────── │  Clipboard copy          │
└─────────────────────────┘                      └──────────────────────────┘
```

1. Press **Ctrl+Shift+Space** to start recording
2. Speak into your microphone
3. Press **Ctrl+Shift+Space** again to stop
4. Transcribed text is copied to clipboard and auto-pasted at your cursor

## Prerequisites

- **WSL2** on Windows 10/11
- **Python 3.12+** in WSL (with [uv](https://docs.astral.sh/uv/) recommended)
- **Soniox API key** — get one at [soniox.com](https://soniox.com)
- **Windows microphone** accessible (default input device)

## Installation

```bash
# Clone the repo
git clone https://github.com/VrajaKd/soniox-dictate.git
cd soniox-dictate

# Install dependencies (using uv)
uv sync

# Or with pip
pip install -e .
```

## Configuration

```bash
cp .env.template .env
# Edit .env and add your Soniox API key
```

## Usage

### 1. Start the dictation server (in WSL)

```bash
# Using the CLI
soniox-dictate --lang et

# Or as a Python module
python -m soniox_dictate --lang et

# Or with the wrapper script
./scripts/start.sh et
```

Options:
- `--lang et` — Estonian transcription (default)
- `--lang en` — Estonian speech transcribed and translated to English
- `--enter-mode off|enter|ctrl+enter` — what key to send after paste
- `-v` — verbose/debug logging

### 2. Start the hotkey listener (in Windows Terminal)

Copy the PowerShell script and run it in a **real Windows Terminal tab** (not from WSL):

```powershell
# In PowerShell / Windows Terminal
powershell -ExecutionPolicy Bypass -File .\scripts\dictate-hotkey.ps1
```

> **Important:** The hotkey listener must run in a real Windows Terminal tab. Running it as a background process will cause hotkey registration to fail silently.

## Hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+Space` | Start/stop recording |
| `Ctrl+Shift+L` | Toggle language: ET / ET->EN |
| `Ctrl+Shift+E` | Cycle auto-Enter mode: off -> enter -> ctrl+enter |
| `Ctrl+Shift+C` | Cancel current recording |
| `Ctrl+C` | Quit (in the terminal running the script) |

## Auto-Enter Modes

After the transcribed text is pasted, an optional keypress can be sent:

| Mode | Behavior | Use case |
|------|----------|----------|
| `off` | Paste only | Default — review before sending |
| `enter` | Paste + Enter | Chat apps (Slack, Teams) |
| `ctrl+enter` | Paste + Ctrl+Enter | Claude Code terminal input |

## Audio Feedback

| Sound | Meaning |
|-------|---------|
| High beep (1000 Hz) | Recording started |
| Low beep (400 Hz) | Recording stopped, finalizing |
| Double beep (800 Hz) | Text pasted |
| Triple low beep (300 Hz) | Recording cancelled |

## TCP Protocol

Communication between PowerShell (Windows) and the Python server (WSL):

**PowerShell -> WSL:**
- Text commands: ASCII + `\n` (e.g., `toggle\n`, `language\n`, `cancel\n`, `enter\n`)
- Audio frames: `0x01` + `uint16_LE(length)` + raw PCM bytes
- End of audio: `0x02`

**WSL -> PowerShell:**
- `started` — start audio capture
- `stopped` — stop audio capture, drain remaining frames
- `paste` / `paste_enter` / `paste_ctrl_enter` — paste from clipboard
- `cancelled` — recording was cancelled
- `error` — server-side error
- `lang:<mode>` — language mode changed
- `enter:<mode>` — enter mode changed

Audio format: 16 kHz, 16-bit, mono PCM (signed little-endian).

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
pytest

# Run with debug logging
soniox-dictate --lang et -v
```

## Troubleshooting

**"Cannot connect to WSL dictation server"**
- Make sure the Python server is running in WSL first

**Hotkeys not working**
- Run the PowerShell script in a real Windows Terminal tab (not as a background task)
- Check that no other app has registered the same hotkeys

**No audio captured**
- Verify your microphone is set as the default Windows input device
- Test with Windows Sound Recorder first

**Empty transcription**
- Check that `SONIOX_API_KEY` is set in `.env`
- Run with `-v` flag to see debug output

**Clipboard/paste not working**
- WSL needs access to `clip.exe` (usually at `/mnt/c/Windows/system32/clip.exe`)
- On native Linux, install `xclip`: `sudo apt install xclip`

## License

MIT
