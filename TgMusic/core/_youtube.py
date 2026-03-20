import asyncio
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

from py_yt import Playlist, VideosSearch
from pytdbot import types
from yt_dlp import YoutubeDL

from TgMusic.logger import LOGGER

from ._config import config
from ._dataclass import MusicTrack, PlatformTracks, TrackInfo
from ._downloader import MusicService
from ._httpx import HttpxClient

concurrent_fragment_downloads = 60

class YouTubeUtils:
    YOUTUBE_VIDEO_PATTERN = re.compile(
        r"^(?:https?://)?(?:www\.)?(?:youtube\.com|music\.youtube\.com|youtu\.be)/"
        r"(?:watch\?v=|embed/|v/|shorts/)?([\w-]{11})(?:\?|&|$)",
        re.IGNORECASE,
    )
    YOUTUBE_PLAYLIST_PATTERN = re.compile(
        r"^(?:https?://)?(?:www\.)?(?:youtube\.com|music\.youtube\.com)/"
        r"(?:playlist|watch)\?.*\blist=([\w-]+)",
        re.IGNORECASE,
    )
    YOUTUBE_SHORTS_PATTERN = re.compile(
        r"^(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w-]+)",
        re.IGNORECASE,
    )

    @staticmethod
    def clean_query(query: str) -> str:
        return query.split("&")[0].split("#")[0].strip()

    @staticmethod
    def is_valid_url(url: Optional[str]) -> bool:
        if not url:
            return False
        return any(
            pattern.match(url)
            for pattern in (
                YouTubeUtils.YOUTUBE_VIDEO_PATTERN,
                YouTubeUtils.YOUTUBE_PLAYLIST_PATTERN,
                YouTubeUtils.YOUTUBE_SHORTS_PATTERN,
            )
        )

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        for pattern in (
            YouTubeUtils.YOUTUBE_VIDEO_PATTERN,
            YouTubeUtils.YOUTUBE_SHORTS_PATTERN,
        ):
            if match := pattern.match(url):
                return match.group(1)
        return None

    @staticmethod
    async def normalize_youtube_url(url: str) -> Optional[str]:
        if not url:
            return None
        if "youtu.be/" in url:
            video_id = url.split("youtu.be/")[1].partition("?")[0].partition("#")[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        if "youtube.com/shorts/" in url:
            video_id = url.split("youtube.com/shorts/")[1].split("?")[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        return url

    @staticmethod
    def create_platform_tracks(data: Dict[str, Any]) -> PlatformTracks:
        if not data or not data.get("results"):
            return PlatformTracks(tracks=[])
        valid_tracks = [
            MusicTrack(**track)
            for track in data["results"]
            if track and track.get("id")
        ]
        return PlatformTracks(tracks=valid_tracks)

    @staticmethod
    def format_track(track_data: Dict[str, Any]) -> Dict[str, Any]:
        duration = track_data.get("duration", "0:00")
        if isinstance(duration, dict):
            duration = duration.get("secondsText", "0:00")

        cover_url = ""
        if thumbnails := track_data.get("thumbnails"):
            for thumb in reversed(thumbnails):
                if url := thumb.get("url"):
                    cover_url = url
                    break

        return {
            "id": track_data.get("id", ""),
            "name": track_data.get("title", "Unknown Title"),
            "duration": YouTubeUtils.duration_to_seconds(duration),
            "cover": cover_url,
            "year": 0,
            "url": f"https://www.youtube.com/watch?v={track_data.get('id', '')}",
            "platform": "youtube",
        }

    @staticmethod
    async def create_track_info(track_data: dict[str, Any]) -> TrackInfo:
        return TrackInfo(
            cdnurl="None",
            key="None",
            name=track_data.get("name", "Unknown Title"),
            tc=track_data.get("id", ""),
            cover=track_data.get("cover", ""),
            duration=track_data.get("duration", 0),
            platform="youtube",
            url=f"https://youtube.com/watch?v={track_data.get('id', '')}",
        )

    @staticmethod
    def duration_to_seconds(duration: str) -> int:
        if not duration:
            return 0
        try:
            parts = list(map(int, duration.split(":")))
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0]
        except (ValueError, AttributeError):
            return 0

    @staticmethod
    async def get_cookie_file() -> Optional[str]:
        cookie_dir = "TgMusic/cookies"
        try:
            if not os.path.exists(cookie_dir):
                return None
            files = await asyncio.to_thread(os.listdir, cookie_dir)
            cookies_files = [f for f in files if f.endswith(".txt")]
            if not cookies_files:
                LOGGER.warning("No cookie files found in '%s'.", cookie_dir)
                return None
            random_file = random.choice(cookies_files)
            return os.path.join(cookie_dir, random_file)
        except Exception as e:
            LOGGER.warning("Error accessing cookie directory: %s", e)
            return None

    @staticmethod
    async def fetch_oembed_data(url: str) -> Optional[dict[str, Any]]:
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        data = await HttpxClient().make_request(oembed_url, max_retries=1)
        if data:
            if "v=" in url:
                video_id = url.split("v=")[1].split("&")[0]
            else:
                video_id = YouTubeUtils._extract_video_id(url) or ""
            return {
                "results": [
                    {
                        "id": video_id,
                        "name": data.get("title"),
                        "duration": 0,
                        "artist": data.get("author_name", ""),
                        "cover": data.get("thumbnail_url", ""),
                        "year": 0,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "platform": "youtube",
                    }
                ]
            }
        return None

    @staticmethod
    async def download_with_api(
        video_id: str, is_video: bool = False
    ) -> Union[None, Path]:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        httpx = HttpxClient()
        get_track = await httpx.make_request(
            f"{config.API_URL}/track?url={video_url}&video={is_video}"
        )
        if not get_track:
            LOGGER.error("Response from API is empty")
            return None

        track = TrackInfo(**get_track)
        cdnurl = track.cdnurl
        if not cdnurl:
            LOGGER.error("CDN URL not found in response")
            return None

        if not re.fullmatch(r"https:\/\/t\.me\/([a-zA-Z0-9_]{5,})\/(\d+)", cdnurl):
            dl = await httpx.download_file(cdnurl)
            return dl.file_path if dl.success else None

        from TgMusic import client

        info = await client.getMessageLinkInfo(cdnurl)
        if isinstance(info, types.Error) or info.message is None:
            LOGGER.error(f"❌ Could not resolve message from link: {cdnurl}; {info}")
            return None

        msg = await client.getMessage(info.chat_id, info.message.id)
        if isinstance(msg, types.Error):
            LOGGER.error(f"❌ Failed to fetch message with ID {info.message.id}; {msg}")
            return None

        file = await msg.download()
        if isinstance(file, types.Error):
            LOGGER.error(
                f"❌ Failed to download message with ID {info.message.id}; {file}"
            )
            return None
        return Path(file.path)

    @staticmethod
    def _format_candidates(video: bool) -> list[str]:
        if video:
            return [
                "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]",
                "bestvideo+bestaudio/best",
                "best[height<=1080]/best",
                "bv*+ba/b",
            ]
        return [
            "bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio[ext=webm]/bestaudio/best",
            "bestaudio/best",
            "ba/b",
        ]

    @staticmethod
    def _build_ydl_opts(
        fmt: str, cookie_file: Optional[str], output_template: str, video: bool
    ) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "format": fmt,
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "retries": 1,
            "continuedl": True,
            "nopart": True,
            "concurrent_fragment_downloads": concurrent_fragment_downloads ,
            "socket_timeout": 60,
            "throttledratelimit": 100 * 1024,
            "noplaylist": True,
            "writethumbnail": False,
            "writeinfojson": False,
            "postprocessors": [],
            "js_runtimes": {"deno": {}},
            "remote_components": ["ejs:github"],
            "restrictfilenames": False,
        }

        if video:
            opts["merge_output_format"] = "mp4"

        if config.PROXY:
            opts["proxy"] = config.PROXY
        elif cookie_file:
            opts["cookiefile"] = cookie_file

        return opts

    @staticmethod
    def _extract_filepath(info: dict[str, Any]) -> Optional[Path]:
        requested_downloads = info.get("requested_downloads") or []
        for item in requested_downloads:
            filepath = item.get("filepath")
            if filepath and Path(filepath).exists():
                return Path(filepath)

        filepath = info.get("filepath")
        if filepath and Path(filepath).exists():
            return Path(filepath)

        _filename = info.get("_filename")
        if _filename and Path(_filename).exists():
            return Path(_filename)

        id_ = info.get("id")
        ext = info.get("ext")
        if id_ and ext:
            guessed = config.DOWNLOADS_DIR / f"{id_}.{ext}"
            if guessed.exists():
                return guessed

        return None

    @staticmethod
    def _run_ytdlp(url: str, opts: dict[str, Any]) -> Optional[Path]:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            return YouTubeUtils._extract_filepath(info)

    @staticmethod
    async def download_with_yt_dlp(video_id: str, video: bool) -> Optional[Path]:
        cookie_file = await YouTubeUtils.get_cookie_file()
        output_template = str(config.DOWNLOADS_DIR / "%(id)s.%(ext)s")
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        for fmt in YouTubeUtils._format_candidates(video):
            try:
                opts = YouTubeUtils._build_ydl_opts(
                    fmt, cookie_file, output_template, video
                )
                downloaded_path = await asyncio.wait_for(
                    asyncio.to_thread(YouTubeUtils._run_ytdlp, video_url, opts),
                    timeout=600,
                )
                if downloaded_path and downloaded_path.exists():
                    return downloaded_path
            except asyncio.TimeoutError:
                LOGGER.error("yt-dlp timed out for video ID: %s", video_id)
                return None
            except Exception as e:
                LOGGER.warning(
                    "yt-dlp attempt failed for %s with format '%s': %s",
                    video_id,
                    fmt,
                    e,
                )
                continue

        LOGGER.error("All yt-dlp format fallbacks failed for %s", video_id)
        return None


