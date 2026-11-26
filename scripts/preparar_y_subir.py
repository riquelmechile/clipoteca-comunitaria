#!/usr/bin/env python3
"""Prepare a contribution locally and upload with public tickets, without an API key."""

import argparse
import json
import math
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

MIB = 1024 * 1024
MAX_SECONDS = 120
CHUNK = 24 * MIB


def run(args, timeout=180):
    return subprocess.run(args, check=True, capture_output=True, timeout=timeout).stdout


def probe(path):
    return json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        )
    )


def prepare(source, target, kind, start, end):
    info = probe(source)
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    rotation = next(
        (s["rotation"] for s in stream.get("side_data_list", []) if "rotation" in s),
        stream.get("tags", {}).get("rotate", 0),
    )
    width, height = int(stream["width"]), int(stream["height"])
    try:
        sar = Fraction(stream.get("sample_aspect_ratio", "1:1").replace(":", "/"))
        if sar > 0:
            width = max(1, round(width * sar))
    except (ValueError, ZeroDivisionError):
        pass
    if round(float(rotation)) % 180 == 90:
        width, height = height, width
    base = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file,pipe"]
    if kind == "image":
        if start is not None or end is not None:
            raise ValueError("No se recorta una foto en el tiempo.")
        factor = min(1, 1920 / max(width, height))
        dims = f"{max(1, int(width * factor))}:{max(1, int(height * factor))}"
        run(
            base
            + [
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                f"scale={dims}:flags=lanczos,setsar=1",
                "-map_metadata",
                "-1",
                "-c:v",
                "libwebp",
                "-quality",
                "85",
                str(target),
            ],
            480,
        )
    else:
        duration = float(info["format"]["duration"])
        start = 0.0 if start is None else start
        end = duration if end is None else end
        if not (
            math.isfinite(duration) and 0 <= start < end <= duration and end - start <= MAX_SECONDS
        ):
            raise ValueError("Video: indica un tramo valido de hasta 120 s con --desde/--hasta.")
        factor = min(1, 720 / min(width, height))
        dims = f"{max(2, int(width * factor) // 2 * 2)}:{max(2, int(height * factor) // 2 * 2)}"
        rate = stream.get("avg_frame_rate", "0/0")
        try:
            fps = Fraction(rate)
        except (ValueError, ZeroDivisionError):
            fps = Fraction(30)
        fps = min(max(fps, Fraction(1)), Fraction(60))
        run(
            base
            + [
                "-ss",
                str(start),
                "-i",
                str(source),
                "-t",
                str(end - start),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-vf",
                f"scale={dims}:flags=lanczos,setsar=1,fps={fps}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "medium",
                "-crf",
                "23",
                "-threads",
                "2",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(target),
            ],
            480,
        )
        if not 0 < float(probe(target)["format"]["duration"]) <= MAX_SECONDS:
            raise ValueError("El video preparado excede 120 s.")
    if not 0 < target.stat().st_size <= (10 if kind == "image" else 100) * MIB:
        raise ValueError("El archivo preparado supera el limite de bytes.")


def curl(url, method="GET", *, body=None, file=None, token=None):
    args = [
        "curl",
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--max-time",
        "180",
        "--proto",
        "=https",
        "--request",
        method,
        url,
    ]
    if token:
        args += ["-H", "Authorization: Bearer " + token]
    if body is not None:
        args += [
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            json.dumps(body, ensure_ascii=True),
        ]
    if file is not None:
        args += ["--upload-file", str(file)]
    try:
        return json.loads(run(args, 190))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        # Do not expose the capability or curl arguments through a traceback.
        raise RuntimeError("Solicitud de subida fallida.") from exc


def upload(api, session, target, metadata, root):
    if not session:
        session = curl(api + "/v1/uploads", "POST", body={})["session_id"]
    ticket = curl(
        api + "/v1/uploads/" + session + "/files",
        "POST",
        body={**metadata, "size": target.stat().st_size},
    )
    url, token = ticket["put_url"], ticket["upload_token"]
    if (
        urlsplit(url).netloc != urlsplit(api).netloc
        or urlsplit(url).scheme != "https"
        or ticket["part_size"] != CHUNK
    ):
        raise ValueError("Ticket no valido.")
    with target.open("rb") as stream:
        for i in range(ticket["parts"]):
            part = root / "part"
            part.write_bytes(stream.read(CHUNK))
            for attempt in range(4):
                try:
                    curl(url + "/parts/" + str(i), "PUT", file=part, token=token)
                    break
                except RuntimeError:
                    if attempt == 3:
                        raise
                    time.sleep((2, 5, 125)[attempt])
    ref = ticket["media_id"]
    status_url = api + "/v1/media/" + ref
    try:
        curl(url + "/complete", "POST", token=token)
    except RuntimeError:
        status = curl(status_url)
        if status.get("status") not in ("processing", "registered"):
            raise
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        try:
            status = curl(status_url)
        except RuntimeError:
            time.sleep(5)
            continue
        if status.get("status") == "registered":
            return ref
        if status.get("status") == "rejected":
            raise RuntimeError("Aporte rechazado: " + str(status.get("reason", "")))
        time.sleep(5)
    raise RuntimeError("Registro pendiente; consulta el ID " + ref)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path)
    parser.add_argument("--api", required=True, help="Origen HTTPS del servicio")
    parser.add_argument(
        "--metadata",
        required=True,
        type=Path,
        help="JSON con description.es; ingles y tags recomendados",
    )
    parser.add_argument("--session")
    parser.add_argument("--desde", type=float)
    parser.add_argument("--hasta", type=float)
    args = parser.parse_args()
    try:
        api = args.api.rstrip("/")
        parsed = urlsplit(api)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("La API debe ser un origen HTTPS.")
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
        kind = metadata["kind"]
        if kind not in ("video", "image"):
            raise ValueError("kind debe ser video o image.")
        with tempfile.TemporaryDirectory(prefix="clipoteca-aporte-") as tmp:
            root = Path(tmp)
            target = root / ("normalized.mp4" if kind == "video" else "normalized.webp")
            prepare(args.archivo.resolve(), target, kind, args.desde, args.hasta)
            print(upload(api, args.session, target, metadata, root))
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        StopIteration,
        subprocess.SubprocessError,
    ) as exc:
        # The error may contain curl arguments; only expose our bounded messages.
        if isinstance(exc, (ValueError, RuntimeError)) and not isinstance(
            exc, json.JSONDecodeError
        ):
            parser.exit(1, str(exc) + "\n")
        parser.exit(1, "No se pudo preparar o registrar el archivo.\n")


if __name__ == "__main__":
    main()
