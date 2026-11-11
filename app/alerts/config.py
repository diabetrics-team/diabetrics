"""Project rule settings: mmol/L, minutes, and mmol/L/minute.

These defaults implement the supplied TODO; they are not medical guidance.
Levels not specified by the supplied TODO are provisional pending team review.
"""

from datetime import timedelta
from types import MappingProxyType

from .contracts import AlertLevel

LOW = 3.9
VERY_LOW = 3.0
HIGH = 10.0
VERY_HIGH = 13.9
FAST_RATE = 0.17
HIGH_DURATION = timedelta(hours=1)
MAX_READING_GAP = timedelta(minutes=10)
NO_DATA_IMPORTANT = timedelta(minutes=20)
NO_DATA_CRITICAL = timedelta(minutes=30)

LEVELS = MappingProxyType({
    "low": AlertLevel.IMPORTANT,
    "very_low": AlertLevel.CRITICAL,
    "fast_drop": AlertLevel.IMPORTANT,
    "fast_rise": AlertLevel.STANDARD,
    "high_1h": AlertLevel.IMPORTANT,
    "very_high_1h": AlertLevel.CRITICAL,
})
