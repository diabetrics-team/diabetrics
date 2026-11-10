"""
Распознавание еды по фото (NutriLens).

Владелец: Dev 4. ЗАГЛУШКА: возвращает фиксированный ответ для демо.
Заменить на: реальный запрос к Vision API (GPT-4o, Yandex Vision или др.).
"""
from __future__ import annotations

import random


async def analyze_photo(image_bytes: bytes) -> dict | None:
    """
    Принимает картинку в байтах, возвращает словарь с КБЖУ.
    Если картинку невозможно распознать — возвращает None.

    В реальной версии здесь будет HTTP-запрос к Vision API.
    """
    # Простейшая имитация задержки сети (чтобы бот показал "печатает...")
    # await asyncio.sleep(1.5)

    # Заглушка: рандомно выбираем блюдо, чтобы на демо было живее
    # (если придет пустой файл, вернем None)
    if not image_bytes or len(image_bytes) < 100:
        return None

    dishes = [
        {"name": "Паста карбонара", "kcal": 650, "protein": 25, "fat": 30, "carbs": 70},
        {"name": "Гречка с курицей", "kcal": 450, "protein": 35, "fat": 12, "carbs": 55},
        {"name": "Салат Цезарь", "kcal": 300, "protein": 15, "fat": 20, "carbs": 15},
        {"name": "Бургер и картошка фри", "kcal": 900, "protein": 30, "fat": 50, "carbs": 85},
    ]

    # Выбираем случайное блюдо для имитации
    return random.choice(dishes)