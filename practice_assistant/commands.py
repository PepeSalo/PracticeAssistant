from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, TypedDict

from .config import LEVELS, Settings, language

ACTIONS = (
    "stop",
    "repeat",
    "speed",
    "level",
    "language",
    "learner_language",
    "scenario",
    "correction",
    "end",
)
LANGUAGES = {
    "english": "en",
    "englanti": "en",
    "finnish": "fi",
    "suomi": "fi",
    "spanish": "es",
    "espanja": "es",
    "french": "fr",
    "ranska": "fr",
    "german": "de",
    "saksa": "de",
    "italian": "it",
    "italia": "it",
    "swedish": "sv",
    "ruotsi": "sv",
    "japanese": "ja",
    "japani": "ja",
}


@dataclass(frozen=True)
class Command:
    action: str
    value: str = ""


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s-]", "", text.casefold()).split())


def parse_command(text: str, *, final: bool = True) -> Command | None:
    if not final:
        return None
    text = normalize(text)
    text = re.sub(r"^(?:please|could you|can you|voisitko|kiitos)\s+", "", text)
    text = re.sub(r"\s+(?:please|kiitos)$", "", text)
    fixed = {
        "stop": ("stop", ""),
        "wait": ("stop", ""),
        "hold on": ("stop", ""),
        "be quiet": ("stop", ""),
        "lopeta": ("stop", ""),
        "odota": ("stop", ""),
        "repeat that": ("repeat", ""),
        "say it again": ("repeat", ""),
        "toista": ("repeat", ""),
        "sano uudelleen": ("repeat", ""),
        "speak slower": ("speed", "slower"),
        "slow down": ("speed", "slower"),
        "puhu hitaammin": ("speed", "slower"),
        "speak faster": ("speed", "faster"),
        "speed up": ("speed", "faster"),
        "puhu nopeammin": ("speed", "faster"),
        "make it easier": ("level", "easier"),
        "helpommin": ("level", "easier"),
        "make it harder": ("level", "harder"),
        "vaikeammin": ("level", "harder"),
        "correct my mistakes": ("correction", "on"),
        "korjaa virheeni": ("correction", "on"),
        "dont correct me": ("correction", "off"),
        "älä korjaa minua": ("correction", "off"),
        "goodbye": ("end", ""),
        "end session": ("end", ""),
        "quit": ("end", ""),
        "lopeta harjoitus": ("end", ""),
        "näkemiin": ("end", ""),
    }
    if text in fixed:
        return Command(*fixed[text])
    match = re.fullmatch(r"(?:level|taso) (a[12]|b[12]|c[12])", text)
    if match:
        return Command("level", match[1].upper())
    match = re.fullmatch(
        r"(?:lets practi[cs]e|practi[cs]e|harjoitellaan|practice language) ([\w-]+)",
        text,
    )
    if match:
        value = LANGUAGES.get(match[1], match[1])
        try:
            return Command("language", language(value))
        except ValueError:
            return None
    match = re.fullmatch(
        r"(?:role-play|role play|scenario|skenaario|roolipeli) (.{1,300})", text
    )
    if match:
        return Command("scenario", match[1])
    if text in ("job interview", "työhaastattelu"):
        return Command("scenario", "job interview")
    return None


def extract_json(raw: str) -> dict[str, object]:
    raw = raw.strip()
    if raw.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if not match:
            raise ValueError("Malformed fenced JSON.")
        raw = match[1]
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return payload


def tool_command(raw: str) -> Command:
    data = extract_json(raw)
    if set(data) != {"action", "value"}:
        raise ValueError("Control arguments require exactly action and value.")
    action, value = data["action"], data["value"]
    if (
        not isinstance(action, str)
        or not isinstance(value, str)
        or action not in ACTIONS
    ):
        raise ValueError("Invalid control action or value type.")
    return Command(action, value)


def apply_command(command: Command, settings: Settings) -> str:
    action, value = command.action, command.value
    if action == "language":
        settings.practice_language = language(value)
    elif action == "learner_language":
        settings.learner_language = language(value, allow_auto=True)
    elif action == "level":
        index = LEVELS.index(settings.level)
        level = (
            LEVELS[max(0, index - 1)]
            if value == "easier"
            else (
                LEVELS[min(len(LEVELS) - 1, index + 1)]
                if value == "harder"
                else value.upper()
            )
        )
        if level not in LEVELS:
            raise ValueError("Invalid CEFR level.")
        settings.level = level
    elif action == "speed":
        speed = (
            max(0.5, settings.speed - 0.15)
            if value == "slower"
            else min(1.5, settings.speed + 0.15) if value == "faster" else float(value)
        )
        if not 0.5 <= speed <= 1.5:
            raise ValueError("Speed must be 0.5 to 1.5.")
        settings.speed = speed
    elif action == "scenario":
        if not value.strip() or len(value) > 300:
            raise ValueError("Scenario must be 1 to 300 characters.")
        settings.scenario = value
    elif action == "correction":
        if value not in ("on", "off"):
            raise ValueError("Correction must be on or off.")
        settings.correction = value == "on"
    elif action not in ("stop", "repeat", "end"):
        raise ValueError("Unknown command.")
    return {
        "language": f"Practice language is now {settings.practice_language}.",
        "learner_language": f"Explanation language is now {settings.learner_language}.",
        "level": f"Level is now {settings.level}.",
        "speed": f"Speaking rate is now {settings.speed:.2f}.",
        "scenario": f"Scenario is now {settings.scenario}.",
        "correction": f"Corrections are {'on' if settings.correction else 'off'}.",
        "stop": "Okay, I am listening.",
        "repeat": "Repeating the last answer.",
        "end": "Ending practice.",
    }[action]


class FunctionTool(TypedDict):
    type: Literal["function"]
    name: str
    description: str
    parameters: dict[str, object]


CONTROL_TOOL: FunctionTool = {
    "type": "function",
    "name": "control",
    "description": (
        "Use only for an explicit request to control this session, in any language. "
        "Never use for quoted commands, a story, or practice dialogue. "
        "Translate language names to ISO codes. Value is empty for stop/repeat/end; "
        "speed: slower/faster or numeric string; level: CEFR or easier/harder; "
        "correction: on/off; scenario: brief description."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "value": {"type": "string"},
        },
        "required": ["action", "value"],
        "additionalProperties": False,
    },
}
LEARNING_TOOL: FunctionTool = {
    "type": "function",
    "name": "record_learning",
    "description": (
        "Record vocabulary actually taught or learner mistakes actually corrected, "
        "not suspected transcription errors. May be empty; do not invent observations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vocabulary": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "mistakes": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        },
        "required": ["vocabulary", "mistakes"],
        "additionalProperties": False,
    },
}
TOOLS = [CONTROL_TOOL, LEARNING_TOOL]
