"""Outbound Telegram send helpers.

Two hard rules enforced here for every outbound message:
  1. Link previews are ALWAYS disabled (LinkPreviewOptions(is_disabled=True)) —
     Telegram auto-fetches previewed URLs server-side, which is a data-exfil
     channel if a reply ever contains an attacker-influenced link.
  2. Plain text only (no parse_mode) — persona replies are arbitrary text; markup
     parsing would both break on stray characters and open a markup-injection path.

Messages over the char limit are split via splitter.split_for_telegram.

Both rules extend to media CAPTIONS — a caption is the same surface as a
message body, so ``send_document`` passes no ``parse_mode`` either.
"""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import Bot, LinkPreviewOptions
from telegram.constants import MessageLimit

from .splitter import split_for_telegram

logger = logging.getLogger(__name__)

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def send_text(bot: Bot, chat_id: int, text: str, limit: int = 4000) -> int:
    """Send one logical message, split into <=limit chunks. Returns chunk count."""
    chunks = split_for_telegram(text, limit)
    for chunk in chunks:
        await bot.send_message(chat_id=chat_id, text=chunk, link_preview_options=_NO_PREVIEW)
    return len(chunks)


async def send_messages(bot: Bot, chat_id: int, messages: list[str], limit: int = 4000) -> int:
    """Send an ordered list of logical messages sequentially. Returns total chunks."""
    total = 0
    for message in messages:
        total += await send_text(bot, chat_id, message, limit)
    return total


async def send_document(
    bot: Bot,
    chat_id: int,
    path: Path,
    *,
    filename: str,
    caption: str | None = None,
    protect_content: bool = True,
) -> None:
    """Upload a local file as a Telegram DOCUMENT, losslessly.

    Document rather than photo, deliberately: ``sendPhoto`` re-encodes
    server-side to JPEG and flattens alpha, with no way to opt out. Our PNGs
    clear every sendPhoto size and dimension limit — the re-encode is the
    problem, not the limits. sendDocument preserves the bytes and raises the
    cap from 10 MB to 50 MB.

    ``path`` is passed to PTB as a ``Path``, NOT as an open handle and NOT as
    bytes. PTB re-opens it inside each call, which is what makes a retry safe:
    an open handle would be at EOF on attempt 2 and would upload an empty body
    that returns ``ok: true`` — a silent corruption, the worst outcome for a
    retry.

    ⚠️ The inverse holds for ``edit_message_media`` when phase 2 adds it:
    ``InputMediaDocument`` calls ``parse_file_input(..., local_mode=True)`` and
    turns an existing ``Path`` into the literal string ``file:///abs/path``,
    uploading NOTHING and silently discarding ``filename``. That one must be
    given ``bytes``. Measured on PTB 22.8; do not "unify" the two call sites.

    No retry here — that is phase 2. When it lands, note that ``BadRequest``
    SUBCLASSES ``NetworkError`` in PTB, so the non-retryable exceptions must be
    caught first or a malformed request gets hammered three times.
    """
    # PTB derives the mimetype from the FILENAME's extension
    # (InputFile.__init__ -> mimetypes.guess_type). Measured on 22.8:
    # "Portrait" arrives as application/octet-stream with no thumbnail at all,
    # "Portrait.png" arrives as image/png. An extensionless name from the
    # coordinator would silently turn a picture into an opaque blob.
    if not Path(filename).suffix:
        filename = f"{filename}{path.suffix or '.png'}"
        logger.warning("[Media] filename had no extension; using %s", filename)

    if caption is not None and len(caption) > MessageLimit.CAPTION_LENGTH:
        # Truncate loudly. Silent truncation is a recurring failure shape here.
        logger.warning(
            "[Media] caption truncated from %d to %d chars",
            len(caption),
            MessageLimit.CAPTION_LENGTH,
        )
        caption = caption[: MessageLimit.CAPTION_LENGTH]

    await bot.send_document(
        chat_id=chat_id,
        document=path,
        filename=filename,
        caption=caption,
        protect_content=protect_content,
    )
