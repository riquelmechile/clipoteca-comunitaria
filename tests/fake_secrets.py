"""Deterministic runtime-only credentials for tests."""


def fake_secret(label: str, length: int = 32) -> str:
    if length < 16:
        raise ValueError("test credential length must be at least 16")
    seed = "-".join(("fake", label, "value"))
    return (seed + "x" * length)[:length]
