"""Las reglas de Meta sobre a quien se puede escribir y cuando.

Este es el modulo que evita que nos baneen la cuenta del cliente, asi que vive
aparte y no depende de la red: es logica pura y se puede probar en seco.

Reglas verificadas en la documentacion oficial el 23 oct 2026:

- **La conversacion la inicia siempre el usuario.** No existe endpoint para
  escribir primero a un seguidor nuevo. Lo que ofrece ManyChat ("Follow to DM")
  es una capacidad de partner en beta que Meta concede cuenta por cuenta; no
  esta en la API publica y por tanto aqui no se puede implementar.
- **Ventana estandar de 24 h** desde la ultima accion del usuario para
  responder a un DM.
- **Etiqueta `HUMAN_AGENT`: 7 dias**, pensada justamente para el flujo
  "redacta el bot, aprueba la persona", que no siempre cabe en 24 h.
- **Respuesta privada a un comentario: 7 dias** desde el comentario, y **un
  unico mensaje por comentarista**. En un Live, solo durante la emision.
- **Seguimiento tras una respuesta privada**: solo si la persona contesta, y
  dentro de las 24 h siguientes a su contestacion.

El limite de "un mensaje por comentarista" no se puede comprobar preguntando a
Meta: hay que recordarlo nosotros. De eso se encarga el libro de envios en
`queue.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

STANDARD_WINDOW = timedelta(hours=24)
HUMAN_AGENT_WINDOW = timedelta(days=7)
PRIVATE_REPLY_WINDOW = timedelta(days=7)
FOLLOWUP_WINDOW = timedelta(hours=24)

# Margen de seguridad: si quedan menos de esto, se considera cerrada. Entre que
# el borrador se aprueba y el cron siguiente lo envia pasa tiempo, y un envio
# que llega tarde no falla silenciosamente: devuelve error y queda en el log.
SAFETY_MARGIN = timedelta(minutes=30)


@dataclass(frozen=True)
class Verdict:
    """Si se puede enviar, por que, y hasta cuando."""

    ok: bool
    reason: str
    closes_at: datetime | None = None

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        until = f" (cierra {self.closes_at:%Y-%m-%d %H:%M})" if self.closes_at else ""
        return ("OK: " if self.ok else "NO: ") + self.reason + until


def _closed(closes_at: datetime, now: datetime, what: str) -> Verdict | None:
    if now >= closes_at:
        return Verdict(False, f"la ventana de {what} se cerro", closes_at)
    if now + SAFETY_MARGIN >= closes_at:
        return Verdict(
            False,
            f"a la ventana de {what} le quedan menos de "
            f"{int(SAFETY_MARGIN.total_seconds() // 60)} min; no arriesgo el envio",
            closes_at,
        )
    return None


def can_reply_dm(
    last_user_message_at: datetime, now: datetime, human_agent: bool = True
) -> Verdict:
    """Se puede responder a un DM que escribio el usuario.

    `human_agent=True` por defecto porque este sistema es literalmente eso: un
    humano aprueba cada respuesta. Da 7 dias en vez de 24 h, que es lo que hace
    viable el ciclo de aprobacion.
    """
    window = HUMAN_AGENT_WINDOW if human_agent else STANDARD_WINDOW
    closes_at = last_user_message_at + window
    if (bad := _closed(closes_at, now, "respuesta a DM")) is not None:
        return bad
    etiqueta = "con etiqueta HUMAN_AGENT (7 dias)" if human_agent else "estandar (24 h)"
    return Verdict(True, f"dentro de la ventana {etiqueta}", closes_at)


def can_private_reply(
    comment_at: datetime, now: datetime, already_replied: bool, is_live: bool = False
) -> Verdict:
    """Se puede mandar un DM a quien comento (respuesta privada).

    `already_replied` lo decide el libro de envios: Meta permite **uno solo** por
    comentarista y no avisa de que te lo has gastado, asi que si hay duda se dice
    que no.
    """
    if already_replied:
        return Verdict(
            False, "ya se le envio una respuesta privada; Meta permite solo una"
        )
    if is_live:
        return Verdict(
            False, "comentario de un Live: solo se puede responder durante la emision"
        )
    closes_at = comment_at + PRIVATE_REPLY_WINDOW
    if (bad := _closed(closes_at, now, "respuesta privada")) is not None:
        return bad
    return Verdict(True, "dentro de los 7 dias del comentario", closes_at)


def can_follow_up(their_reply_at: datetime, now: datetime) -> Verdict:
    """Se puede seguir la conversacion despues de que la persona haya contestado."""
    closes_at = their_reply_at + FOLLOWUP_WINDOW
    if (bad := _closed(closes_at, now, "seguimiento")) is not None:
        return bad
    return Verdict(True, "dentro de las 24 h de su respuesta", closes_at)


def can_reply_comment(comment_at: datetime, now: datetime) -> Verdict:
    """Responder en publico, en el hilo del comentario.

    No tiene ventana documentada: es una respuesta publica, no un mensaje. Se
    limita por cortesia y para no resucitar hilos viejos del cliente.
    """
    closes_at = comment_at + timedelta(days=30)
    if now >= closes_at:
        return Verdict(
            False, "el comentario tiene mas de 30 dias; responder ahora queda raro", closes_at
        )
    return Verdict(True, "comentario reciente", closes_at)
