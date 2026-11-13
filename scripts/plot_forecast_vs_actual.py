import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as md
from datetime import datetime, timezone
from pathlib import Path
import csv
from app.forecast import predict
from app.models import Reading

CSV_PATH = Path(
    r"C:\Users\user\OneDrive\Рабочий стол\ВУЗ\Хакатоны\DiaBetrics\diabetes_subset_pictures-glucose-food-insulin\001\glucose.csv")


def main():
    readings = []
    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("type") != "cgm":
                continue
            dt = datetime.strptime(f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            readings.append(Reading(ts=dt, mmol=float(row["glucose"])))

    # Берем кусок данных, например, 100 точек (около 8 часов)
    subset = readings[:150]

    times = [r.ts for r in subset]
    actual = [r.mmol for r in subset]

    # Делаем прогноз для каждой точки (на 20 минут вперед)
    predicted = []
    for i in range(len(subset)):
        f = predict(subset[:i + 1])
        if f:
            predicted.append(f.mmol_20)
        else:
            predicted.append(None)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.axhspan(3.9, 10.0, color="#2a9d8f", alpha=0.1)
    ax.plot(times, actual, label="Реальный сахар", color="#264653", linewidth=2)
    ax.plot(times, predicted, label="Прогноз (+20 мин)", color="#e76f51", linestyle="--", linewidth=2)

    ax.set_ylabel("ммоль/л")
    ax.set_ylim(2, 16)
    ax.legend()
    ax.xaxis.set_major_formatter(md.DateFormatter("%H:%M", tz=timezone.utc))
    fig.autofmt_xdate()

    out_path = "forecast_vs_actual.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"График сохранен в {out_path}")


if __name__ == "__main__":
    main()