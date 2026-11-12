"""
Тест прогноза на реальных данных.
Ожидает, что данные D1NAMO лежат локально, иначе тест пропускается.
"""
import pytest
from datetime import datetime, timezone
from pathlib import Path
import csv

from app.forecast import predict
from app.models import Reading

# Путь к твоему реальному файлу.
# ВАЖНО: у остальных dev'ов файла нет, поэтому тест должен корректно пропускаться (skip).
CSV_PATH = Path(
    r"C:\Users\user\OneDrive\Рабочий стол\ВУЗ\Хакатоны\DiaBetrics\diabetes_subset_sensor_data\001\glucose.csv")


def load_readings():
    if not CSV_PATH.exists():
        return None
    readings = []
    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("type") != "cgm":
                continue
            dt = datetime.strptime(f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            readings.append(Reading(ts=dt, mmol=float(row["glucose"])))
    return readings


def test_real_data_mae():
    """Проверяем, что модель реально предсказывает лучше, чем 'оставить как есть'."""
    readings = load_readings()
    if not readings:
        pytest.skip("Реальные данные D1NAMO не найдены локально")

    errors, naive_errors = [], []
    for i in range(len(readings) - 4):  # 4 точки = 20 минут
        history = readings[:i + 1]
        actual_future = readings[i + 4]

        forecast = predict(history)
        if forecast is None:
            continue

        errors.append(abs(forecast.mmol_20 - actual_future.mmol))
        naive_errors.append(abs(history[-1].mmol - actual_future.mmol))

    mae = sum(errors) / len(errors)
    naive_mae = sum(naive_errors) / len(naive_errors)

    # Главное утверждение: наша модель умнее наивного прогноза
    assert mae < naive_mae, f"Модель хуже наивного прогноза ({mae:.3f} vs {naive_mae:.3f})"