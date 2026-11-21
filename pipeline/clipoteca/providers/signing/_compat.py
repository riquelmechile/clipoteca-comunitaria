"""Minimal Clipoteca bindings for vendored signing modules."""

import logging


class SigningFailed(RuntimeError):
    pass


class UpstreamChanged(RuntimeError):
    pass


class InvalidParam(ValueError):
    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def get_logger(name: str):
    return logging.getLogger(name)
