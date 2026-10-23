"""Capa de interaccion: responder DMs y comentarios, con aprobacion humana.

Modulo aparte de `publisher/` a proposito. Publicar y conversar tienen cadencias
y riesgos distintos: publicar es un cron semanal e idempotente; conversar tiene
ventanas de horas, limites por persona y un error se lee como si lo hubiera
escrito el cliente.

Invariante que comparte con `publisher/agenda.py` y que es la razon de ser del
diseno: **el bot solo actua sobre lo que una persona ha marcado**. Redacta
borradores en `_bandeja.md` como `pendiente`; nada sale hasta que alguien lo
pasa a `aprobado` a mano.
"""

__all__ = ["cli", "draft", "fixtures", "meta", "queue", "windows"]
