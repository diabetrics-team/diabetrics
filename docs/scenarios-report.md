# Прогон сценариев fake_cgm через pipeline

Настоящие clean → predict → evaluate → антиспам → эскалация; подменены только Nightscout и Telegram. БД: SQLite в памяти (app/db.py). Доверенное лицо подключено, «Я в порядке» никто не нажимает. Время — МСК.

Сгенерировано: `python scripts/run_scenarios.py --md docs/scenarios-report.md`.

| Сценарий · режим | Итог | Уведомления (новые) | Доверенному |
|---|---|---|---|
| normal · normal | ✅ | 15:20 important/no_data, 15:35 important/no_data, 15:50 important/no_data | — |
| night_hypo · normal | ✅ | 01:45 critical/predicted_low, 01:50 important/low, 02:20 critical/predicted_low, 02:50 critical/predicted_low, 03:00 critical/very_low, 03:05 critical/predicted_low … | 01:55 |
| night_hypo · night | ✅ | 01:45 critical/predicted_low, 01:50 critical/low, 02:20 critical/predicted_low, 02:50 critical/predicted_low, 03:00 critical/very_low, 03:05 critical/predicted_low … | 01:55 |
| night_hypo · normal + ночь по расписанию 23:00–07:00 | ✅ | 01:45 critical/predicted_low, 01:50 critical/low, 02:20 critical/predicted_low, 02:50 critical/predicted_low, 03:00 critical/very_low, 03:05 critical/predicted_low … | 01:55 |
| meal · normal | ✅ | 08:30 important/fast_rise, 09:40 important/high_1h, 10:25 info/back_in_range, 12:20 important/no_data, 12:35 important/no_data, 12:50 important/no_data | — |
| meal · after_meal | ✅ | 12:20 important/no_data, 12:35 important/no_data, 12:50 important/no_data | — |
| fast_drop · normal | ✅ | 16:45 important/fast_drop, 16:45 critical/predicted_low, 16:55 important/low, 17:00 critical/predicted_low, 17:15 info/back_in_range, 18:20 important/no_data … | 16:55 |
| hypo_corrected · after_hypo | ✅ | 12:20 critical/predicted_low, 12:35 important/low, 12:35 critical/predicted_low, 13:00 info/back_in_range, 14:20 important/no_data, 14:35 important/no_data … | 12:30 |
| hypo_not_corrected · after_hypo | ✅ | 12:20 critical/predicted_low, 12:35 important/low, 12:35 critical/predicted_low, 12:55 critical/still_low, 13:55 info/back_in_range, 14:20 important/no_data … | 12:30 |
| hyper_long · normal | ✅ | 15:30 important/high_1h, 16:20 critical/very_high_1h, 17:55 info/back_in_range, 18:30 important/no_data, 18:45 important/no_data, 19:00 important/no_data | 16:30 |
| sensor_gap · normal | ✅ | 18:25 critical/predicted_low, 18:45 important/low, 18:45 critical/predicted_low, 19:00 critical/predicted_low, 19:10 important/no_data, 19:20 critical/no_data … | 18:35 |
| noise_spike · normal | ✅ | 13:50 important/no_data, 14:05 important/no_data, 14:20 important/no_data | — |
| borderline · normal | ✅ | 15:25 critical/predicted_low, 15:35 important/low, 16:30 info/back_in_range, 17:20 important/no_data, 17:35 important/no_data, 17:50 important/no_data | 15:35 |
| meal_overbolus · normal | ✅ | 13:40 important/fast_rise, 14:40 important/high_1h, 14:50 info/back_in_range, 15:20 important/predicted_low, 15:25 critical/predicted_low, 15:40 important/low … | 15:35 |
| meal_overbolus · after_meal | ✅ | 15:20 important/predicted_low, 15:25 critical/predicted_low, 15:40 important/low, 15:40 critical/predicted_low, 16:20 info/back_in_range, 17:30 important/no_data … | 15:35 |
| sensor_lost · normal | ✅ | 21:20 important/no_data, 21:35 important/no_data, 21:50 important/no_data | — |
| dirty_data · normal | ✅ | 14:20 important/no_data, 14:35 important/no_data, 14:50 important/no_data | — |
| random_day · normal | ✅ | 13:30 important/fast_rise, 14:35 important/high_1h, 15:25 info/back_in_range, 00:20 important/no_data, 00:35 important/no_data, 00:50 important/no_data | — |

