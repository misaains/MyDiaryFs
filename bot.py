import asyncio
import os
import tempfile
import logging
import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import TELEGRAM_BOT_TOKEN
from ai_service import transcribe_audio_file, parse_food_text
from fatsecret_client import FatSecretClient

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
dp = Dispatcher()
fatsecret = FatSecretClient()

MEAL_NAMES = {
    "breakfast": "🌅 Завтрак",
    "lunch": "☀️ Обед",
    "dinner": "🌙 Ужин",
    "other": "🍏 Перекус"
}

FS_MEAL_NAMES = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "other": "Other"
}

def select_best_serving(servings, grams: float):
    """Выбирает граммовую порцию (100 г) и точно рассчитывает units для FatSecret."""
    if isinstance(servings, dict):
        servings = [servings]

    chosen = None
    for s in servings:
        desc = s.get("serving_description", "").lower()
        if "100 g" in desc or "100 г" in desc or desc == "100g" or "100 ml" in desc or "100 мл" in desc:
            chosen = s
            break

    if not chosen:
        for s in servings:
            u_unit = s.get("metric_serving_unit", "").lower()
            if u_unit in ["g", "ml", "г", "мл"]:
                chosen = s
                break

    if not chosen:
        chosen = servings[0]

    srv_id = chosen.get("serving_id")
    metric_amount = float(chosen.get("metric_serving_amount", 0) or 0)
    num_units = float(chosen.get("number_of_units", 1) or 1)

    if metric_amount > 0:
        units = grams * (num_units / metric_amount)
    else:
        units = 1.0

    return srv_id, units

# Временное хранилище распознанных приемов пищи
user_pending_meals = {}

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    auth_status = "✅ Привязан к FatSecret" if fatsecret.is_authorized() else "⚠️ Не привязан к FatSecret (/login)"
    await message.answer(
        f"👋 **Привет! Я твой голосовой дневник калорий.**\n"
        f"Статус: {auth_status}\n\n"
        "Я умею понимать как одиночные фразы, так и **большие списки за несколько дней**!\n"
        "Например, можно писать:\n"
        "• *«Позавчера на обед съела...»*\n"
        "• *«Вчера на ужин...»*\n"
        "• *«18.10 на обед... 18.10 на ужин...»*\n\n"
        "Просто отправьте текст или голосовое сообщение!",
        parse_mode="Markdown"
    )

