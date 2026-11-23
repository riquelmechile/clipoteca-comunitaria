#!/usr/bin/env python3
"""Export the pinned espeak wav2vec2 checkpoint to a small int8 ONNX asset."""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

REPO = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"
REVISION = "2c733782da5604684829819a5eb744c193fe9398"


def _describe(path: Path) -> str:
    return f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}  {path.stat().st_size}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("dist/phon"))
    args = parser.parse_args()

    import torch
    from huggingface_hub import hf_hub_download
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModelForCTC

    args.out.mkdir(parents=True, exist_ok=True)
    fp32_path = args.out / "w2v2-phon-espeak.model.fp32.onnx"
    int8_path = args.out / "w2v2-phon-espeak.model.int8.onnx"
    vocab_path = args.out / "w2v2-phon-espeak.vocab.json"
    model = AutoModelForCTC.from_pretrained(REPO, revision=REVISION).eval()

    class Wrapper(torch.nn.Module):
        def __init__(self, wrapped: torch.nn.Module):
            super().__init__()
            self.model = wrapped

        def forward(self, values: torch.Tensor) -> torch.Tensor:
            return self.model(values).logits

    torch.onnx.export(
        Wrapper(model),
        torch.randn(1, 32000),
        fp32_path,
        input_names=["input_values"],
        output_names=["logits"],
        dynamic_axes={"input_values": {0: "b", 1: "t"}, "logits": {0: "b", 1: "f"}},
        opset_version=17,
        dynamo=False,
    )
    quantize_dynamic(
        fp32_path,
        int8_path,
        weight_type=QuantType.QInt8,
        per_channel=True,
        op_types_to_quantize=["MatMul"],
    )
    source_vocab = hf_hub_download(REPO, "vocab.json", revision=REVISION)
    shutil.copyfile(source_vocab, vocab_path)
    fp32_path.unlink()
    print(_describe(int8_path))
    print(_describe(vocab_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
