"""Validacion pre-flight de un post antes de tocar la API de Meta.

Todo lo que Meta rechazaria se detecta aqui, en local y gratis. La diferencia
practica: un fallo de validacion deja el workflow en rojo con un mensaje claro,
mientras que un rechazo de Meta a mitad del flujo puede dejar contenedores de
carrusel a medio crear y es mucho mas dificil de leer.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

# Instagram rechaza carruseles de feed fuera de este rango. 9:16 (0.5625) NO vale.
MIN_RATIO = 0.8
MAX_RATIO = 1.91

MIN_SLIDES = 2
MAX_SLIDES = 10

MAX_CAPTION_CHARS = 2200
MAX_HASHTAGS = 30

SLIDE_GLOBS = ("*.jpg", "*.jpeg")

_HASHTAG = re.compile(r"#\w+", re.UNICODE)


def slides_of(post_dir: Path) -> list[Path]:
    """Slides del post, ordenadas por nombre (01, 02, 03...).

    Deliberadamente no recursivo: las carpetas `4x5-old/` y `9x16-old/` que hay
    en algunos posts son versiones antiguas y no deben publicarse.
    """
    found: list[Path] = []
    for pattern in SLIDE_GLOBS:
        found.extend((post_dir / "slides").glob(pattern))
    return sorted(found, key=lambda p: p.name)


def read_caption(post_dir: Path) -> str:
    """El copy de `descripcion.md` va tal cual a Instagram: emojis y saltos incluidos."""
    return (post_dir / "descripcion.md").read_text(encoding="utf-8").strip()


def validate_post(post_dir: Path) -> list[str]:
    """Devuelve la lista de problemas. Vacia significa que el post es publicable."""
    errors: list[str] = []

    if not post_dir.is_dir():
        return [f"la carpeta no existe: {post_dir}"]

    # --- caption -------------------------------------------------------
    desc = post_dir / "descripcion.md"
    if not desc.is_file():
        errors.append("falta descripcion.md")
    else:
        caption = read_caption(post_dir)
        if not caption:
            errors.append("descripcion.md esta vacio")
        if len(caption) > MAX_CAPTION_CHARS:
            errors.append(
                f"caption de {len(caption)} caracteres, maximo {MAX_CAPTION_CHARS}"
            )
        hashtags = _HASHTAG.findall(caption)
        if len(hashtags) > MAX_HASHTAGS:
            errors.append(f"{len(hashtags)} hashtags, maximo {MAX_HASHTAGS}")

    # --- slides --------------------------------------------------------
    slides = slides_of(post_dir)
    if not (MIN_SLIDES <= len(slides) <= MAX_SLIDES):
        errors.append(
            f"{len(slides)} slides en slides/, se necesitan entre {MIN_SLIDES} y {MAX_SLIDES}"
        )

    for slide in slides:
        try:
            with Image.open(slide) as img:
                fmt, (width, height) = img.format, img.size
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de lectura es un error de validacion
            errors.append(f"{slide.name}: no se puede leer como imagen ({exc})")
            continue

        # La API de publicacion de Instagram solo acepta JPEG.
        if fmt != "JPEG":
            errors.append(f"{slide.name}: formato {fmt}, Instagram solo acepta JPEG")

        ratio = width / height
        if not (MIN_RATIO <= ratio <= MAX_RATIO):
            errors.append(
                f"{slide.name}: {width}x{height} ratio {ratio:.3f}, "
                f"fuera del rango {MIN_RATIO}-{MAX_RATIO} que acepta Instagram"
            )

    return errors
