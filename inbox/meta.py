"""Cliente de la Graph API para mensajes y comentarios de Instagram.

Reutiliza a proposito `publisher.meta`: mismo host (`graph.instagram.com` en la
ruta `instagram`), mismo tipo de token y, sobre todo, **la misma extraccion de
errores**. Duplicar `_check` aqui significaria que dentro de seis meses los dos
modulos fallan de forma distinta ante el mismo error de Meta.

Permisos que necesita cada cosa (hay que marcarlos **antes** de generar el
token; anadirlos despues obliga a regenerarlo):

- leer y responder DMs ............ `instagram_business_manage_messages`
- leer y responder comentarios .... `instagram_business_manage_comments`
- respuesta privada a comentario ... los **dos** de arriba
- metricas ........................ `instagram_business_manage_insights`

⚠️ Pendiente de verificar contra la API real el 26 oct 2026: los nombres exactos
de campo de `conversations` y `comments` en la ruta de Instagram Login. Estan
escritos segun la documentacion, pero la propia `publisher.meta.account_info` ya
tuvo que llevar un fallback porque `account_type` no se expone en todas las
rutas. Por eso `fetch_*` pide los campos en una sola llamada y cualquier
`MetaError` sube tal cual: es mejor un fallo claro en `inbox check` que un
parseo silencioso que devuelve listas vacias y parece que no hay mensajes.
"""

from __future__ import annotations

import json
from typing import Any

from publisher import meta as graph

MetaError = graph.MetaError

# Meta permite responder un DM hasta 7 dias despues con esta etiqueta, en vez de
# las 24 h estandar. Es exactamente el caso de uso de este modulo: lo redacta el
# bot y lo aprueba una persona, que no siempre esta delante el mismo dia.
HUMAN_AGENT_TAG = "HUMAN_AGENT"


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------

def fetch_conversations(ig_user_id: str, token: str, limit: int = 25) -> list[dict[str, Any]]:
    """Conversaciones de DM, mas recientes primero.

    Pide los mensajes anidados en la misma llamada: el flujo necesita saber
    **cuando escribio el usuario por ultima vez** para calcular la ventana, y
    hacerlo con una peticion por conversacion gasta cuota sin motivo.
    """
    data = graph._get(
        f"{ig_user_id}/conversations",
        token,
        platform="instagram",
        fields="id,updated_time,participants,messages.limit(10){id,created_time,from,to,message}",
        limit=limit,
    )
    return list(data.get("data") or [])


def fetch_media(ig_user_id: str, token: str, limit: int = 10) -> list[dict[str, Any]]:
    """Ultimas publicaciones, para saber donde buscar comentarios."""
    data = graph._get(
        f"{ig_user_id}/media",
        token,
        fields="id,caption,timestamp,comments_count,permalink",
        limit=limit,
    )
    return list(data.get("data") or [])


def fetch_comments(media_id: str, token: str, limit: int = 50) -> list[dict[str, Any]]:
    """Comentarios de una publicacion, con autor y marca de tiempo."""
    data = graph._get(
        f"{media_id}/comments",
        token,
        fields="id,text,timestamp,username,from,parent_id",
        limit=limit,
    )
    return list(data.get("data") or [])


def fetch_insights(ig_user_id: str, token: str, metrics: str = "reach,impressions,profile_views") -> list[dict[str, Any]]:
    """Metricas de la cuenta. Requiere `manage_insights`."""
    data = graph._get(
        f"{ig_user_id}/insights", token, metric=metrics, period="day"
    )
    return list(data.get("data") or [])


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------

def send_dm(ig_user_id: str, token: str, recipient_id: str, text: str, human_agent: bool = True) -> str:
    """Responde por DM a quien ya ha escrito a la cuenta.

    No sirve para escribir primero a nadie: la API exige que la conversacion la
    haya abierto el usuario. Para alcanzar a quien ha comentado esta
    `private_reply`, que es la unica via legitima de iniciar un DM.
    """
    params: dict[str, Any] = {
        "recipient": _json({"id": recipient_id}),
        "message": _json({"text": text}),
    }
    if human_agent:
        params["messaging_type"] = "MESSAGE_TAG"
        params["tag"] = HUMAN_AGENT_TAG
    data = graph._post(f"{ig_user_id}/messages", token, **params)
    return str(data.get("message_id") or data.get("id") or "")


def private_reply(ig_user_id: str, token: str, comment_id: str, text: str) -> str:
    """DM a quien ha comentado, dirigido por `comment_id`.

    Meta permite **uno solo por comentarista** y no devuelve error distinguible
    si ya se gasto, asi que quien decide si se puede es
    `queue.already_private_replied`, no esta funcion.
    """
    data = graph._post(
        f"{ig_user_id}/messages",
        token,
        recipient=_json({"comment_id": comment_id}),
        message=_json({"text": text}),
    )
    return str(data.get("message_id") or data.get("id") or "")


def reply_comment(comment_id: str, token: str, text: str) -> str:
    """Respuesta publica, en el hilo del propio comentario."""
    data = graph._post(f"{comment_id}/replies", token, message=text)
    return str(data.get("id") or "")


def hide_comment(comment_id: str, token: str, hidden: bool = True) -> None:
    """Oculta un comentario sin borrarlo.

    Para spam y faltas de respeto en la cuenta de un cliente: ocultar es
    reversible y no notifica a nadie; borrar deja al cliente expuesto a la
    captura de pantalla de "me has censurado".
    """
    graph._post(comment_id, token, hide="true" if hidden else "false")


def _json(value: dict[str, str]) -> str:
    """`recipient` y `message` viajan como JSON dentro de parametros de formulario."""
    return json.dumps(value)