Итого: 18 из 18 случаев прошли.

## Подробно

### normal · normal ✅

Ожидание: ни одного критического и важного

- ✅ нет critical/important

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 15:20 | important | no_data | 6.7 | пользователю |
| 15:35 | important | no_data | 6.7 | пользователю |
| 15:50 | important | no_data | 6.7 | пользователю |

### night_hypo · normal ✅

Ожидание: важное low при first_below_3_9 (в Ночном режиме — сразу критическое); критическое very_low не позже first_below_3_0; предиктивное — раньше; нет «Я в порядке» -> повтор -> доверенному лицу

- ✅ important low в 01:50
- ✅ critical very_low в 03:00
- ✅ critical predicted_low в 01:45
- ✅ доверенному лицу — в 01:55

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 01:45 | critical | predicted_low | 3.0 | пользователю |
| 01:50 | important | low | 3.9 | пользователю |
| 01:50 | critical | predicted_low | 3.0 | пользователю |
| 01:55 | critical | predicted_low | 3.0 | доверенному |
| 02:20 | critical | predicted_low | 3.0 | пользователю |
| 02:25 | critical | predicted_low | 3.0 | пользователю |
| 02:30 | critical | predicted_low | 3.0 | доверенному |
| 02:50 | critical | predicted_low | 2.8 | пользователю |
| 02:55 | critical | predicted_low | 2.8 | пользователю |
| 03:00 | critical | very_low | 2.9 | пользователю |
| 03:00 | critical | predicted_low | 2.8 | доверенному |
| 03:05 | critical | predicted_low | 2.4 | пользователю |
| 03:05 | critical | very_low | 2.9 | пользователю |
| 03:10 | critical | very_low | 2.9 | доверенному |
| 03:10 | critical | predicted_low | 2.4 | пользователю |
| 03:15 | critical | predicted_low | 2.4 | доверенному |
| 03:20 | critical | predicted_low | 2.6 | пользователю |
| 03:25 | critical | predicted_low | 2.6 | пользователю |
| 03:30 | critical | predicted_low | 2.6 | доверенному |
| 03:35 | critical | predicted_low | 2.4 | пользователю |
| 03:40 | critical | predicted_low | 2.4 | пользователю |
| 03:45 | critical | predicted_low | 2.4 | доверенному |
| 04:20 | info | back_in_range | 4.3 | пользователю |
| 07:20 | important | no_data | 6.5 | пользователю |
| 07:35 | important | no_data | 6.5 | пользователю |
| 07:50 | important | no_data | 6.5 | пользователю |

### night_hypo · night ✅

Ожидание: важное low при first_below_3_9 (в Ночном режиме — сразу критическое); критическое very_low не позже first_below_3_0; предиктивное — раньше; нет «Я в порядке» -> повтор -> доверенному лицу

