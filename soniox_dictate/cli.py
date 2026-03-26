"""CLI entry point for soniox-dictate."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="soniox-dictate",
        description="Real-time speech-to-text dictation using Soniox API.",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="et",
        choices=["et", "en"],
        help="Output language: et=Estonian, en=English translation (default: et)",
    )
    parser.add_argument(
        "--enter-mode",
        type=str,
        default="off",
        choices=["off", "enter", "ctrl+enter"],
        help="Key after paste: off/enter/ctrl+enter (default: off)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Load .env file if present
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # python-dotenv is optional at runtime if env vars are set directly

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    from soniox_dictate.dictator import Dictator

    try:
        asyncio.run(Dictator(lang=args.lang, enter_mode=args.enter_mode).run())
    except KeyboardInterrupt:
        print("\nExiting dictation.")
