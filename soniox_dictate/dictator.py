"""Async dictation controller that receives commands and audio via TCP.

Listens for commands via TCP socket from the Windows-side hotkey script
(dictate-hotkey.ps1). Audio is captured on Windows using waveIn API and
streamed to this server via TCP as framed PCM data.

Protocol (PS -> WSL):
  Text command:  printable ASCII + \\n (e.g. "toggle\\n")
  Audio frame:   0x01 + uint16_LE(length) + raw PCM bytes
  Audio end:     0x02 (signals no more audio frames)

Protocol (WSL -> PS):
  Text responses: "started\\n", "stopped\\n", "paste\\n", etc.

Global hotkeys (via PowerShell script):
  Ctrl+Shift+Space  - start/stop recording
  Ctrl+Shift+L      - toggle ET / ET->EN mode
  Ctrl+Shift+E      - toggle auto-Enter after paste
  Ctrl+Shift+C      - cancel current recording
  Ctrl+C            - quit (in this terminal)
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import sys

logger = logging.getLogger(__name__)

CMD_PORT = 19876

# Binary frame markers (PS -> WSL)
FRAME_AUDIO = 0x01
FRAME_AUDIO_END = 0x02


class State(enum.Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    FINALIZING = "FINALIZING"


class EnterMode(enum.Enum):
    OFF = "off"
    ENTER = "enter"
    CTRL_ENTER = "ctrl+enter"


class Dictator:
    """Async dictation controller that receives commands and audio via TCP."""

    def __init__(self, lang: str, enter_mode: str = "off") -> None:
        self.lang = lang
        self.enter_mode = EnterMode(enter_mode)
        self.state = State.IDLE

        self.soniox = None
        self._client_writer: asyncio.StreamWriter | None = None

    def _print(self, msg: str) -> None:
        sys.stdout.write(f"\r\033[K{msg}\n")
        sys.stdout.flush()

    def _print_status(self) -> None:
        mode = "ET" if self.lang == "et" else "ET->EN"
        enter = self.enter_mode.value
        self._print(
            f"\n--- Dictation | {mode} | Enter: {enter}"
            f" | Port: {CMD_PORT} ---\n\n"
            f"  Ctrl+Shift+Space = start/stop\n"
            f"  Ctrl+Shift+L     = toggle ET/EN\n"
            f"  Ctrl+Shift+E     = cycle enter mode\n"
            f"  Ctrl+Shift+C     = cancel\n"
            f"  Ctrl+C           = quit"
        )

    async def _send_to_client(self, msg: str) -> None:
        """Send a message back to the connected PowerShell client."""
        if self._client_writer and not self._client_writer.is_closing():
            try:
                self._client_writer.write(f"{msg}\n".encode())
                await self._client_writer.drain()
            except Exception:
                pass

    async def run(self) -> None:
        """Main entry point - starts TCP server and waits."""
        server = await asyncio.start_server(self._handle_client, "0.0.0.0", CMD_PORT)
        self._print_status()

        try:
            async with server:
                await server.serve_forever()
        finally:
            if self.soniox:
                await self.soniox.close()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a connected hotkey client.

        Reads a mixed stream of text commands and binary audio frames:
        - 0x01 + uint16_LE(len) + data = audio frame -> feed to Soniox
        - 0x02 = end of audio -> finalize transcription
        - printable ASCII + \\n = text command
        """
        addr = writer.get_extra_info("peername")
        self._print(f"Hotkey client connected from {addr}")
        self._client_writer = writer

        try:
            while True:
                first_byte = await reader.readexactly(1)
                b = first_byte[0]

                if b == FRAME_AUDIO:
                    # Audio frame: 2-byte LE length + PCM data
                    len_bytes = await reader.readexactly(2)
                    length = int.from_bytes(len_bytes, "little")
                    audio_data = await reader.readexactly(length)
                    await self._handle_audio(audio_data)

                elif b == FRAME_AUDIO_END:
                    # End of audio stream - finalize transcription
                    await self._handle_audio_end()

                else:
                    # Text command - read rest of line
                    rest = await reader.readline()
                    if not rest:
                        break
                    cmd = (first_byte + rest).decode().strip()
                    if cmd:
                        await self._handle_command(cmd)

        except asyncio.IncompleteReadError:
            pass  # Client disconnected
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Client connection error")
        finally:
            self._client_writer = None
            # Clean up if client disconnected mid-recording
            if self.soniox:
                await self.soniox.close()
                self.soniox = None
            self.state = State.IDLE
            writer.close()
            self._print("Hotkey client disconnected")

    async def _handle_audio(self, data: bytes) -> None:
        """Feed an audio frame to Soniox (if connected and recording/finalizing)."""
        if self.soniox and self.state in (State.RECORDING, State.FINALIZING):
            await self.soniox.send_audio(data)

    async def _handle_audio_end(self) -> None:
        """Handle end-of-audio marker - finalize if in FINALIZING state."""
        if self.state == State.FINALIZING:
            await self._finalize_transcription()

    async def _handle_command(self, cmd: str) -> None:
        """Process a text command string."""
        if cmd == "toggle":
            if self.state == State.IDLE:
                await self._start_recording()
            elif self.state == State.RECORDING:
                await self._stop_recording()
        elif cmd == "language":
            if self.state != State.RECORDING:
                self.lang = "en" if self.lang == "et" else "et"
                mode = "ET" if self.lang == "et" else "ET->EN"
                self._print(f"Language switched to: {mode}")
                await self._send_to_client(f"lang:{mode}")
            else:
                self._print("Cannot switch language while recording.")
        elif cmd == "enter":
            cycle = [EnterMode.OFF, EnterMode.ENTER, EnterMode.CTRL_ENTER]
            idx = cycle.index(self.enter_mode)
            self.enter_mode = cycle[(idx + 1) % len(cycle)]
            self._print(f"Auto-Enter: {self.enter_mode.value}")
            await self._send_to_client(f"enter:{self.enter_mode.value}")
        elif cmd == "cancel":
            if self.state == State.RECORDING:
                self._print("Recording cancelled.")
                await self._cleanup_recording()
                self.state = State.IDLE
                await self._send_to_client("cancelled")

    async def _start_recording(self) -> None:
        """Connect to Soniox and signal PS to start audio capture."""
        from soniox_dictate.soniox_client import SonioxClient

        self.state = State.RECORDING
        mode = "ET" if self.lang == "et" else "ET->EN"
        self._print(f"[REC] Recording... (Mode: {mode})")

        self.soniox = SonioxClient()
        translate_to = "en" if self.lang == "en" else None

        try:
            await self.soniox.connect(
                on_result_callback=self._on_transcription_result,
                language="et",
                translate_to=translate_to,
                audio_format="pcm_s16le",
                sample_rate=16000,
                num_channels=1,
            )
        except Exception:
            logger.exception("Failed to connect to Soniox")
            self._print("ERROR: Failed to connect to Soniox. Check API key.")
            self.state = State.IDLE
            await self._send_to_client("error")
            return

        # Tell PS to start audio capture - PS will begin streaming audio frames
        await self._send_to_client("started")

    async def _on_transcription_result(
        self,
        text: str,
        word_count: int,  # noqa: ARG002
        is_final: bool,  # noqa: ARG002
    ) -> None:
        """Callback from Soniox with transcription updates."""
        if self.state not in (State.RECORDING, State.FINALIZING):
            return
        display = text[-80:] if len(text) > 80 else text
        sys.stdout.write(f"\r\033[K  >> {display}")
        sys.stdout.flush()

    async def _stop_recording(self) -> None:
        """Signal PS to stop recording. Audio frames continue until 0x02 marker."""
        self.state = State.FINALIZING
        await self._send_to_client("stopped")
        # PS will stop capture, drain remaining frames, then send 0x02.
        # The main read loop feeds audio frames to Soniox as they arrive.
        # When 0x02 is received, _handle_audio_end calls _finalize_transcription.

    async def _finalize_transcription(self) -> None:
        """Finalize Soniox, copy text to clipboard, signal PS to paste."""
        from soniox_dictate.clipboard import copy_to_clipboard

        assert self.soniox is not None

        # Send finalize and wait for Soniox to process remaining audio
        try:
            if self.soniox.websocket and self.soniox.is_connected:
                await self.soniox.websocket.send(json.dumps({"type": "finalize"}))
                async with asyncio.timeout(3):
                    while self.soniox.is_connected:
                        await asyncio.sleep(0.05)
        except (TimeoutError, Exception):
            pass  # Use whatever text we have

        # Grab all text (final + any remaining pending)
        original_text = (self.soniox.final_text + self.soniox.pending_text).strip()
        translated_text = None
        if self.soniox._translate_to:
            translated_text = (
                self.soniox.translated_text + self.soniox.pending_translated_text
            ).strip()

        await self.soniox.close()
        self.soniox = None

        output_text = translated_text if translated_text else original_text

        if not output_text:
            self._print("No speech detected.")
            self.state = State.IDLE
            return

        word_count = len(output_text.split())

        try:
            copy_to_clipboard(output_text)
            self._print(f"Copied ({word_count} words): {output_text[:100]}")

            if translated_text and original_text:
                self._print(f"  Original: {original_text[:100]}")

            paste_signals = {
                EnterMode.OFF: "paste",
                EnterMode.ENTER: "paste_enter",
                EnterMode.CTRL_ENTER: "paste_ctrl_enter",
            }
            await self._send_to_client(paste_signals[self.enter_mode])
        except Exception as e:
            self._print(f"[ERROR] Clipboard: {e}")
            self._print(f"Text: {output_text}")

        self.state = State.IDLE
        self._print("Ready.")

    async def _cleanup_recording(self) -> None:
        """Clean up Soniox without finalizing (for cancel)."""
        if self.soniox:
            await self.soniox.close()
            self.soniox = None