- ✅ critical low в 01:50
- ✅ нет important low
- ✅ доверенному лицу — в 01:55

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 01:45 | critical | predicted_low | 3.0 | пользователю |
| 01:50 | critical | low | 3.9 | пользователю |
| 01:50 | critical | predicted_low | 3.0 | пользователю |
| 01:55 | critical | predicted_low | 3.0 | доверенному |
| 01:55 | critical | low | 3.9 | пользователю |
| 02:00 | critical | low | 3.9 | доверенному |
| 02:20 | critical | predicted_low | 3.0 | пользователю |
| 02:25 | critical | predicted_low | 3.0 | пользователю |
| 02:30 | critical | predicted_low | 3.0 | доверенному |
| 02:50 | critical | predicted_low | 2.8 | пользователю |
| 02:55 | critical | predicted_low | 2.8 | пользователю |
| 03:00 | critical | very_low | 2.9 | пользователю |
| 03:00 | critical | predicted_low | 2.8 | доверенному |
| 03:05 | critical | predicted_low | 2.4 | пользователю |
| 03:05 | critical | very_low | 2.9 | пользователю |
| 03:10 | critical | very_low | 2.9 | доверенному |
| 03:10 | critical | predicted_low | 2.4 | пользователю |
| 03:15 | critical | predicted_low | 2.4 | доверенному |
| 03:20 | critical | predicted_low | 2.6 | пользователю |
| 03:25 | critical | predicted_low | 2.6 | пользователю |
| 03:30 | critical | predicted_low | 2.6 | доверенному |
| 03:35 | critical | predicted_low | 2.4 | пользователю |
| 03:40 | critical | predicted_low | 2.4 | пользователю |
| 03:45 | critical | predicted_low | 2.4 | доверенному |
| 04:20 | info | back_in_range | 4.3 | пользователю |
| 07:20 | important | no_data | 6.5 | пользователю |
| 07:35 | important | no_data | 6.5 | пользователю |
| 07:50 | important | no_data | 6.5 | пользователю |

### night_hypo · normal + ночь по расписанию 23:00–07:00 ✅

Ожидание: важное low при first_below_3_9 (в Ночном режиме — сразу критическое); критическое very_low не позже first_below_3_0; предиктивное — раньше; нет «Я в порядке» -> повтор -> доверенному лицу

- ✅ critical low в 01:50
- ✅ нет important low

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 01:45 | critical | predicted_low | 3.0 | пользователю |
| 01:50 | critical | low | 3.9 | пользователю |
| 01:50 | critical | predicted_low | 3.0 | пользователю |
| 01:55 | critical | predicted_low | 3.0 | доверенному |
| 01:55 | critical | low | 3.9 | пользователю |
| 02:00 | critical | low | 3.9 | доверенному |
| 02:20 | critical | predicted_low | 3.0 | пользователю |
| 02:25 | critical | predicted_low | 3.0 | пользователю |
| 02:30 | critical | predicted_low | 3.0 | доверенному |
| 02:50 | critical | predicted_low | 2.8 | пользователю |
| 02:55 | critical | predicted_low | 2.8 | пользователю |
| 03:00 | critical | very_low | 2.9 | пользователю |
| 03:00 | critical | predicted_low | 2.8 | доверенному |
| 03:05 | critical | predicted_low | 2.4 | пользователю |
| 03:05 | critical | very_low | 2.9 | пользователю |
| 03:10 | critical | very_low | 2.9 | доверенному |
| 03:10 | critical | predicted_low | 2.4 | пользователю |
| 03:15 | critical | predicted_low | 2.4 | доверенному |
| 03:20 | critical | predicted_low | 2.6 | пользователю |
| 03:25 | critical | predicted_low | 2.6 | пользователю |
| 03:30 | critical | predicted_low | 2.6 | доверенному |
| 03:35 | critical | predicted_low | 2.4 | пользователю |
| 03:40 | critical | predicted_low | 2.4 | пользователю |
| 03:45 | critical | predicted_low | 2.4 | доверенному |
| 04:20 | info | back_in_range | 4.3 | пользователю |
| 07:20 | important | no_data | 6.5 | пользователю |
| 07:35 | important | no_data | 6.5 | пользователю |
| 07:50 | important | no_data | 6.5 | пользователю |

### meal · normal ✅

Ожидание: Обычный режим: важное fast_rise, затем важное high > 1 ч; «После еды»: ни одного уведомления (порог 13.9 не пересечён)

