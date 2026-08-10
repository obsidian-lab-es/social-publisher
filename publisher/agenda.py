"""Lectura y escritura de `_calendario.md`, la unica fuente de verdad del estado.

El archivo lo edita una persona en Obsidian, asi que el parseo tiene que ser
tolerante: se ignora todo lo que no sea una fila de tabla con 7 celdas y una
fecha ISO valida. Eso deja fuera cabeceras, separadores, filas vacias y
cualquier prosa alrededor, sin necesidad de saber en que tabla estamos.

El modulo se llama `agenda` y no `calendar` a proposito: `publisher/calendar.py`
ensombreceria el modulo `calendar` de la libreria estandar para cualquiera que
haga `import calendar` con el paquete en el path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

# El bot SOLO actua sobre filas en este estado. Cualquier otro se ignora.
PUBLISHABLE = "programado"
PUBLISHED = "publicado"

# Indices de columna en la tabla: # | Fecha | Hora | Tema | Carpeta | Estado | IDs
_COLS = 7
_COL_ESTADO = 5
_COL_IDS = 6

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^\d{1,2}:\d{2}$")


@dataclass
class Row:
    ref: str
    fecha: date
    hora: time
    tema: str
    carpeta: str
    estado: str
    ids: str
    lineno: int
    raw_cells: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        """`2026-09-A-tema/` -> `2026-09-A-tema` (sin backticks ni barra)."""
        return self.carpeta.strip("`/ ")

    @property
    def when(self) -> datetime:
        """Momento programado, siempre en hora de Madrid.

        Anclar aqui la zona es lo que hace que el cambio de horario de verano
        deje de importar: el cron corre en UTC, pero la comparacion es local.
        """
        return datetime.combine(self.fecha, self.hora, tzinfo=MADRID)

    def is_due(self, now: datetime) -> bool:
        return self.estado == PUBLISHABLE and self.when <= now

    def __str__(self) -> str:
        return f"[{self.ref}] {self.fecha} {self.hora:%H:%M} {self.tema} ({self.estado})"


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(path: Path) -> list[Row]:
    """Devuelve todas las filas de post del calendario, de ambas tablas."""
    rows: list[Row] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != _COLS:
            continue
        ref, fecha, hora, tema, carpeta, estado, ids = cells
        # Filtra cabecera, separador `---` y la fila vacia de plantilla.
        if not _ISO_DATE.match(fecha) or not _HHMM.match(hora):
            continue
        h, m = (int(x) for x in hora.split(":"))
        rows.append(
            Row(
                ref=ref,
                fecha=date.fromisoformat(fecha),
                hora=time(h, m),
                tema=tema,
                carpeta=carpeta,
                estado=estado.lower(),
                ids=ids,
                lineno=lineno,
                raw_cells=cells,
            )
        )
    return rows


def due_rows(rows: list[Row], now: datetime | None = None) -> list[Row]:
    """Filas listas para publicar, de la mas antigua a la mas reciente."""
    now = now or datetime.now(MADRID)
    return sorted((r for r in rows if r.is_due(now)), key=lambda r: r.when)


def mark_published(path: Path, row: Row, ig_id: str, fb_id: str) -> None:
    """Reescribe la fila como `publicado` y le anota los IDs de la API.

    Es lo que hace el sistema idempotente: una vez escrito esto, la fila deja
    de cumplir `is_due()` y ninguna ejecucion posterior la vuelve a tocar.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    cells = list(row.raw_cells)
    cells[_COL_ESTADO] = PUBLISHED
    cells[_COL_IDS] = f"ig:{ig_id} fb:{fb_id}"
    lines[row.lineno] = "| " + " | ".join(cells) + " |"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_log(path: Path, row: Row, ig_id: str, fb_id: str) -> None:
    """Rastro append-only, independiente del calendario, por si este se edita mal."""
    stamp = datetime.now(MADRID).isoformat(timespec="seconds")
    line = f"{stamp}\t{row.slug}\tig:{ig_id}\tfb:{fb_id}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
