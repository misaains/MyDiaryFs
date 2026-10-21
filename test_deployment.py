import asyncio
import datetime
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiogram import Bot, Dispatcher, F
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from access_control import install_access_guard
from config import configuration_errors, parse_allowed_ids, read_settings

TOKEN = "123456789:" + "a" * 35


def valid_settings():
    return {
        "TELEGRAM_BOT_TOKEN": TOKEN, "GROQ_API_KEY": "fixture-key",
        "FATSECRET_CONSUMER_KEY": "fixture-key", "FATSECRET_CONSUMER_SECRET": "fixture-secret",
        "ALLOWED_TELEGRAM_IDS": "123", "TELEGRAM_PROXY": "", "OUTBOUND_PROXY": "",
    }


class ConfigurationTests(unittest.TestCase):
    def test_direct_connections_are_valid_and_bad_proxies_are_rejected(self):
        self.assertEqual(configuration_errors(valid_settings()), [])
        for proxy in ("http://:8118", "http://host:notaport", "http://host:999999", "file:///secret", "http://host:8118/path"):
            settings = valid_settings()
            settings["OUTBOUND_PROXY"] = proxy
            with self.subTest(proxy=proxy):
                self.assertIn("OUTBOUND_PROXY", configuration_errors(settings))

    def test_environment_can_supply_settings_without_a_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, valid_settings(), clear=True):
            self.assertEqual(configuration_errors(read_settings(Path(directory) / "missing.env")), [])

    def test_empty_or_malformed_allowlist_never_permits_startup(self):
        for value in ("", " ", "@name", "123,", "0", "-123", "123,bad", "1.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_allowed_ids(value)
        self.assertEqual(parse_allowed_ids("123, 456"), frozenset({123, 456}))

    def test_blank_template_is_not_ready_and_values_are_not_interpolated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.env"
            path.write_text("TELEGRAM_BOT_TOKEN=\nGROQ_API_KEY=abc${HOME}$def\nALLOWED_TELEGRAM_IDS=\n")
            settings = read_settings(path)
            self.assertEqual(settings["GROQ_API_KEY"], "abc${HOME}$def")
            self.assertIn("ALLOWED_TELEGRAM_IDS", configuration_errors(settings))
            self.assertIn("TELEGRAM_BOT_TOKEN", configuration_errors(settings))

    def test_native_bot_import_uses_local_configuration_without_a_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("\n".join(f"{key}={value}" for key, value in valid_settings().items()))
            environment = dict(os.environ)
            for key in valid_settings():
                environment.pop(key, None)
            environment["MYDIARY_ENV_FILE"] = str(path)
            environment["PYTHONPATH"] = str(Path(__file__).parent)
            code = "import asyncio,bot; assert bot.allowed_ids == frozenset({123}); asyncio.run(bot.bot.session.close())"
            result = subprocess.run([sys.executable, "-c", code], cwd=directory, env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_rereads_configuration_before_starting_bot(self):
        import launch
        with patch("launch.read_settings", side_effect=[{}, valid_settings()]), \
             patch("launch.time.sleep") as sleep, patch("launch.set_status") as status, \
             patch("launch.os.umask"), patch("launch.os.execv", side_effect=SystemExit) as execute:
            with self.assertRaises(SystemExit):
                launch.main()
            sleep.assert_called_once_with(10)
            self.assertEqual(status.call_args_list[0].args[0], "waiting-for-configuration")
            self.assertEqual(status.call_args_list[-1].args[0], "starting-bot")
            self.assertEqual(execute.call_args.args[1][-1], str(Path(launch.__file__).with_name("bot.py")))


class DispatcherAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_dispatcher_blocks_strangers_groups_and_missing_identity(self):
        for allowed in (frozenset(), frozenset({123})):
            dispatcher = Dispatcher(disable_fsm=True)
            install_access_guard(dispatcher, allowed)
            seen = []

            async def message_handler(message):
                seen.append(("message", message.from_user.id))

            async def callback_handler(callback):
                seen.append(("callback", callback.from_user.id))

            dispatcher.message.register(message_handler)
            dispatcher.callback_query.register(callback_handler)
            bot = Bot(TOKEN)
            now = datetime.datetime.now(datetime.timezone.utc)
            try:
                counter = 0
                for user_id, chat_id, chat_type in (
                    (999, 999, "private"), (123, -100001, "supergroup"),
                    (999, 123, "private"), (123, 123, "private"),
                ):
                    user = User(id=user_id, is_bot=False, first_name="Test")
                    message = Message(message_id=1, date=now, chat=Chat(id=chat_id, type=chat_type), from_user=user, text="/login")
                    counter += 1
                    await dispatcher.feed_update(bot, Update(update_id=counter, message=message))
                    counter += 1
                    await dispatcher.feed_update(bot, Update(update_id=counter, callback_query=CallbackQuery(id=str(counter), from_user=user, chat_instance="test", data="confirm_save", message=message)))
                no_user = Message(message_id=2, date=now, chat=Chat(id=123, type="private"), text="test")
                await dispatcher.feed_update(bot, Update(update_id=99, message=no_user))
                self.assertEqual(seen, [("message", 123), ("callback", 123)] if allowed else [])
            finally:
                await bot.session.close()


if __name__ == "__main__":
    unittest.main()
