"""
Варианты в /settings. Отдельный модуль без aiogram — его проверяют и тесты в окружении Dev 2.

Владелец: Dev 3.
Все варианты эскалации больше повтора (app/alerts/config.py: REPEAT_AFTER = 5 мин) — иначе
escalation_step Dev 2 бросит ValueError. Это проверяет tests/test_bot_night.py.
"""
ESCALATION_OPTIONS = (10, 15, 20, 30)
NIGHT_START_OPTIONS = ("21:00", "22:00", "23:00", "00:00")
NIGHT_END_OPTIONS = ("06:00", "07:00", "08:00", "09:00")
