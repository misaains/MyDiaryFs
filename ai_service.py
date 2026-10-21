import json
import os
import re
import datetime
import logging
from groq import Groq
from config import GROQ_API_KEY

def get_groq_client():
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY не задан в файле .env")
    return Groq(api_key=GROQ_API_KEY)

async def transcribe_audio_file(file_path: str) -> str:
    """Распознает аудиофайл (включая .ogg из Telegram) через Whisper в Groq."""
    client = get_groq_client()
    with open(file_path, "rb") as file:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(file_path), file.read()),
            model="whisper-large-v3",
            language="ru",
            response_format="text"
        )
    return transcription.strip()

def split_into_meal_blocks(text: str) -> list[str]:
    """
    Разбивает длинный текст с несколькими датами или приемами пищи на отдельные блоки.
    Например: "18.10 на обед ... 18.10 на ужин ... вчера на завтрак ..."
    """
    pattern = r'(?m)^(?=(?:(?:\d{1,2}[./]\d{1,2}(?:\.\d{2,4})?|позавчера|вчера|сегодня)\s*)?(?:на\s+)?(?:завтрак|обед|ужин|перекус)\b)'
    raw_blocks = [b.strip() for b in re.split(pattern, text, flags=re.I) if b.strip()]
    return raw_blocks if raw_blocks else [text.strip()]

async def parse_single_meal_block(block: str, default_date_str: str) -> dict:
    """
    Разбирает один прием пищи с распознаванием даты (относительной: вчера/позавчера или точной: 18.10).
    """
    client = get_groq_client()
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    day_before_str = (today - datetime.timedelta(days=2)).strftime("%Y-%m-%d")

    prompt = f"""Ты — персональный диетолог и помощник по учету калорий.
Сегодняшняя дата: {today_str} (год {today.year}).
Дата по умолчанию (если в тексте нет другой): {default_date_str}.

Текст одного приема пищи:
"{block}"

Правила:
1. date: точная дата "YYYY-MM-DD".
   - "позавчера" -> {day_before_str}
   - "вчера" -> {yesterday_str}
   - "сегодня" -> {today_str}
   - явная дата (например, "18.10", "19.10") -> {today.year}-MM-DD
   - если дата не указана, используй дату по умолчанию: {default_date_str}
2. meal_type: "breakfast" (завтрак), "lunch" (обед), "dinner" (ужин), "other" (перекус).
3. items: список продуктов.
   - name: название на русском
   - en: краткое название на английском для базы FatSecret (например, 'Lean Beef', 'Mashed Potatoes', 'Pomegranate Salad', 'Greek Yogurt', 'Whole Wheat Pasta')
   - g: вес в граммах (оцени штуки и миллилитры, например 3 шт печенья ~ 60г, 200мл вина ~ 200г)
   - cal: калории
   - p: белки (г)
   - f: жиры (г)
   - c: углеводы (г)

Формат СТРОГО следующий JSON:
{{
  "date": "YYYY-MM-DD",
  "meal_type": "lunch",
  "items": [
    {{"name": "...", "en": "...", "g": 100, "cal": 150, "p": 10.0, "f": 5.0, "c": 15.0}}
  ]
}}
"""
    response = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
        messages=[
            {"role": "system", "content": "You are a nutrition assistant. Respond only with valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_tokens=450,
        temperature=0.1
    )
    return json.loads(response.choices[0].message.content)

async def parse_food_text(text: str) -> list[dict]:
    """
    Разбирает текст (любого размера: один прием или много дней/приемов) в список приемов пищи.
    """
    blocks = split_into_meal_blocks(text)
    curr_date = datetime.date.today().strftime("%Y-%m-%d")
    meals = []
    
    for block in blocks:
        try:
            parsed = await parse_single_meal_block(block, curr_date)
            if parsed and parsed.get("items"):
                curr_date = parsed.get("date", curr_date)
                meals.append(parsed)
        except Exception as e:
            logging.error(f"Ошибка парсинга блока '{block[:30]}': {e}")
            
    return meals