- ✅ important fast_rise в 08:30
- ✅ important high_1h в 09:40

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 08:30 | important | fast_rise | 9.4 | пользователю |
| 09:40 | important | high_1h | 11.8 | пользователю |
| 10:25 | info | back_in_range | 9.2 | пользователю |
| 12:20 | important | no_data | 6.2 | пользователю |
| 12:35 | important | no_data | 6.2 | пользователю |
| 12:50 | important | no_data | 6.2 | пользователю |

### meal · after_meal ✅

Ожидание: Обычный режим: важное fast_rise, затем важное high > 1 ч; «После еды»: ни одного уведомления (порог 13.9 не пересечён)

- ✅ нет critical/important

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 12:20 | important | no_data | 6.2 | пользователю |
| 12:35 | important | no_data | 6.2 | пользователю |
| 12:50 | important | no_data | 6.2 | пользователю |

### fast_drop · normal ✅

Ожидание: важное fast_drop; предиктивное: важное при текущем >= 5.0, критическое при < 5.0; важное low; very_low нет (до 3.0 не доходит)

- ✅ important fast_drop в 16:45
- ✅ important low в 16:55
- ✅ нет very_low

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 16:45 | important | fast_drop | 4.8 | пользователю |
| 16:45 | critical | predicted_low | 0.8 | пользователю |
| 16:50 | critical | predicted_low | 0.8 | пользователю |
| 16:55 | important | low | 3.9 | пользователю |
| 16:55 | critical | predicted_low | 0.8 | доверенному |
| 17:00 | critical | predicted_low | 2.3 | пользователю |
| 17:05 | critical | predicted_low | 2.3 | пользователю |
| 17:10 | critical | predicted_low | 2.3 | доверенному |
| 17:15 | info | back_in_range | 4.4 | пользователю |
| 18:20 | important | no_data | 5.9 | пользователю |
| 18:35 | important | no_data | 5.9 | пользователю |
| 18:50 | important | no_data | 5.9 | пользователю |

### hypo_corrected · after_hypo ✅

Ожидание: важное low один раз; в «После коррекции гипо»: повторов нет, перепроверка через 15 мин -> тишина

- ✅ low ровно 1 раз — пришло 1: 12:35 important/low
- ✅ нет still_low

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 12:20 | critical | predicted_low | 2.8 | пользователю |
| 12:25 | critical | predicted_low | 2.8 | пользователю |
| 12:30 | critical | predicted_low | 2.8 | доверенному |
| 12:35 | important | low | 3.5 | пользователю |
| 12:35 | critical | predicted_low | 2.4 | пользователю |
| 12:40 | critical | predicted_low | 2.4 | пользователю |
| 12:45 | critical | predicted_low | 2.4 | доверенному |
| 13:00 | info | back_in_range | 4.9 | пользователю |
| 14:20 | important | no_data | 6.3 | пользователю |
| 14:35 | important | no_data | 6.3 | пользователю |
| 14:50 | important | no_data | 6.3 | пользователю |

### hypo_not_corrected · after_hypo ✅

Ожидание: важное low; в «После коррекции гипо»: через 15 мин всё ещё < 3.9 -> критическое

- ✅ important low в 12:35
- ✅ critical still_low в 12:55

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 12:20 | critical | predicted_low | 2.8 | пользователю |
| 12:25 | critical | predicted_low | 2.8 | пользователю |
| 12:30 | critical | predicted_low | 2.8 | доверенному |
| 12:35 | important | low | 3.6 | пользователю |
| 12:35 | critical | predicted_low | 2.5 | пользователю |
| 12:40 | critical | predicted_low | 2.5 | пользователю |
| 12:45 | critical | predicted_low | 2.5 | доверенному |
| 12:55 | critical | still_low | 3.6 | пользователю |
| 13:00 | critical | still_low | 3.6 | пользователю |
| 13:05 | critical | still_low | 3.6 | доверенному |
| 13:55 | info | back_in_range | 4.4 | пользователю |
| 14:20 | important | no_data | 4.6 | пользователю |
| 14:35 | important | no_data | 4.6 | пользователю |
| 14:50 | important | no_data | 4.6 | пользователю |

