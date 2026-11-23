"""Lazy phoneme recognition and deterministic PER primitives."""

from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from .textnorm import _distance, norm_words

NORM = {
    "β": "b",
    "ð": "d",
    "ɣ": "ɡ",
    "g": "ɡ",
    "ɾ": "r",
    "ʝ": "j",
    "ɪ": "i",
    "ʊ": "u",
    "ɛ": "e",
    "ɔ": "o",
    "ɐ": "a",
    "ə": "e",
    "θ": "s",
    "ʃ": "s",
    "x": "h",
    "ɲ": "nj",
}
_SESSION: Any = None
_VOCAB: dict[int, str] = {}


def toks(value: str) -> list[str]:
    value = value.replace("ˈ", "").replace("ˌ", "").replace(" ", "").replace("|", "")
    out: list[str] = []
    for char in value:
        if char in "ːʲ̃͡" and out:
            out[-1] += char
        else:
            out.append(char)
    return out


def _n(char: str) -> str:
    char = char.replace("ː", "").replace("̃", "")
    return NORM.get(char, char)


def per(ref_ipa: str, hyp_ipa: str) -> float:
    ref = [_n(item) for item in toks(ref_ipa)]
    hyp = [_n(item) for item in toks(hyp_ipa)]
    return _distance(ref, hyp) / len(ref) if ref else 0.0


def ipa_ref(text: str, lang: str = "es-419") -> str:
    from espeakng_loader import get_data_path, get_library_path
    from phonemizer import phonemize
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    EspeakWrapper.set_library(get_library_path())
    EspeakWrapper.set_data_path(get_data_path())
    words = norm_words(text, "es")
    result = phonemize(
        words,
        language=lang,
        backend="espeak",
        strip=True,
        with_stress=False,
        njobs=1,
    )
    return "".join(part.replace(" ", "") for part in result)


def _session(model_dir: str | Path) -> None:
    global _SESSION, _VOCAB
    import onnxruntime as ort

    root = Path(model_dir)
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    _SESSION = ort.InferenceSession(
        str(root / "w2v2-phon-espeak.model.int8.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    vocab = json.loads((root / "w2v2-phon-espeak.vocab.json").read_text(encoding="utf-8"))
    _VOCAB = {int(index): token for token, index in vocab.items()}


def recog(y16: Any) -> str:
    import numpy as np

    if _SESSION is None:
        raise RuntimeError("phoneme session is not initialized")
    values = np.asarray(y16, dtype=np.float32)
    values = (values - values.mean()) / np.sqrt(values.var() + 1e-7)
    logits = _SESSION.run(None, {"input_values": values[None].astype(np.float32)})[0][0]
    out: list[str] = []
    previous = -1
    for item in logits.argmax(-1):
        index = int(item)
        if index != previous and index > 3:
            out.append(_VOCAB.get(index, ""))
        previous = index
    return "".join(out)


def _init_worker(model_dir: str) -> None:
    _session(model_dir)
    ipa_ref("prueba")


def _score(text: str, samples: Any) -> tuple[float, str, str]:
    ref = ipa_ref(text)
    hyp = recog(samples)
    return per(ref, hyp), ref, hyp


class Worker:
    """Run the PER model in an isolated, single spawned process."""

    def __init__(self, model_dir: str | Path):
        self._pool = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_init_worker,
            initargs=(str(model_dir),),
        )

    def score(self, text: str, samples: Any) -> tuple[float, str, str]:
        return self._pool.submit(_score, text, samples).result()

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    def __enter__(self) -> "Worker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
