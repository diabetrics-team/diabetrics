"""
Тексты бота покрывают все типы из app/models.py, а шаблоны уведомлений
собираются только из alert.kind и alert.value.

Владелец: Dev 3.
"""
from datetime import datetime, timedelta
from html.parser import HTMLParser
from string import Formatter
from zoneinfo import ZoneInfo

import pytest

from app.bot import texts
from app.models import AlertKind, Level, Mode


class _AnyValue(dict):
    """Для .format_map: любая подстановка заменяется на «1»."""

    def __missing__(self, key):
        return "1"


class _TagChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, f"лишний или не тот </{tag}>"


def assert_html_ok(text: str) -> None:
    checker = _TagChecker()
    checker.feed(text)
    assert not checker.stack, f"не закрыты теги: {checker.stack}"


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def test_every_level_has_emoji():
    assert set(texts.LEVEL_EMOJI) == set(Level)


def test_every_mode_has_title_and_description():
    assert set(texts.MODES) == set(Mode)
    for mode, info in texts.MODES.items():
        assert info["title"] and info["description"], mode


@pytest.mark.parametrize("kind", list(AlertKind))
def test_alert_template_exists_and_uses_only_value(kind):
    template = texts.ALERTS[kind]
    assert placeholders(template) <= {"value"}, "в Alert для текста есть только kind и value"
    assert_html_ok(template.format(value="3.4"))


STRINGS = {k: v for k, v in vars(texts).items() if k.isupper() and isinstance(v, str)}


@pytest.mark.parametrize("name", list(STRINGS))
def test_text_formats_and_has_valid_html(name):
    assert_html_ok(STRINGS[name].format_map(_AnyValue()))


def test_commands_menu_is_valid_for_telegram():
    for command, description in texts.COMMANDS.items():
        assert command.islower() and len(command) <= 32
        assert 1 <= len(description) <= 256


@pytest.mark.parametrize("name", list(texts.TIMEZONES))
def test_timezone_label_matches_real_offset(name):
    offset = datetime(2026, 11, 11, tzinfo=ZoneInfo(name)).utcoffset()
    hours = int(offset / timedelta(hours=1))
    assert f"(UTC+{hours})" in texts.TIMEZONES[name]