### hyper_long · normal ✅

Ожидание: важное high > 1 ч; критическое very_high > 1 ч; fast_rise НЕТ (рост медленнее порога)

- ✅ important high_1h в 15:30
- ✅ critical very_high_1h в 16:20
- ✅ нет fast_rise

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 15:30 | important | high_1h | 14.4 | пользователю |
| 16:20 | critical | very_high_1h | 15.1 | пользователю |
| 16:25 | critical | very_high_1h | 15.1 | пользователю |
| 16:30 | critical | very_high_1h | 15.1 | доверенному |
| 17:55 | info | back_in_range | 9.2 | пользователю |
| 18:30 | important | no_data | 8.4 | пользователю |
| 18:45 | important | no_data | 8.4 | пользователю |
| 19:00 | important | no_data | 8.4 | пользователю |

### sensor_gap · normal ✅

Ожидание: важное no_data через 20 мин; критическое no_data через 30 мин (последнее значение низкое); после возврата данных: тренд неизвестен, прогноза нет

- ✅ important no_data в 19:10
- ✅ critical no_data в 19:20

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 18:25 | critical | predicted_low | 2.9 | пользователю |
| 18:30 | critical | predicted_low | 2.9 | пользователю |
| 18:35 | critical | predicted_low | 2.9 | доверенному |
| 18:45 | important | low | 3.8 | пользователю |
| 18:45 | critical | predicted_low | 2.6 | пользователю |
| 18:50 | critical | predicted_low | 2.6 | пользователю |
| 18:55 | critical | predicted_low | 2.6 | доверенному |
| 19:00 | critical | predicted_low | 2.6 | пользователю |
| 19:05 | critical | predicted_low | 2.6 | пользователю |
| 19:10 | important | no_data | 3.7 | пользователю |
| 19:10 | critical | predicted_low | 2.6 | доверенному |
| 19:20 | critical | no_data | 3.7 | пользователю |
| 19:25 | critical | no_data | 3.7 | пользователю |
| 19:30 | critical | no_data | 3.7 | доверенному |
| 19:55 | info | back_in_range | 4.2 | пользователю |
| 20:50 | important | no_data | 5.4 | пользователю |
| 21:05 | important | no_data | 5.4 | пользователю |
| 21:20 | important | no_data | 5.4 | пользователю |

### noise_spike · normal ✅

Ожидание: НИ ОДНОГО критического, эскалации нет; выброс = скачок > 0.5 ммоль/л/мин: ждать следующего показания

- ✅ нет critical
- ✅ доверенному не писали

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 13:50 | important | no_data | 6.8 | пользователю |
| 14:05 | important | no_data | 6.8 | пользователю |
| 14:20 | important | no_data | 6.8 | пользователю |

### borderline · normal ✅

Ожидание: важное low РОВНО один раз + повторы по антиспаму, а не на каждое пересечение; гистерезис: «вернулся в норму» только выше 4.2

- ✅ important low в 15:35

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 15:25 | critical | predicted_low | 2.6 | пользователю |
| 15:30 | critical | predicted_low | 2.6 | пользователю |
| 15:35 | important | low | 3.8 | пользователю |
| 15:35 | critical | predicted_low | 2.6 | доверенному |
| 16:30 | info | back_in_range | 4.3 | пользователю |
| 17:20 | important | no_data | 5.2 | пользователю |
| 17:35 | important | no_data | 5.2 | пользователю |
| 17:50 | important | no_data | 5.2 | пользователю |

### meal_overbolus · normal ✅

