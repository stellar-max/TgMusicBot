#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

import asyncio
import os
import uuid
from urllib.parse import urlparse

import aiofiles
import aiohttp

from TgMusic.logger import LOGGER


def resolve_raw_url(url: str) -> str:
    paste_id = url.strip("/").split("/")[-1]

    if "pastebin.com" in url and "/raw/" not in url:
        return f"https://pastebin.com/raw/{paste_id}"

    if "batbin.me" in url and "/raw/" not in url:
        return f"https://batbin.me/raw/{paste_id}"

    return url


async def fetch_content(session: aiohttp.ClientSession, url: str) -> str | None:
    resolved_url = resolve_raw_url(url)

    try:
        async with session.get(resolved_url) as response:
            if response.status != 200:
                LOGGER.error("Failed to download %s: %s", resolved_url, response.status)
                return None

            content_type = (response.headers.get("Content-Type") or "").lower()
            text = await response.text()

            if not text or not text.strip():
                LOGGER.error("Empty response from %s", resolved_url)
                return None

            if (
                "text" in content_type
                or "json" in content_type
                or "octet-stream" in content_type
                or resolved_url.endswith(".txt")
                or resolved_url.endswith(".cookies")
            ):
                return text

            return text
    except Exception as e:
        LOGGER.error("Error fetching %s: %s", resolved_url, e)
        return None


async def save_bin_content(session: aiohttp.ClientSession, url: str) -> str | None:
    parsed = urlparse(url)
    filename = (
        (parsed.path.strip("/").split("/")[-1] or str(uuid.uuid4()).split("-")[0])
        .split("?")[0]
        .split("#")[0]
    )

    if not filename or filename in {"raw", "download"}:
        filename = str(uuid.uuid4()).split("-")[0]

    if not filename.endswith(".txt"):
        filename += ".txt"

    filepath = os.path.join("TgMusic/cookies", filename)

    content = await fetch_content(session, url)
    if content:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(content)
            return filepath
        except Exception as e:
            LOGGER.error("Error saving file %s: %s", filepath, e)

    return None


async def save_all_cookies(cookie_urls: list[str]) -> list[str]:
    async with aiohttp.ClientSession() as session:
        tasks = [save_bin_content(session, url) for url in cookie_urls]
        results = await asyncio.gather(*tasks)

    return [res for res in results if res]
