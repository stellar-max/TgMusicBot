# Copyright (c) 2025 AshokShau
# Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
# Part of the TgMusicBot project. All rights reserved where applicable.

import asyncio
import json
from datetime import datetime

from pytdbot import Client, types
from pytdbot.tdjson.tdjson import TdJson

__version__ = "1.2.4"
StartTime = datetime.now()


def _patch_pytdbot_tdjson() -> None:
    def execute(self, request):
        if not isinstance(request, str):
            request = json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        res = self._td_execute(request)
        return json.loads(res) if res else None

    def send(self, client_id, query):
        if not isinstance(query, str):
            query = json.dumps(
                query,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return self._td_send(client_id, query)

    TdJson.execute = execute
    TdJson.send = send


_patch_pytdbot_tdjson()

from TgMusic.core import call, config, db, tg


class Bot(Client):
    def __init__(self) -> None:
        super().__init__(
            token=config.TOKEN,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            default_parse_mode="html",
            td_log=types.LogStreamEmpty(),
            plugins=types.plugins.Plugins(folder="TgMusic/modules"),
            files_directory="",
            database_encryption_key="",
            options={"ignore_background_updates": config.IGNORE_BACKGROUND_UPDATES},
        )
        self._initialize_services()

    def _initialize_services(self) -> None:
        from TgMusic.modules.jobs import InactiveCallManager

        self.config = config
        self.db = db
        self.call = call
        self.tg = tg
        self.call_manager = InactiveCallManager(self)
        self._start_time = StartTime
        self._version = __version__

    async def start_clients(self) -> None:
        try:
            await asyncio.gather(
                *[
                    self.call.start_client(config.API_ID, config.API_HASH, session_str)
                    for session_str in config.SESSION_STRINGS
                ]
            )
        except Exception as exc:
            raise SystemExit(1) from exc

    async def initialize_components(self) -> None:
        from TgMusic.core import save_all_cookies

        await save_all_cookies(config.COOKIES_URL)
        await self.db.ping()
        await self.start_clients()
        await self.call.add_bot(self)
        await self.call.register_decorators()
        await super().start()
        await self.call_manager.start()
        uptime = self._get_uptime()
        self.logger.info(f"Bot started successfully in {uptime:.2f} seconds")
        self.logger.info(f"Version: {self._version}")

    async def stop_task(self) -> None:
        self.logger.info("Stopping bot...")
        try:
            shutdown_tasks = [
                self.db.close(),
                self.call_manager.stop(),
                self.call.stop_all_clients(),
            ]
            await asyncio.gather(*shutdown_tasks)
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}", exc_info=True)
            raise

    def _get_uptime(self) -> float:
        return (datetime.now() - self._start_time).total_seconds()


client: Bot = Bot()
