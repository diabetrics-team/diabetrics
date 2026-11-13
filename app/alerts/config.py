"""Project rule settings: mmol/L, minutes, and mmol/L/minute."""

from datetime import timedelta
from types import MappingProxyType

from app.models import AlertKind, Level

LOW = 3.9
LOW_RESET = 4.2
VERY_LOW = 3.0
HIGH = 10.0
HIGH_RESET = 9.5
VERY_HIGH = 13.9
FAST_RATE = 0.17
ARTIFACT_RATE = 0.5
PREDICTED_LOW_CRITICAL_BELOW = 5.0
HIGH_DURATION = timedelta(hours=1)
MAX_READING_GAP = timedelta(minutes=10)
FORECAST_WINDOW = timedelta(minutes=15)
NO_DATA_IMPORTANT = timedelta(minutes=20)
NO_DATA_CRITICAL = timedelta(minutes=30)
ALERT_COOLDOWN = timedelta(minutes=15)
REPEAT_AFTER = timedelta(minutes=5)
TRUSTED_AFTER_MINUTES = 10

LEVELS = MappingProxyType({
    AlertKind.LOW: Level.IMPORTANT,
    AlertKind.VERY_LOW: Level.CRITICAL,
    AlertKind.FAST_DROP: Level.IMPORTANT,
    AlertKind.FAST_RISE: Level.IMPORTANT,
    AlertKind.HIGH_1H: Level.IMPORTANT,
    AlertKind.VERY_HIGH_1H: Level.CRITICAL,
    AlertKind.STILL_LOW: Level.CRITICAL,
})
