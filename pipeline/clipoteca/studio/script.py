"""Strict, offline Clipoteca v3 video script parsing and duration estimates."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypedDict

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SEC_PER_CHAR = 0.065
MAX_SCRIPT_BYTES = 65_536
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "video.schema.json"

Aspect = Literal["9:16", "16:9", "1:1"]
SceneType = Literal["intro", "fact", "outro"]
Mood = Literal["upbeat", "chill", "epic"]
VoiceName = Literal["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]


class ScriptError(ValueError):
    """Invalid script; messages refer to fields, never supplied script text."""


@dataclass(frozen=True, slots=True)
class MusicSpec:
    mood: Mood
    bpm: int


@dataclass(frozen=True, slots=True)
class VoiceSpec:
    voice: VoiceName
    speed: float
    lexicon: dict[str, str]


@dataclass(frozen=True, slots=True)
class SceneSpec:
    id: str
    type: SceneType
    title: str
    sub: str
    say: str
    caption: str
    query: dict[str, list[str]]
    prompt: str
    avoid: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VideoScript:
    title: str
    aspect: Aspect
    language: Literal["es"]
    music: MusicSpec
    voice: VoiceSpec
    scenes: tuple[SceneSpec, ...]


class DurationEstimate(TypedDict):
    scenes: dict[str, float]
    total: float


def _path(parts: list[object]) -> str:
    result = "script"
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}"
    return result


def _schema_error(exc: ValidationError) -> ScriptError:
    path = list(exc.absolute_path)
    if exc.validator == "required" and isinstance(exc.instance, dict):
        missing = next((k for k in exc.validator_value if k not in exc.instance), None)
        if missing:
            path.append(missing)
    elif exc.validator == "additionalProperties" and isinstance(exc.instance, dict):
        allowed = set(exc.schema.get("properties", {}))
        allowed.update(exc.schema.get("patternProperties", {}))
        unknown = next((k for k in exc.instance if k not in allowed), None)
        if unknown and unknown.isascii() and unknown.isidentifier() and len(unknown) <= 40:
            path.append(unknown)
    return ScriptError(f"{_path(path)}: validacion {exc.validator}")


def _reject_controls(value: object, path: list[object] | None = None) -> None:
    path = [] if path is None else path
    if isinstance(value, str):
        allow_newline = len(path) >= 3 and path[0] == "scenes" and path[-1] in {"say", "caption"}
        if any(
            unicodedata.category(char) == "Cc" and not (allow_newline and char == "\n")
            for char in value
        ):
            raise ScriptError(f"{_path(path)}: caracter de control no permitido")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_controls(key, [*path, "key"])
            _reject_controls(item, [*path, key])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_controls(item, [*path, index])


def parse_video_script(data: object) -> VideoScript:
    """Validate M25 JSON, semantic scene order, controls, size, and apply defaults.

    Errors always name a field or path but never echo script contents.
    """
    try:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ScriptError("script: JSON invalido") from exc
    if len(raw.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ScriptError("script: tamano maximo 65536 bytes")

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    error = next(validator.iter_errors(data), None)
    if error is not None:
        raise _schema_error(error)
    assert isinstance(data, dict)  # guaranteed by the schema
    _reject_controls(data)

    input_scenes = data["scenes"]
    ids: set[str] = set()
    intro_seen = False
    outro_seen = False
    if not any(scene["type"] == "fact" for scene in input_scenes):
        raise ScriptError("scenes.type: se requiere al menos una fact")
    for index, scene in enumerate(input_scenes):
        scene_id = scene["id"]
        if scene_id in ids:
            raise ScriptError(f"scenes[{index}].id: duplicado")
        ids.add(scene_id)
        scene_type = scene["type"]
        if scene_type == "intro":
            if intro_seen or index != 0:
                raise ScriptError(f"scenes[{index}].type: intro debe ser unica y primera")
            intro_seen = True
        elif scene_type == "outro":
            if outro_seen or index != len(input_scenes) - 1:
                raise ScriptError(f"scenes[{index}].type: outro debe ser unica y ultima")
            outro_seen = True

    music = data.get("music", {})
    voice = data.get("voice", {})
    scenes = tuple(
        SceneSpec(
            id=scene["id"],
            type=scene["type"],
            title=scene["title"],
            sub=scene.get("sub", ""),
            say=scene["say"],
            caption=scene.get("caption", scene["say"]),
            query={lang: list(scene["query"][lang]) for lang in ("es", "en")},
            prompt=scene["prompt"],
            avoid=tuple(scene.get("avoid", [])),
        )
        for scene in input_scenes
    )
    return VideoScript(
        title=data["title"],
        aspect=data["aspect"],
        language=data.get("language", "es"),
        music=MusicSpec(mood=music.get("mood", "upbeat"), bpm=music.get("bpm", 120)),
        voice=VoiceSpec(
            voice=voice.get("voice", "F1"),
            speed=float(voice.get("speed", 1.2)),
            lexicon=dict(voice.get("lexicon", {})),
        ),
        scenes=scenes,
    )


def estimate_durations(script: VideoScript) -> DurationEstimate:
    """Seconds per scene and total, each rounded to 2 decimals.

    SEC_PER_CHAR=0.065 is provisional, inherited from the original engine;
    M26 recalibrates it against real generated speech measurements.
    """
    durations = {
        scene.id: round(max(1.5, len(scene.say) * SEC_PER_CHAR / script.voice.speed), 2)
        for scene in script.scenes
    }
    return {"scenes": durations, "total": round(sum(durations.values()), 2)}


def script_id(script: VideoScript) -> str:
    """SHA-256 of canonical, default-expanded UTF-8 JSON for a parsed script."""
    canonical = json.dumps(
        asdict(script), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