@dp.message(Command("login"))
async def cmd_login(message: types.Message):
    try:
        auth_url = fatsecret.start_authorization()
        await message.answer(
            "🔗 **Связываем бота с вашим FatSecret на iPhone:**\n\n"
            f"1. Нажмите на ссылку: [Авторизовать в FatSecret]({auth_url})\n"
            "2. Войдите под своей почтой и паролем.\n"
            "3. Нажмите кнопку **«Разрешить» (Allow)**.\n"
            "4. FatSecret покажет вам проверочный **PIN-код** (цифры).\n"
            "5. **Пришлите этот код сюда в ответном сообщении!**",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Ошибка старта авторизации: {e}")
        await message.answer(f"❌ Не удалось получить ссылку для авторизации: {e}")

@dp.message(F.text.regexp(r"^\d{4,8}$"))
async def handle_pin(message: types.Message):
    pin = message.text.strip()
    try:
        fatsecret.complete_authorization(pin)
        await message.answer(
            "🎉 **Ура! Ваш аккаунт FatSecret успешно привязан!**\n\n"
            "Теперь все подтверждённые приёмы пищи будут автоматически попадать в нужные дни на iPhone!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка ввода PIN: {e}")
        await message.answer(f"❌ Ошибка проверки PIN-кода: {e}. Попробуйте снова через /login")

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    status_msg = await message.answer("🎧 Слушаю и расшифровываю голосовое...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_file:
        tmp_path = tmp_file.name

    try:
        await bot.download(message.voice, destination=tmp_path)
        text = await transcribe_audio_file(tmp_path)
        await status_msg.edit_text(f"🎤 **Вы сказали:**\n_{text}_\n\n⏳ Считаю граммы и даты...", parse_mode="Markdown")
        await process_food_input(message, text, status_msg)
    except Exception as e:
        logging.error(f"Ошибка обработки голоса: {e}")
        await status_msg.edit_text(f"❌ Произошла ошибка: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@dp.message(F.text)
async def handle_text(message: types.Message):
    status_msg = await message.answer("⏳ Анализирую продукты и даты...")
    await process_food_input(message, message.text, status_msg)

async def process_food_input(message: types.Message, text: str, status_msg: types.Message):
    try:
        meals = await parse_food_text(text)
        if not meals:
            await status_msg.edit_text("Не удалось распознать продукты в сообщении. Попробуйте написать точнее.")
            return

        user_pending_meals[message.from_user.id] = meals

        total_items_count = sum(len(m.get("items", [])) for m in meals)
        total_kcal = int(sum(sum(it.get("cal", it.get("calories", 0)) for it in m.get("items", [])) for m in meals))

        lines = []
        for m in meals:
            date_val = m.get("date", "Сегодня")
            meal_type = m.get("meal_type", "other")
            meal_title = MEAL_NAMES.get(meal_type, "🍽 Приём пищи")
            m_items = m.get("items", [])
            m_kcal = int(sum(it.get("cal", it.get("calories", 0)) for it in m_items))

            lines.append(f"📅 **{date_val}** | **{meal_title}** (~{m_kcal} ккал):")
            for it in m_items:
                name = it.get("name") or it.get("original_name")
                grams = it.get("g") or it.get("weight_grams", 0)
                kcal = int(it.get("cal") or it.get("calories", 0))
                p = it.get("p") or it.get("protein", 0)
                f = it.get("f") or it.get("fat", 0)
                c = it.get("c") or it.get("carbs", 0)
                lines.append(f"  • {name} ({grams}г) — {kcal} ккал (Б:{p} Ж:{f} У:{c})")
            lines.append("")

        header = f"📋 **Распознано приёмов пищи: {len(meals)}** (всего {total_items_count} прод., ~{total_kcal} ккал)\n\n"
        full_text = header + "\n".join(lines).strip()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить и записать в FatSecret", callback_data="confirm_save")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="edit_entry")]
        ])

        if len(full_text) > 4000:
            chunks = [full_text[i:i+3800] for i in range(0, len(full_text), 3800)]
            await status_msg.edit_text(chunks[0], parse_mode="Markdown")
            for chunk in chunks[1:-1]:
                await message.answer(chunk, parse_mode="Markdown")
            await message.answer(chunks[-1], parse_mode="Markdown", reply_markup=keyboard)
        else:
            await status_msg.edit_text(full_text, parse_mode="Markdown", reply_markup=keyboard)

    except Exception as e:
        logging.error(f"Ошибка обработки еды: {e}")
        await status_msg.edit_text(f"❌ Ошибка при расчете: {e}")

@dp.callback_query(F.data == "confirm_save")
async def cb_confirm(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    meals = user_pending_meals.get(user_id)
    
    if not meals:
        await callback.message.reply("Нет сохранённых данных для записи. Отправьте новое сообщение.")
        await callback.answer()
        return

    if not fatsecret.is_authorized():
        await callback.message.reply(
            "⚠️ **Бот ещё не привязан к вашему аккаунту FatSecret!**\n"
            "Отправьте команду **/login**, чтобы связать бота с вашим дневником на айфоне.",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    progress_msg = await callback.message.reply("⏳ Записываю все приёмы пищи в FatSecret по датам...")
    saved_count = 0
    errors = []
    dates_saved = set()

    for meal in meals:
        meal_date = meal.get("date")
        meal_type = meal.get("meal_type", "other")
        fs_meal = FS_MEAL_NAMES.get(meal_type, "Lunch")
        items = meal.get("items", [])

        for item in items:
            name_ru = item.get("name") or item.get("original_name")
            query_en = item.get("en") or item.get("search_query_en") or name_ru
            grams = item.get("g") or item.get("weight_grams", 100)

            try:
                foods = fatsecret.search_food(query_en, max_results=1)
                if not foods:
                    foods = fatsecret.search_food("generic food", max_results=1)

                if foods:
                    food_id = foods[0]["food_id"]
                    details = fatsecret.get_food(food_id)
                    servings = details.get("servings", {}).get("serving", [])
                    if isinstance(servings, dict):
                        servings = [servings]

                    srv_id, units = select_best_serving(servings, float(grams))
                    fatsecret.add_food_entry(
                        food_id=food_id,
                        serving_id=srv_id,
                        units=units,
                        entry_name=name_ru,
                        meal=fs_meal,
                        date_str=meal_date
                    )
                    saved_count += 1
                    if meal_date:
                        dates_saved.add(meal_date)
            except Exception as e:
                logging.error(f"Ошибка сохранения {name_ru} на {meal_date}: {e}")
                errors.append(f"{name_ru}: {e}")

    await callback.message.edit_reply_markup(reply_markup=None)
    dates_str = ", ".join(sorted(dates_saved)) if dates_saved else "сегодня"
    if saved_count > 0:
        await progress_msg.edit_text(
            f"🎉 **Успешно записано в FatSecret!**\n\n"
            f"• Продуктов: **{saved_count}**\n"
            f"• Даты: **{dates_str}**\n\n"
            f"Откройте приложение FatSecret на iPhone — всё уже появилось в нужных днях! 📱✨",
            parse_mode="Markdown"
        )
    else:
        await progress_msg.edit_text(f"❌ Не удалось записать: {', '.join(errors)}")

    await callback.answer()

@dp.callback_query(F.data == "edit_entry")
async def cb_edit(callback: types.CallbackQuery):
    await callback.message.reply("Просто отправьте новое сообщение с исправлениями.")
    await callback.answer()

async def main():
    if not bot:
        print("Ошибка: не задан TELEGRAM_BOT_TOKEN!")
        return
    print("Бот запущен и ожидает сообщений...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
