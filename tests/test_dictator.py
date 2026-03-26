"""Tests for the Dictator state machine."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soniox_dictate.dictator import Dictator, EnterMode, State


@pytest.fixture
def dictator():
    """Create a Dictator instance with mocked client writer."""
    d = Dictator(lang="et")
    d._client_writer = MagicMock()
    d._client_writer.is_closing.return_value = False
    d._client_writer.write = MagicMock()
    d._client_writer.drain = AsyncMock()
    return d


class TestDictatorInit:
    """Tests for Dictator initialization."""

    def test_default_state_is_idle(self):
        d = Dictator(lang="et")
        assert d.state == State.IDLE

    def test_default_lang(self):
        d = Dictator(lang="et")
        assert d.lang == "et"

    def test_english_lang(self):
        d = Dictator(lang="en")
        assert d.lang == "en"

    def test_enter_mode_default_off(self):
        d = Dictator(lang="et")
        assert d.enter_mode == EnterMode.OFF

    def test_enter_mode_enter(self):
        d = Dictator(lang="et", enter_mode="enter")
        assert d.enter_mode == EnterMode.ENTER

    def test_enter_mode_ctrl_enter(self):
        d = Dictator(lang="et", enter_mode="ctrl+enter")
        assert d.enter_mode == EnterMode.CTRL_ENTER


@pytest.mark.asyncio
class TestDictatorHandleCommand:
    """Tests for command handling and state transitions."""

    async def test_toggle_from_idle_starts_recording(self, dictator):
        with patch.object(
            dictator, "_start_recording", new_callable=AsyncMock
        ) as mock_start:
            await dictator._handle_command("toggle")
            mock_start.assert_called_once()

    async def test_toggle_from_recording_stops(self, dictator):
        dictator.state = State.RECORDING
        with patch.object(
            dictator, "_stop_recording", new_callable=AsyncMock
        ) as mock_stop:
            await dictator._handle_command("toggle")
            mock_stop.assert_called_once()

    async def test_toggle_from_finalizing_ignored(self, dictator):
        dictator.state = State.FINALIZING
        with (
            patch.object(
                dictator, "_start_recording", new_callable=AsyncMock
            ) as mock_start,
            patch.object(
                dictator, "_stop_recording", new_callable=AsyncMock
            ) as mock_stop,
        ):
            await dictator._handle_command("toggle")
            mock_start.assert_not_called()
            mock_stop.assert_not_called()

    async def test_language_toggle_et_to_en(self, dictator):
        dictator.lang = "et"
        with patch.object(dictator, "_send_to_client", new_callable=AsyncMock):
            await dictator._handle_command("language")
        assert dictator.lang == "en"

    async def test_language_toggle_en_to_et(self, dictator):
        dictator.lang = "en"
        with patch.object(dictator, "_send_to_client", new_callable=AsyncMock):
            await dictator._handle_command("language")
        assert dictator.lang == "et"

    async def test_language_blocked_during_recording(self, dictator):
        dictator.state = State.RECORDING
        dictator.lang = "et"
        await dictator._handle_command("language")
        assert dictator.lang == "et"

    async def test_cancel_during_recording(self, dictator):
        dictator.state = State.RECORDING
        with (
            patch.object(
                dictator, "_cleanup_recording", new_callable=AsyncMock
            ) as mock_cleanup,
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._handle_command("cancel")
            mock_cleanup.assert_called_once()
            assert dictator.state == State.IDLE

    async def test_cancel_from_idle_ignored(self, dictator):
        dictator.state = State.IDLE
        with patch.object(
            dictator, "_cleanup_recording", new_callable=AsyncMock
        ) as mock_cleanup:
            await dictator._handle_command("cancel")
            mock_cleanup.assert_not_called()

    async def test_enter_cycles_off_to_enter(self, dictator):
        assert dictator.enter_mode == EnterMode.OFF
        with patch.object(
            dictator, "_send_to_client", new_callable=AsyncMock
        ) as mock_send:
            await dictator._handle_command("enter")
        assert dictator.enter_mode == EnterMode.ENTER
        mock_send.assert_called_with("enter:enter")

    async def test_enter_cycles_enter_to_ctrl_enter(self, dictator):
        dictator.enter_mode = EnterMode.ENTER
        with patch.object(
            dictator, "_send_to_client", new_callable=AsyncMock
        ) as mock_send:
            await dictator._handle_command("enter")
        assert dictator.enter_mode == EnterMode.CTRL_ENTER
        mock_send.assert_called_with("enter:ctrl+enter")

    async def test_enter_cycles_ctrl_enter_to_off(self, dictator):
        dictator.enter_mode = EnterMode.CTRL_ENTER
        with patch.object(
            dictator, "_send_to_client", new_callable=AsyncMock
        ) as mock_send:
            await dictator._handle_command("enter")
        assert dictator.enter_mode == EnterMode.OFF
        mock_send.assert_called_with("enter:off")

    async def test_unknown_command_ignored(self, dictator):
        await dictator._handle_command("unknown_cmd")
        assert dictator.state == State.IDLE


@pytest.mark.asyncio
class TestDictatorStartRecording:
    """Tests for _start_recording."""

    async def test_sets_recording_state(self, dictator):
        mock_soniox = AsyncMock()
        mock_soniox.connect = AsyncMock()
        mock_soniox.is_connected = True

        with (
            patch(
                "soniox_dictate.soniox_client.SonioxClient",
                return_value=mock_soniox,
            ),
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._start_recording()

        assert dictator.state == State.RECORDING

    async def test_connects_soniox_with_et_lang(self, dictator):
        mock_soniox = AsyncMock()
        mock_soniox.connect = AsyncMock()
        mock_soniox.is_connected = True

        with (
            patch(
                "soniox_dictate.soniox_client.SonioxClient",
                return_value=mock_soniox,
            ),
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._start_recording()

        mock_soniox.connect.assert_called_once()
        call_kwargs = mock_soniox.connect.call_args[1]
        assert call_kwargs["language"] == "et"
        assert call_kwargs["translate_to"] is None
        assert call_kwargs["audio_format"] == "pcm_s16le"
        assert call_kwargs["sample_rate"] == 16000

    async def test_connects_soniox_with_en_translation(self, dictator):
        dictator.lang = "en"
        mock_soniox = AsyncMock()
        mock_soniox.connect = AsyncMock()
        mock_soniox.is_connected = True

        with (
            patch(
                "soniox_dictate.soniox_client.SonioxClient",
                return_value=mock_soniox,
            ),
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._start_recording()

        call_kwargs = mock_soniox.connect.call_args[1]
        assert call_kwargs["translate_to"] == "en"

    async def test_sends_started_to_client(self, dictator):
        mock_soniox = AsyncMock()
        mock_soniox.connect = AsyncMock()
        mock_soniox.is_connected = True

        with (
            patch(
                "soniox_dictate.soniox_client.SonioxClient",
                return_value=mock_soniox,
            ),
            patch.object(
                dictator, "_send_to_client", new_callable=AsyncMock
            ) as mock_send,
        ):
            await dictator._start_recording()

        mock_send.assert_called_with("started")

    async def test_soniox_connect_failure_resets_state(self, dictator):
        mock_soniox = AsyncMock()
        mock_soniox.connect = AsyncMock(side_effect=Exception("Connection failed"))

        with (
            patch(
                "soniox_dictate.soniox_client.SonioxClient",
                return_value=mock_soniox,
            ),
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._start_recording()

        assert dictator.state == State.IDLE


@pytest.mark.asyncio
class TestDictatorStopRecording:
    """Tests for _stop_recording (just signals PS, doesn't finalize)."""

    async def test_sets_finalizing_state(self, dictator):
        dictator.state = State.RECORDING
        with patch.object(dictator, "_send_to_client", new_callable=AsyncMock):
            await dictator._stop_recording()

        assert dictator.state == State.FINALIZING

    async def test_sends_stopped_to_client(self, dictator):
        dictator.state = State.RECORDING
        with patch.object(
            dictator, "_send_to_client", new_callable=AsyncMock
        ) as mock_send:
            await dictator._stop_recording()

        mock_send.assert_called_once_with("stopped")


@pytest.mark.asyncio
class TestDictatorHandleAudio:
    """Tests for _handle_audio."""

    async def test_feeds_audio_to_soniox_when_recording(self, dictator):
        dictator.state = State.RECORDING
        mock_soniox = AsyncMock()
        dictator.soniox = mock_soniox

        await dictator._handle_audio(b"\x00" * 4096)

        mock_soniox.send_audio.assert_called_once_with(b"\x00" * 4096)

    async def test_feeds_audio_to_soniox_when_finalizing(self, dictator):
        dictator.state = State.FINALIZING
        mock_soniox = AsyncMock()
        dictator.soniox = mock_soniox

        await dictator._handle_audio(b"\x00" * 4096)

        mock_soniox.send_audio.assert_called_once_with(b"\x00" * 4096)

    async def test_ignores_audio_when_idle(self, dictator):
        dictator.state = State.IDLE
        mock_soniox = AsyncMock()
        dictator.soniox = mock_soniox

        await dictator._handle_audio(b"\x00" * 4096)

        mock_soniox.send_audio.assert_not_called()

    async def test_ignores_audio_when_no_soniox(self, dictator):
        dictator.state = State.RECORDING
        dictator.soniox = None

        await dictator._handle_audio(b"\x00" * 4096)  # Should not raise


@pytest.mark.asyncio
class TestDictatorHandleAudioEnd:
    """Tests for _handle_audio_end."""

    async def test_finalizes_when_finalizing(self, dictator):
        dictator.state = State.FINALIZING
        with patch.object(
            dictator, "_finalize_transcription", new_callable=AsyncMock
        ) as mock_finalize:
            await dictator._handle_audio_end()
            mock_finalize.assert_called_once()

    async def test_ignores_when_not_finalizing(self, dictator):
        dictator.state = State.IDLE
        with patch.object(
            dictator, "_finalize_transcription", new_callable=AsyncMock
        ) as mock_finalize:
            await dictator._handle_audio_end()
            mock_finalize.assert_not_called()


@pytest.mark.asyncio
class TestDictatorFinalizeTranscription:
    """Tests for _finalize_transcription."""

    async def test_copies_to_clipboard_and_sends_paste(self, dictator):
        dictator.state = State.FINALIZING
        mock_soniox = MagicMock()
        mock_soniox.final_text = "Tere maailm"
        mock_soniox.pending_text = ""
        mock_soniox._translate_to = None
        mock_soniox.websocket = None
        mock_soniox.is_connected = False
        mock_soniox.close = AsyncMock()
        dictator.soniox = mock_soniox

        with (
            patch("soniox_dictate.clipboard.copy_to_clipboard") as mock_copy,
            patch.object(
                dictator, "_send_to_client", new_callable=AsyncMock
            ) as mock_send,
        ):
            await dictator._finalize_transcription()

        mock_copy.assert_called_once_with("Tere maailm")
        mock_send.assert_called_with("paste")

    async def test_sends_paste_enter_when_enter_mode(self, dictator):
        dictator.state = State.FINALIZING
        dictator.enter_mode = EnterMode.ENTER
        mock_soniox = MagicMock()
        mock_soniox.final_text = "Tere maailm"
        mock_soniox.pending_text = ""
        mock_soniox._translate_to = None
        mock_soniox.websocket = None
        mock_soniox.is_connected = False
        mock_soniox.close = AsyncMock()
        dictator.soniox = mock_soniox

        with (
            patch("soniox_dictate.clipboard.copy_to_clipboard"),
            patch.object(
                dictator, "_send_to_client", new_callable=AsyncMock
            ) as mock_send,
        ):
            await dictator._finalize_transcription()

        mock_send.assert_called_with("paste_enter")

    async def test_sends_paste_ctrl_enter_when_ctrl_enter_mode(self, dictator):
        dictator.state = State.FINALIZING
        dictator.enter_mode = EnterMode.CTRL_ENTER
        mock_soniox = MagicMock()
        mock_soniox.final_text = "Tere maailm"
        mock_soniox.pending_text = ""
        mock_soniox._translate_to = None
        mock_soniox.websocket = None
        mock_soniox.is_connected = False
        mock_soniox.close = AsyncMock()
        dictator.soniox = mock_soniox

        with (
            patch("soniox_dictate.clipboard.copy_to_clipboard"),
            patch.object(
                dictator, "_send_to_client", new_callable=AsyncMock
            ) as mock_send,
        ):
            await dictator._finalize_transcription()

        mock_send.assert_called_with("paste_ctrl_enter")

    async def test_uses_pending_text_as_fallback(self, dictator):
        dictator.state = State.FINALIZING
        mock_soniox = MagicMock()
        mock_soniox.final_text = "Tere "
        mock_soniox.pending_text = "maailm"
        mock_soniox._translate_to = None
        mock_soniox.websocket = None
        mock_soniox.is_connected = False
        mock_soniox.close = AsyncMock()
        dictator.soniox = mock_soniox

        with (
            patch("soniox_dictate.clipboard.copy_to_clipboard") as mock_copy,
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._finalize_transcription()

        mock_copy.assert_called_once_with("Tere maailm")

    async def test_uses_translated_text_when_available(self, dictator):
        dictator.state = State.FINALIZING
        mock_soniox = MagicMock()
        mock_soniox.final_text = "Tere maailm"
        mock_soniox.pending_text = ""
        mock_soniox._translate_to = "en"
        mock_soniox.translated_text = "Hello world"
        mock_soniox.pending_translated_text = ""
        mock_soniox.websocket = None
        mock_soniox.is_connected = False
        mock_soniox.close = AsyncMock()
        dictator.soniox = mock_soniox

        with (
            patch("soniox_dictate.clipboard.copy_to_clipboard") as mock_copy,
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._finalize_transcription()

        mock_copy.assert_called_once_with("Hello world")

    async def test_no_speech_detected(self, dictator):
        dictator.state = State.FINALIZING
        mock_soniox = MagicMock()
        mock_soniox.final_text = ""
        mock_soniox.pending_text = ""
        mock_soniox._translate_to = None
        mock_soniox.websocket = None
        mock_soniox.is_connected = False
        mock_soniox.close = AsyncMock()
        dictator.soniox = mock_soniox

        with (
            patch("soniox_dictate.clipboard.copy_to_clipboard") as mock_copy,
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._finalize_transcription()

        mock_copy.assert_not_called()
        assert dictator.state == State.IDLE

    async def test_state_returns_to_idle(self, dictator):
        dictator.state = State.FINALIZING
        mock_soniox = MagicMock()
        mock_soniox.final_text = "text"
        mock_soniox.pending_text = ""
        mock_soniox._translate_to = None
        mock_soniox.websocket = None
        mock_soniox.is_connected = False
        mock_soniox.close = AsyncMock()
        dictator.soniox = mock_soniox

        with (
            patch("soniox_dictate.clipboard.copy_to_clipboard"),
            patch.object(dictator, "_send_to_client", new_callable=AsyncMock),
        ):
            await dictator._finalize_transcription()

        assert dictator.state == State.IDLE


@pytest.mark.asyncio
class TestDictatorCleanupRecording:
    """Tests for _cleanup_recording."""

    async def test_closes_soniox(self, dictator):
        mock_soniox = AsyncMock()
        dictator.soniox = mock_soniox

        await dictator._cleanup_recording()

        mock_soniox.close.assert_called_once()
        assert dictator.soniox is None

    async def test_handles_no_soniox(self, dictator):
        dictator.soniox = None
        await dictator._cleanup_recording()  # Should not raise


@pytest.mark.asyncio
class TestDictatorSendToClient:
    """Tests for _send_to_client."""

    async def test_sends_message_with_newline(self, dictator):
        await dictator._send_to_client("started")
        dictator._client_writer.write.assert_called_once_with(b"started\n")

    async def test_no_error_when_no_client(self, dictator):
        dictator._client_writer = None
        await dictator._send_to_client("started")  # Should not raise

    async def test_no_error_when_client_closing(self, dictator):
        dictator._client_writer.is_closing.return_value = True
        await dictator._send_to_client("started")
        dictator._client_writer.write.assert_not_called()