class YouTubeData(MusicService):
    def __init__(self, query: Optional[str] = None) -> None:
        self.query = YouTubeUtils.clean_query(query) if query else None

    def is_valid(self) -> bool:
        return YouTubeUtils.is_valid_url(self.query)

    async def get_info(self) -> Union[PlatformTracks, types.Error]:
        if not self.query or not self.is_valid():
            return types.Error(code=400, message="Invalid YouTube URL provided")

        data = await self._fetch_data(self.query)
        if not data:
            return types.Error(code=404, message="Could not retrieve track information")

        return YouTubeUtils.create_platform_tracks(data)

    async def search(self) -> Union[PlatformTracks, types.Error]:
        if not self.query:
            return types.Error(code=400, message="No search query provided")

        if self.is_valid():
            return await self.get_info()

        try:
            search = VideosSearch(self.query, limit=5)
            results = await search.next()

            if not results or not results.get("result"):
                return types.Error(
                    code=404, message=f"No results found for: {self.query}"
                )

            tracks = [
                MusicTrack(**YouTubeUtils.format_track(video))
                for video in results["result"]
            ]
            return PlatformTracks(tracks=tracks)

        except Exception as error:
            LOGGER.error(f"YouTube search failed for '{self.query}': {error}")
            return types.Error(code=500, message=f"Search failed: {str(error)}")

    async def get_track(self) -> Union[TrackInfo, types.Error]:
        if not self.query:
            return types.Error(code=400, message="No track identifier provided")

        url = (
            self.query
            if re.match("^https?://", self.query)
            else f"https://youtube.com/watch?v={self.query}"
        )

        data = await self._fetch_data(url)
        if not data or not data.get("results"):
            return types.Error(code=404, message="Could not retrieve track details")

        return await YouTubeUtils.create_track_info(data["results"][0])

    async def download_track(
        self, track: TrackInfo, video: bool = False
    ) -> Union[Path, types.Error]:
        if not track:
            return types.Error(code=400, message="Invalid track information provided")

        if config.API_URL and config.API_KEY:
            if api_result := await YouTubeUtils.download_with_api(track.tc, video):
                return api_result

        dl_path = await YouTubeUtils.download_with_yt_dlp(track.tc, video)
        if not dl_path:
            return types.Error(
                code=500, message="Failed to download track from YouTube"
            )

        return dl_path

    async def _fetch_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            if YouTubeUtils.YOUTUBE_PLAYLIST_PATTERN.match(url):
                return await self._get_playlist_data(url)
            return await self._get_video_data(url)
        except Exception as error:
            LOGGER.error(f"Data fetch failed for {url}: {error}")
            return None

    @staticmethod
    async def _get_video_data(url: str) -> Optional[Dict[str, Any]]:
        normalized_url = await YouTubeUtils.normalize_youtube_url(url)
        if not normalized_url:
            return None

        if oembed_data := await YouTubeUtils.fetch_oembed_data(normalized_url):
            return oembed_data

        try:
            search = VideosSearch(normalized_url, limit=1)
            results = await search.next()

            if not results or not results.get("result"):
                return None

            return {"results": [YouTubeUtils.format_track(results["result"][0])]}
        except Exception as error:
            LOGGER.error(f"Video data fetch failed: {error}")
            return None

    @staticmethod
    async def _get_playlist_data(url: str) -> Optional[Dict[str, Any]]:
        try:
            playlist = await Playlist.getVideos(url)
            if not playlist or not playlist.get("videos"):
                return None

            return {
                "results": [
                    YouTubeUtils.format_track(track)
                    for track in playlist["videos"]
                    if track.get("id")
                ]
            }
        except Exception as error:
            LOGGER.error(f"Playlist data fetch failed: {error}")
            return None
