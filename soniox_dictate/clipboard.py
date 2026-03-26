"""Clipboard copy for WSL/Linux."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CLIP_EXE = Path("/mnt/c/Windows/system32/clip.exe")


def copy_to_clipboard(text: str) -> None:
    """Copy text to system clipboard (WSL clip.exe or xclip fallback)."""
    encoded = text.encode("utf-16-le")  # clip.exe expects UTF-16 LE for Unicode

    if CLIP_EXE.exists():
        subprocess.run(
            [str(CLIP_EXE)],
            input=encoded,
            check=True,
            timeout=5,
        )
    else:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
            timeout=5,
        )
    logger.info("Copied %d chars to clipboard", len(text))
