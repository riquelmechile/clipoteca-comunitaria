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


_LAZY_EXPORTS = {
    "estimate_scenes": ".plan",
    "build_plan": ".plan",
    "scene_job": ".plan",
    "write_plan": ".plan",
    "ClipScorer": ".clipscore",
    "windows": ".windows",
    "fit_mode": ".windows",
    "choose_window": ".windows",
    "load_catalog": ".library",
    "text_score": ".library",
    "candidates": ".library",
    "assign_scenes": ".library",
}


def __getattr__(name: str):
    """Expose optional features without loading CLIP or media dependencies."""
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    from importlib import import_module

    value = getattr(import_module(_LAZY_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value


__all__ += list(_LAZY_EXPORTS)
