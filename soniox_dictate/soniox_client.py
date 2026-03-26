"""Soniox WebSocket client for real-time speech-to-text."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Coroutine
from typing import Any

import websockets

logger = logging.getLogger(__name__)

# Type alias for the callback function
TranscriptionCallback = Callable[[str, int, bool], Coroutine[Any, Any, None]]


class SonioxClient:
    """
    Async WebSocket client for Soniox real-time speech-to-text API.

    Usage:
        client = SonioxClient()
        await client.connect(on_result_callback=my_callback)
        await client.send_audio(audio_chunk)
        final_text = await client.finalize()
        await client.close()
    """

    ENDPOINT = "wss://stt-rt.soniox.com/transcribe-websocket"
    MODEL = "stt-rt-v4"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SONIOX_API_KEY")
        self.websocket: Any = None
        self.on_result_callback: TranscriptionCallback | None = None
        self.is_connected = False
        self.final_text = ""  # Only finalized original tokens
        self.pending_text = ""  # Non-final tokens (may change)
        self.translated_text = ""  # Finalized translation tokens
        self.pending_translated_text = ""  # Non-final translation tokens
        self._translate_to: str | None = None
        self._receive_task: asyncio.Task | None = None

    async def connect(
        self,
        on_result_callback: TranscriptionCallback,
        language: str = "en",
        translate_to: str | None = None,
        audio_format: str = "auto",
        sample_rate: int | None = None,
        num_channels: int | None = None,
    ) -> None:
        """
        Connect to Soniox WebSocket API.

        Args:
            on_result_callback: Async callback function(text, word_count, is_final)
            language: ISO language code for transcription hints (e.g., 'en', 'et').
                      Biases the model toward the specified language but doesn't
                      restrict recognition to only that language.
            translate_to: Target language for translation (e.g., 'en'). None disables.
            audio_format: Audio format (e.g., 'auto', 'pcm_s16le').
            sample_rate: Sample rate in Hz (e.g., 16000). None omits from config.
            num_channels: Number of audio channels (e.g., 1). None omits from config.
        """
        if not self.api_key:
            raise ValueError(
                "SONIOX_API_KEY not configured. "
                "Set it in .env or pass api_key to constructor."
            )

        self.on_result_callback = on_result_callback
        self._translate_to = translate_to

        try:
            self.websocket = await websockets.connect(
                self.ENDPOINT,
                ping_interval=20,
                ping_timeout=20,
            )
            self.is_connected = True

            # Send initial configuration
            config: dict[str, Any] = {
                "api_key": self.api_key,
                "model": self.MODEL,
                "audio_format": audio_format,
                "language_hints": [language],
            }
            if sample_rate is not None:
                config["sample_rate"] = sample_rate
            if num_channels is not None:
                config["num_channels"] = num_channels
            if translate_to:
                config["translation"] = {
                    "type": "one_way",
                    "target_language": translate_to,
                }
            await self.websocket.send(json.dumps(config))

            # Start receiving messages in background
            self._receive_task = asyncio.create_task(self._receive_loop())

            logger.debug("Connected to Soniox (lang=%s)", language)

        except Exception as e:
            logger.error(f"Failed to connect to Soniox: {e}")
            raise

    async def send_audio(self, audio_chunk: bytes) -> None:
        """Send audio chunk to Soniox for transcription."""
        if not self.websocket or not self.is_connected:
            return

        try:
            await self.websocket.send(audio_chunk)
        except Exception as e:
            logger.error(f"Error sending audio to Soniox: {e}")

    async def finalize(self) -> str:
        """
        Finalize the transcription session and get final text.

        Returns:
            Final transcribed text (original language).
        """
        if not self.websocket or not self.is_connected:
            return self.final_text + self.pending_text

        try:
            # Send finalize command
            await self.websocket.send(json.dumps({"type": "finalize"}))

            # Wait for final response (with timeout)
            try:
                async with asyncio.timeout(10):
                    while self.is_connected:
                        await asyncio.sleep(0.1)
                        # The receive loop will handle the final message
            except TimeoutError:
                logger.warning("Timeout waiting for final transcription")

        except Exception as e:
            logger.error(f"Error finalizing Soniox session: {e}")

        # After finalize, all pending should be final
        return self.final_text

    async def finalize_with_translation(self) -> tuple[str, str | None]:
        """
        Finalize and return both original and translated text.

        Returns:
            Tuple of (original_text, translated_text or None).
        """
        original = await self.finalize()
        translated = self.translated_text if self._translate_to else None
        return original, translated

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self.is_connected = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket: {e}")
            self.websocket = None

        logger.debug("Disconnected from Soniox")

    async def _receive_loop(self) -> None:
        """Background task to receive messages from Soniox."""
        if not self.websocket:
            return

        try:
            async for message in self.websocket:
                if isinstance(message, str):
                    await self._handle_message(json.loads(message))
                # Binary messages are not expected from Soniox

        except websockets.exceptions.ConnectionClosed:
            logger.debug("Soniox connection closed")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")
        finally:
            self.is_connected = False

    async def _handle_message(self, data: dict[str, Any]) -> None:
        """Handle incoming message from Soniox."""
        # Check for errors
        if "error_code" in data:
            error_msg = data.get("error_message", "Unknown error")
            logger.error(f"Soniox error: {error_msg}")
            return

        # Check if finished
        if data.get("finished"):
            self.is_connected = False
            return

        # Process tokens
        tokens = data.get("tokens", [])
        if tokens:
            # Separate final, pending, and translation tokens
            final_parts: list[str] = []
            pending_parts: list[str] = []
            translated_final_parts: list[str] = []
            translated_pending_parts: list[str] = []

            for token in tokens:
                text = token.get("text", "")
                if not text or text == "<fin>":
                    continue

                translation_status = token.get("translation_status", "none")

                if translation_status == "translation":
                    # Translation token
                    if token.get("is_final", False):
                        translated_final_parts.append(text)
                    else:
                        translated_pending_parts.append(text)
                else:
                    # Original token ("original" or "none")
                    if token.get("is_final", False):
                        final_parts.append(text)
                    else:
                        pending_parts.append(text)

            # Add final tokens to permanent text
            if final_parts:
                self.final_text += "".join(final_parts)
            if translated_final_parts:
                self.translated_text += "".join(translated_final_parts)

            # Replace pending text with current non-final tokens
            self.pending_text = "".join(pending_parts)
            self.pending_translated_text = "".join(translated_pending_parts)

            # Build display text (final + pending) — always use original for callback
            display_text = self.final_text + self.pending_text

            # Count words in total text
            word_count = len(display_text.split())

            # Call callback
            if self.on_result_callback:
                await self.on_result_callback(
                    display_text,
                    word_count,
                    bool(final_parts),  # is_final if we got any final tokens
                )
