"""`_bandeja.md`: la cola de aprobacion, y `_bandeja.log`, el libro de envios.

Mismo patron que `_calendario.md`: un fichero markdown del repo del cliente que
una persona edita en Obsidian y que el bot lee y reescribe. Aqui no es una tabla
porque los borradores son parrafos, asi que cada item es un bloque `##`.

El bot **solo envia bloques en estado `aprobado`**. Es el mismo invariante que
`PUBLISHABLE` en la agenda, y por la misma razon: que un fichero mal editado no
pueda disparar mensajes en la cuenta de un cliente.

El libro de envios (`_bandeja.log`) es append-only y cumple una funcion que el
markdown no puede: Meta permite **una sola respuesta privada por comentarista**
y no avisa de que se ha gastado. Si no lo recordamos nosotros, no lo recuerda
nadie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from publisher.agenda import MADRID

PENDING = "pendiente"
APPROVED = "aprobado"
SENT = "enviado"
DISCARDED = "descartado"
FAILED = "error"

# Rafaga maxima por ejecucion. El limite de Meta es mucho mas alto, pero diez
# DMs seguidos desde una cuenta que normalmente contesta a mano se lee como un
# bot. Con varias pasadas al dia, cinco sobra.
MAX_SEND_PER_RUN = 5

STAMP = "%Y-%m-%d %H:%M"

_FIELD = re.compile(r"^-\s*([A-Za-z]+):\s*(.*)$")
_BLOCK = re.compile(r"^##\s+")
_DRAFT_HEADER = re.compile(r"^\*\*Borrador:\*\*\s*$")
_SAID_HEADER = re.compile(r"^\*\*Dijo:\*\*\s*$")

CABECERA = """# Bandeja - pendiente de responder