Ожидание: «После еды» НЕ глушит низкие: важное low приходит; Обычный режим: fast_rise и high > 1 ч; в «После еды» оба подавлены; fast_drop нет

- ✅ important fast_rise в 13:40
- ✅ important high_1h в 14:40
- ✅ important low в 15:40
- ✅ нет fast_drop

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 13:40 | important | fast_rise | 10.2 | пользователю |
| 14:40 | important | high_1h | 10.3 | пользователю |
| 14:50 | info | back_in_range | 9.4 | пользователю |
| 15:20 | important | predicted_low | 3.0 | пользователю |
| 15:25 | critical | predicted_low | 2.2 | пользователю |
| 15:30 | critical | predicted_low | 2.2 | пользователю |
| 15:35 | critical | predicted_low | 2.2 | доверенному |
| 15:40 | important | low | 3.4 | пользователю |
| 15:40 | critical | predicted_low | 1.4 | пользователю |
| 15:45 | critical | predicted_low | 1.4 | пользователю |
| 15:50 | critical | predicted_low | 1.4 | доверенному |
| 16:20 | info | back_in_range | 4.4 | пользователю |
| 17:30 | important | no_data | 5.9 | пользователю |
| 17:45 | important | no_data | 5.9 | пользователю |
| 18:00 | important | no_data | 5.9 | пользователю |

### meal_overbolus · after_meal ✅

Ожидание: «После еды» НЕ глушит низкие: важное low приходит; Обычный режим: fast_rise и high > 1 ч; в «После еды» оба подавлены; fast_drop нет

- ✅ important low в 15:40
- ✅ нет fast_rise
- ✅ нет high_1h

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 15:20 | important | predicted_low | 3.0 | пользователю |
| 15:25 | critical | predicted_low | 2.2 | пользователю |
| 15:30 | critical | predicted_low | 2.2 | пользователю |
| 15:35 | critical | predicted_low | 2.2 | доверенному |
| 15:40 | important | low | 3.4 | пользователю |
| 15:40 | critical | predicted_low | 1.4 | пользователю |
| 15:45 | critical | predicted_low | 1.4 | пользователю |
| 15:50 | critical | predicted_low | 1.4 | доверенному |
| 16:20 | info | back_in_range | 4.4 | пользователю |
| 17:30 | important | no_data | 5.9 | пользователю |
| 17:45 | important | no_data | 5.9 | пользователю |
| 18:00 | important | no_data | 5.9 | пользователю |

### sensor_lost · normal ✅

Ожидание: важное no_data через 20 мин, дальше повторы по антиспаму; критического НЕТ (последнее значение в норме)

- ✅ important no_data в 21:20
- ✅ нет critical no_data
- ✅ нет critical

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 21:20 | important | no_data | 6.9 | пользователю |
| 21:35 | important | no_data | 6.9 | пользователю |
| 21:50 | important | no_data | 6.9 | пользователю |

### dirty_data · normal ✅

Ожидание: после очистки ровно facts.points показаний, ни одного уведомления; клиент: только type=sgv, sgv >= 40, дедупликация по времени

- ✅ показаний после очистки 36 (эталон 36)
- ✅ нет critical/important

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 14:20 | important | no_data | 6.3 | пользователю |
| 14:35 | important | no_data | 6.3 | пользователю |
| 14:50 | important | no_data | 6.3 | пользователю |

### random_day · normal ✅

Ожидание: для графиков, метрик TIR и оценки прогноза

- ✅ нет critical

| Время | Уровень | Вид | Значение | Кому |
|---|---|---|---|---|
| 13:30 | important | fast_rise | 9.4 | пользователю |
| 14:35 | important | high_1h | 11.1 | пользователю |
| 15:25 | info | back_in_range | 9.1 | пользователю |
| 00:20 | important | no_data | 6.8 | пользователю |
| 00:35 | important | no_data | 6.8 | пользователю |
| 00:50 | important | no_data | 6.8 | пользователю |
