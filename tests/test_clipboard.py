"""Tests for clipboard module."""

from unittest.mock import patch

from soniox_dictate.clipboard import copy_to_clipboard


class TestCopyToClipboard:
    """Tests for copy_to_clipboard()."""

    @patch("soniox_dictate.clipboard.CLIP_EXE")
    @patch("soniox_dictate.clipboard.subprocess.run")
    def test_uses_clip_exe_when_available(self, mock_run, mock_clip_path):
        mock_clip_path.exists.return_value = True
        mock_clip_path.__str__ = lambda self: "/mnt/c/Windows/system32/clip.exe"

        copy_to_clipboard("Hello world")

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert str(mock_clip_path) in args[0][0]
        assert args[1]["input"] == "Hello world".encode("utf-16-le")
        assert args[1]["check"] is True

    @patch("soniox_dictate.clipboard.CLIP_EXE")
    @patch("soniox_dictate.clipboard.subprocess.run")
    def test_uses_xclip_fallback(self, mock_run, mock_clip_path):
        mock_clip_path.exists.return_value = False

        copy_to_clipboard("Hello world")

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][0] == ["xclip", "-selection", "clipboard"]
        assert args[1]["input"] == b"Hello world"

    @patch("soniox_dictate.clipboard.CLIP_EXE")
    @patch("soniox_dictate.clipboard.subprocess.run")
    def test_handles_unicode_text(self, mock_run, mock_clip_path):
        mock_clip_path.exists.return_value = True
        mock_clip_path.__str__ = lambda self: "/mnt/c/Windows/system32/clip.exe"

        copy_to_clipboard("Tere, maailm! Öö üü ää")

        args = mock_run.call_args
        assert args[1]["input"] == "Tere, maailm! Öö üü ää".encode("utf-16-le")

    @patch("soniox_dictate.clipboard.CLIP_EXE")
    @patch("soniox_dictate.clipboard.subprocess.run")
    def test_has_timeout(self, mock_run, mock_clip_path):
        mock_clip_path.exists.return_value = True
        mock_clip_path.__str__ = lambda self: "/mnt/c/Windows/system32/clip.exe"

        copy_to_clipboard("test")

        args = mock_run.call_args
        assert args[1]["timeout"] == 5