> **Este archivo lo lee y lo escribe el bot.** Reglas:
> - El bot **solo envia** bloques cuyo `Estado:` sea **`aprobado`**.
> - Edita libremente el texto bajo `Borrador:` antes de aprobar.
> - `descartado` se ignora para siempre; `pendiente` es lo que espera decision.
> - No toques las lineas `ID:`, `Tipo:`, `Recibido:` ni `Cierra:`.
> - `Cierra:` es el limite de Meta para contestar. Pasado eso ya no se puede.
"""


@dataclass
class Item:
    """Un DM o comentario pendiente, con su borrador."""

    id: str
    kind: str  # dm | comment | private_reply
    author: str  # @handle, para que la persona sepa a quien contesta
    author_id: str  # el id de Meta, que es lo que el libro de envios recuerda
    received: datetime
    closes: datetime | None
    estado: str
    said: str = ""
    draft: str = ""
    lineno: int = 0
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.estado == APPROVED

    def __str__(self) -> str:
        return f"[{self.kind}] {self.author} ({self.estado}) {self.received:%Y-%m-%d %H:%M}"

    def render(self) -> str:
        """El bloque markdown tal y como se escribe en `_bandeja.md`."""
        cierra = ""
        if self.closes:
            cierra = "\n- Cierra: " + self.closes.strftime(STAMP)
        dijo = "\n".join(f"> {line}" for line in (self.said or "").splitlines())
        return (
            f"\n## {self.author} - {self.kind}\n"
            f"- ID: {self.id}\n"
            f"- Tipo: {self.kind}\n"
            f"- De: {self.author}\n"
            f"- AuthorId: {self.author_id}\n"
            f"- Recibido: {self.received.strftime(STAMP)}{cierra}\n"
            f"- Estado: {self.estado}\n\n"
            f"**Dijo:**\n{dijo or '> (sin texto)'}\n\n"
            f"**Borrador:**\n{self.draft.strip()}\n"
        )


def _parse_stamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), STAMP).replace(tzinfo=MADRID)
    except ValueError:
        return None


def _section(block: list[str], header: re.Pattern[str]) -> str:
    """Texto que sigue a `**Dijo:**` o `**Borrador:**` hasta el siguiente header."""
    out: list[str] = []
    collecting = False
    for line in block:
        if header.match(line):
            collecting = True
            continue
        if collecting:
            if line.startswith("**") or _FIELD.match(line):
                break
            out.append(line[2:] if line.startswith("> ") else line)
    return "\n".join(out).strip()


def parse(path: Path) -> list[Item]:
    """Lee la bandeja. Tolerante: un bloque sin `ID:` se ignora en vez de romper."""
    if not path.is_file():
        return []

    items: list[Item] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if _BLOCK.match(line)]

    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = lines[start:end]

        fields: dict[str, str] = {}
        for line in block:
            m = _FIELD.match(line)
            if m is not None:
                fields[m.group(1).lower()] = m.group(2).strip()

        if "id" not in fields:
            continue
        received = _parse_stamp(fields.get("recibido", ""))
        if received is None:
            continue

        items.append(
            Item(
                id=fields["id"],
                kind=fields.get("tipo", "dm"),
                author=fields.get("de", "?"),
                author_id=fields.get("authorid", ""),
                received=received,
                closes=_parse_stamp(fields.get("cierra", "")),
                estado=fields.get("estado", PENDING).lower(),
                said=_section(block, _SAID_HEADER),
                draft=_section(block, _DRAFT_HEADER),
                lineno=start,
                extra=fields,
            )
        )
    return items


def known_ids(path: Path) -> set[str]:
    """IDs ya en la bandeja, para no duplicar un DM en cada pasada."""
    return {item.id for item in parse(path)}


def approved(items: list[Item]) -> list[Item]:
    """Lo que la persona ha aprobado, de lo mas antiguo a lo mas nuevo."""
    return sorted((i for i in items if i.is_approved), key=lambda i: i.received)


def append_items(path: Path, items: list[Item]) -> int:
    """Anade bloques nuevos al final. Devuelve cuantos ha escrito."""
    if not items:
        return 0
    existing = known_ids(path)
    fresh = [i for i in items if i.id not in existing]
    if not fresh:
        return 0

    cabecera = "" if path.is_file() else CABECERA
    with path.open("a", encoding="utf-8") as fh:
        if cabecera:
            fh.write(cabecera)
        for item in fresh:
            fh.write(item.render())
    return len(fresh)


def set_estado(path: Path, item_id: str, estado: str, note: str = "") -> bool:
    """Reescribe el `Estado:` de un bloque, localizandolo por su `ID:`.

    Se busca por ID y no por numero de linea a proposito: entre que se lee la
    bandeja y se escribe el resultado, la persona puede haber editado el fichero
    en Obsidian y haber movido todo lo demas de sitio.
    """
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()

    anchor = None
    for i, line in enumerate(lines):
        if line.lower().startswith("- id:") and line.split(":", 1)[1].strip() == item_id:
            anchor = i
            break
    if anchor is None:
        return False

    for i in range(anchor, len(lines)):
        if i > anchor and _BLOCK.match(lines[i]):
            return False
        if lines[i].lower().startswith("- estado:"):
            lines[i] = f"- Estado: {estado}"
            if note:
                lines.insert(i + 1, f"- Resultado: {note}")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


# --------------------------------------------------------------------------
# Libro de envios
# --------------------------------------------------------------------------

def append_ledger(path: Path, item: Item, result: str) -> None:
    """Rastro append-only de todo lo que ha salido de verdad."""
    stamp = datetime.now(MADRID).isoformat(timespec="seconds")
    line = f"{stamp}\t{item.kind}\t{item.id}\t{item.author_id}\t{result}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def already_private_replied(path: Path, author_id: str) -> bool:
    """Si ya se gasto la unica respuesta privada que Meta permite con esa persona.

    Ante la duda (fichero ilegible, `author_id` vacio) devuelve True: dejar de
    contestar a alguien es recuperable; que Meta nos marque por insistir, no.
    """
    if not author_id:
        return True
    if not path.is_file():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 5 and parts[1] == "private_reply" and parts[3] == author_id:
                if parts[4].startswith("ok"):
                    return True
    except OSError:
        return True
    return False
