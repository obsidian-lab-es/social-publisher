"""Datos simulados con la forma exacta que devuelve la Graph API.

Existe para que `inbox pull --demo` funcione sin token y sin cuenta de Meta: el
modulo se puede escribir, probar y revisar antes de que exista la app. Cuando el
token este, lo unico que cambia es de donde salen estos diccionarios.

Nombres y textos deliberadamente genericos y marcados como `demo`: este repo es
publico y no contiene datos de ningun cliente, ni reales ni verosimiles.

Los casos estan elegidos para que la demo ensene algo en cada uno:

- un DM reciente que **casa** con el guion -> borrador listo para aprobar
- un DM de hace nueve dias -> **fuera de ventana**, aparece marcado a mano
- un DM ya contestado por nosotros -> se **ignora**, no se vuelve a encolar
- un comentario con palabra clave -> candidato al embudo comentario -> DM
- un comentario de spam -> sin plantilla, marcado para una persona
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# El id de la cuenta en la demo. Coincide con lo que `_ig_id()` devuelve por
# defecto para que el filtro de "mensaje nuestro" se pueda probar.
DEMO_IG_ID = "me"

API_STAMP = "%Y-%m-%dT%H:%M:%S+0000"


def _stamp(moment: datetime) -> str:
    """Formato de Meta: UTC con offset sin dos puntos.

    La conversion a UTC es de verdad y no cosmetica. Etiquetar la hora local
    como `+0000` desplazaria la demo dos horas en verano y haria parecer que las
    ventanas se calculan mal cuando el que esta mal es el dato de prueba.
    """
    return moment.astimezone(timezone.utc).strftime(API_STAMP)


def conversations(now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "id": "conv_demo_1",
            "updated_time": _stamp(now - timedelta(hours=3)),
            "participants": {"data": [{"id": "17841400000000001", "username": "demo_interesada"}]},
            "messages": {
                "data": [
                    {
                        "id": "msg_demo_1",
                        "created_time": _stamp(now - timedelta(hours=3)),
                        "from": {"id": "17841400000000001", "username": "demo_interesada"},
                        "message": "Hola! Sigue disponible lo del ultimo post?",
                    }
                ]
            },
        },
        {
            "id": "conv_demo_2",
            "updated_time": _stamp(now - timedelta(days=9)),
            "participants": {"data": [{"id": "17841400000000002", "username": "demo_curioso"}]},
            "messages": {
                "data": [
                    {
                        "id": "msg_demo_2",
                        "created_time": _stamp(now - timedelta(days=9)),
                        "from": {"id": "17841400000000002", "username": "demo_curioso"},
                        "message": "Buenas, que comision llevais por el servicio?",
                    }
                ]
            },
        },
        {
            "id": "conv_demo_3",
            "updated_time": _stamp(now - timedelta(hours=1)),
            "participants": {"data": [{"id": "17841400000000003", "username": "demo_contestada"}]},
            "messages": {
                "data": [
                    {
                        "id": "msg_demo_3b",
                        "created_time": _stamp(now - timedelta(hours=1)),
                        "from": {"id": DEMO_IG_ID, "username": "demo_cuenta"},
                        "message": "Te lo confirmo manana a primera hora.",
                    },
                    {
                        "id": "msg_demo_3a",
                        "created_time": _stamp(now - timedelta(hours=2)),
                        "from": {"id": "17841400000000003", "username": "demo_contestada"},
                        "message": "Sigue libre lo que preguntaba?",
                    },
                ]
            },
        },
    ]


def media() -> list[dict[str, Any]]:
    return [
        {
            "id": "media_demo_1",
            "caption": "Publicacion de ejemplo",
            "timestamp": _stamp(datetime.now() - timedelta(days=2)),
            "comments_count": 2,
            "permalink": "https://instagram.com/p/DEMO1",
        }
    ]


def comments_for(media_id: str) -> list[dict[str, Any]]:
    now = datetime.now()
    return [
        {
            "id": "comment_demo_1",
            "text": "Me interesa, sigue disponible algo asi?",
            "timestamp": _stamp(now - timedelta(days=1)),
            "username": "demo_comentarista",
            "from": {"id": "17841400000000004"},
            "parent_id": "",
        },
        {
            "id": "comment_demo_2",
            "text": "GANA 5000 EUROS AL MES DESDE CASA, escribeme",
            "timestamp": _stamp(now - timedelta(hours=5)),
            "username": "demo_spam",
            "from": {"id": "17841400000000005"},
            "parent_id": "",
        },
    ]
