"""Contracts for the Clipoteca v3 video studio (no rendering in M25)."""

from .script import (
    MusicSpec,
    SceneSpec,
    ScriptError,
    VideoScript,
    VoiceSpec,
    estimate_durations,
    parse_video_script,
    script_id,
)

__all__ = [
    "MusicSpec",
    "SceneSpec",
    "ScriptError",
    "VideoScript",
    "VoiceSpec",
    "estimate_durations",
    "parse_video_script",
    "script_id",
]
