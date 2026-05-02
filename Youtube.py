import asyncio
import os
import re
import logging
import aiohttp
import yt_dlp
from typing import Union, Optional, Tuple, List
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch, Playlist
from NEOMUSIC.utils.formatters import time_to_seconds
from NEOMUSIC import LOGGER

# --- CONFIGURATION ---
try:
    from config import API_ID, BOT_TOKEN, MONGO_DB_URI
except ImportError:
    LOGGER.error("Config file not found!")

# --- SECURITY FILTER ---
class SensitiveDataFilter(logging.Filter):
    def filter(self, record):
        msg = str(record.msg)
        patterns = [r"\d{8,10}:[a-zA-Z0-9_-]{35,}", r"mongodb\+srv://\S+"]
        for pattern in patterns:
            msg = re.sub(pattern, "[PROTECTED]", msg)
        record.msg = msg
        return True

logging.getLogger().addFilter(SensitiveDataFilter())

API_URL = "http://kiru-bot.up.railway.app"

# --- UTILS ---
def get_clean_id(link: str) -> Optional[str]:
    """Extracts and sanitizes YouTube Video ID"""
    if "v=" in link:
        video_id = link.split('v=')[-1].split('&')[0]
    elif "youtu.be/" in link:
        video_id = link.split('youtu.be/')[-1].split('?')[0]
    else:
        video_id = link
    clean_id = re.sub(r'[^a-zA-Z0-9_-]', '', video_id)
    return clean_id if 5 <= len(clean_id) <= 15 else None

async def get_direct_stream_link(link: str, media_type: str) -> Optional[str]:
    """Generates direct streamable URL via API"""
    video_id = get_clean_id(link)
    if not video_id:
        return None

    try:
        timeout = aiohttp.ClientTimeout(total=15) 
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout) as session:
            async with session.get(f"{API_URL}/download", params={"url": video_id, "type": media_type}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    token = data.get("download_token")
                    if token:
                        return f"{API_URL}/stream/{video_id}?type={media_type}&token={token}"
    except Exception:
        pass # Fallback to yt-dlp will handle this
    return None

class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.listbase = "https://youtube.com/playlist?list="

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message: Message) -> Optional[str]:
        """Extracts URL from message or replied message"""
        messages = [message, message.reply_to_message]
        for msg in messages:
            if not msg: continue
            text = msg.text or msg.caption
            if not text: continue

            if msg.entities:
                for entity in msg.entities:
                    if entity.type == MessageEntityType.URL:
                        return text[entity.offset : entity.offset + entity.length]
            
            urls = re.findall(r'(https?://\S+)', text)
            if urls: return urls[0]
        return None

    async def search(self, query: str, limit: int = 1):
        """Search videos using youtubesearchpython"""
        try:
            search = VideosSearch(query, limit=limit)
            resp = await search.next()
            return resp.get("result", [])
        except Exception as e:
            LOGGER.error(f"Search Error: {e}")
            return []

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        try:
            # Check if it's a direct URL or a search query
            if not await self.exists(link):
                res = await self.search(link, limit=1)
            else:
                link = link.split("&")[0]
                results = VideosSearch(link, limit=1)
                res_data = await results.next()
                res = res_data.get("result", [])

            if not res: return None
            video = res[0]
            return (
                video["title"],
                video.get("duration", "00:00"),
                int(time_to_seconds(video.get("duration", "00:00"))),
                video["thumbnails"][0]["url"].split("?")[0],
                video["id"]
            )
        except Exception as e:
            LOGGER.error(f"Details Error: {e}")
            return None

    async def track(self, query: str, videoid: Union[bool, str] = None):
        det = await self.details(query, videoid)
        if not det: return None, None
        track_details = {
            "title": det[0],
            "link": self.base + det[4],
            "vidid": det[4],
            "duration_min": det[1],
            "thumb": det[3],
        }
        return track_details, det[4]

    async def download(
        self,
        link: str,
        mystic=None,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        **kwargs
    ) -> Tuple[Optional[str], bool]:
        """Returns streamable URL. Fixes GroupcallInvalid by ensuring a valid link."""
        if videoid: link = self.base + link
        m_type = "video" if video else "audio"
        
        # 1. Pehle API se try karein (Fastest)
        stream_link = await get_direct_stream_link(link, m_type)
        if stream_link:
            return stream_link, True
        
        # 2. Fallback: yt-dlp (Strongest) - Isse GroupcallInvalid solve ho jayega
        try:
            ydl_opts = {
                "format": "bestaudio/best" if not video else "bestvideo+bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "geo_bypass": True,
                "nocheckcertificate": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, link, download=False)
                if 'url' in info:
                    return info['url'], True
        except Exception as e:
            LOGGER.error(f"Download Error: {e}")
            
        return None, False

# Initialize
YouTube = YouTubeAPI()
