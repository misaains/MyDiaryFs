"""Read-only network probes with deliberately invalid credentials."""
import asyncio
import datetime
import os

import requests
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramUnauthorizedError
from groq import AuthenticationError, DefaultHttpxClient, Groq

from config import OUTBOUND_PROXY, TELEGRAM_PROXY, apply_proxy_environment, read_settings


async def main():
    apply_proxy_environment(read_settings())
    bot = Bot("123456789:" + "a" * 35, session=AiohttpSession(proxy=TELEGRAM_PROXY or None, timeout=10))
    try:
        await bot.get_me()
        raise RuntimeError("Probe credentials unexpectedly accepted")
    except TelegramUnauthorizedError:
        print("Telegram through AiohttpSession: expected 401; TLS and proxy OK", flush=True)
    finally:
        await bot.session.close()

    with Groq(api_key="network-probe-invalid", max_retries=0, timeout=10,
              http_client=DefaultHttpxClient(proxy=OUTBOUND_PROXY or None)) as client:
        try:
            client.models.list()
            raise RuntimeError("Probe credentials unexpectedly accepted")
        except AuthenticationError:
            print("Groq through its Python SDK: expected 401; TLS and proxy OK", flush=True)

    response = requests.get("https://platform.fatsecret.com/rest/server.api",
                            params={"method": "foods.search", "format": "json"}, timeout=10)
    response.raise_for_status()
    body = response.json()
    assert "error" in body, "An unauthenticated probe should require credentials"
    print("FatSecret through requests: JSON API reached; credentials required", flush=True)
    print("Container local time: " + str(datetime.datetime.now().astimezone()), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
