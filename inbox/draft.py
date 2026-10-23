"""Redaccion de borradores a partir del guion de respuestas del cliente.

**Por plantillas y no por modelo de lenguaje, a proposito.** En inmobiliaria la
respuesta equivocada no es una errata: "si, sigue disponible" o "te lo dejamos
en 180.000" dicho por un bot en la cuenta del cliente es una promesa que alguien
va a tener que sostener. Un modelo escribe bien y acierta casi siempre, y "casi"
es exactamente el margen que no se puede asumir con dinero de otra persona.

Asi que el borrador sale del guion que el cliente escribe en `respuestas.md`, y
lo que no encaja en ninguna entrada se marca para que lo escriba una persona.
Esto deja el hueco preparado para meter un modelo mas adelante en la pieza donde
aporta sin inventar: reformular una respuesta ya aprobada para que no suene
calcada. Ese paso va *despues* de elegir la plantilla, no en lugar de elegirla.

`respuestas.md` vive en el repo del cliente y se edita en Obsidian:

    ## Disponibilidad
    - Palabras: disponible, sigue, queda, libre

    **Respuesta:**
    Hola! Gracias por escribir. Te confirmo disponibilidad hoy mismo por aqui.

    ## Por defecto

    **Respuesta:**
    Hola! Gracias por escribir, te contesto en un rato con el detalle.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .queue import _BLOCK, _FIELD, _section

DEFAULT_HEADING = "por defecto"

# Marca que se deja en el borrador cuando ninguna entrada encaja. Se busca a
# ojo en Obsidian y deja claro que ahi hay que escribir, no solo aprobar.
NEEDS_HUMAN = "[ESCRIBIR A MANO]"

_RESPONSE_HEADER = re.compile(r"^\*\*Respuesta:\*\*\s*$")


@dataclass
class Entry:
    """Una entrada del guion: palabras que la disparan y el texto a enviar."""

    heading: str
    keywords: list[str]
    response: str

    @property
    def is_default(self) -> bool:
        return not self.keywords or _norm(self.heading) == DEFAULT_HEADING


def _norm(text: str) -> str:
    """Minusculas y sin tildes, para que `hipoteca` case con `Hipoteca`."""
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in stripped if unicodedata.category(c) != "Mn")


def load_playbook(path: Path) -> list[Entry]:
    """Lee `respuestas.md`. Si no existe, devuelve lista vacia (todo a mano)."""
    if not path.is_file():
        return []

    entries: list[Entry] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if _BLOCK.match(line)]

    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = lines[start:end]
        heading = lines[start].lstrip("# ").strip()

        keywords: list[str] = []
        for line in block:
            m = _FIELD.match(line)
            if m is not None and m.group(1).lower() == "palabras":
                keywords = [k.strip() for k in m.group(2).split(",") if k.strip()]

        response = _section(block, _RESPONSE_HEADER)
        if response:
            entries.append(Entry(heading=heading, keywords=keywords, response=response))
    return entries


def match(text: str, entries: list[Entry]) -> Entry | None:
    """Primera entrada cuyas palabras aparecen en el mensaje.

    Gana la primera por orden del fichero, no la que mas palabras casa: asi el
    cliente controla la prioridad moviendo bloques en Obsidian, que es mas
    predecible que una puntuacion que no puede ver.
    """
    haystack = _norm(text)
    for entry in entries:
        if entry.is_default:
            continue
        if any(_norm(k) in haystack for k in entry.keywords):
            return entry
    return next((e for e in entries if e.is_default), None)


def draft_for(text: str, entries: list[Entry]) -> tuple[str, str]:
    """Devuelve `(borrador, motivo)` para un mensaje entrante.

    Nunca devuelve cadena vacia: un bloque sin borrador en la bandeja parece un
    fallo del bot, cuando lo que pasa es que hace falta una persona.
    """
    entry = match(text, entries)
    if entry is None:
        return (
            f"{NEEDS_HUMAN} No hay ninguna entrada en respuestas.md que encaje.",
            "sin guion",
        )
    if entry.is_default:
        return (
            f"{NEEDS_HUMAN} {entry.response}",
            f"plantilla por defecto ({entry.heading})",
        )
    return entry.response, f"plantilla: {entry.heading}"
