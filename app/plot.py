"""
Отрисовка графика сахара для команды /graph.

Владелец: Dev 4. Возвращает картинку в байтах (BytesIO), готовую к отправке в Telegram.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # Важно для работы без графического интерфейса (на сервере)
import matplotlib.dates as md
import matplotlib.pyplot as plt

from app.models import Reading


def plot(readings: list[Reading], hours: int = 6, tz: timezone = timezone.utc) -> BytesIO:
    """
    Рисует график за последние `hours` часов.
    Возвращает BytesIO с PNG-картинкой.
    """
    # 1. Фильтруем данные (берем только то, что попадает в нужный период)
    # Используем время последнего показания как "сейчас"
    if not readings:
        # Если данных нет, рисуем пустой график с текстом
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", fontsize=14)
        ax.axis("off")
    else:
        now = readings[-1].ts
        start_time = now - timedelta(hours=hours)
        window_data = [r for r in readings if r.ts >= start_time]

        # Переводим время в локальный часовой пояс пользователя
        times = [r.ts.astimezone(tz) for r in window_data]
        values = [r.mmol for r in window_data]

        fig, ax = plt.subplots(figsize=(8, 4))

        # 2. Рисуем зоны и пороги
        ax.axhspan(3.9, 10.0, color="#2a9d8f", alpha=0.1)  # Зеленая зона нормы
        ax.axhline(3.0, color="#d62828", lw=1, ls="--")  # Критически низкий
        ax.axhline(3.9, color="#f4a261", lw=1, ls="--")  # Низкий
        ax.axhline(10.0, color="#f4a261", lw=1, ls="--")  # Высокий
        ax.axhline(13.9, color="#d62828", lw=1, ls="--")  # Критически высокий

        # 3. Рисуем сам график
        ax.plot(times, values, color="#264653", marker=".", markersize=4, linestyle="-")

        # 4. Настраиваем оси
        ax.set_ylim(2.0, 16.0)
        ax.set_ylabel("ммоль/л")

        # Форматируем время по оси X
        ax.xaxis.set_major_formatter(md.DateFormatter("%H:%M", tz=tz))
        fig.autofmt_xdate()

    # 5. Сохраняем в память (BytesIO)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)  # Обязательно закрываем фигуру, чтобы не текла память
    buf.seek(0)

    return buf