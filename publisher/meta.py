"""Cliente minimo de la Graph API para publicar en Instagram y en una Pagina de Facebook.

Hay **dos rutas** y se eligen con `META_ROUTE`:

- `instagram` (por defecto): *Instagram API with Instagram Login*, contra
  `graph.instagram.com`. No necesita Pagina de Facebook ni que la cuenta este
  vinculada a ninguna. En cambio el token es de usuario de Instagram y **caduca
  a los 60 dias**, asi que hay que renovarlo (`refresh_long_lived_token`).
- `facebook`: la ruta clasica contra `graph.facebook.com`, con token de System
  User que no caduca. Publica tambien en la Pagina, pero exige tener *Control
  total* de una Pagina vinculada a la cuenta de Instagram.

Los endpoints de publicacion en Instagram son identicos en las dos; lo unico
que cambia es el host, el tipo de token y si hay o no publicacion en Facebook.

Dos cosas que la documentacion de Meta no deja obvias y que estan resueltas aqui:

1. Un contenedor de carrusel tarda en procesarse. Llamar a `media_publish` antes
   de que su `status_code` sea `FINISHED` falla de forma intermitente, asi que
   hay que sondear (`wait_until_finished`).
2. Publicar en Instagram por API **no** hace crosspost a la Pagina vinculada.
   El crossposting nativo de Meta es solo para publicacion manual. Por eso hay
   un flujo de Facebook separado y explicito.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

# v25.0 es la actual (feb 2026). v23.0 esta comodamente dentro de soporte y los
# endpoints de publicacion son identicos; se puede subir con GRAPH_VERSION.
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v23.0")

IG_HOST = "https://graph.instagram.com"
FB_HOST = "https://graph.facebook.com"
ROUTES = ("instagram", "facebook")

ROUTE = os.environ.get("META_ROUTE", "instagram").strip().lower()
if ROUTE not in ROUTES:
    # Una errata aqui publicaria contra el host equivocado con el token
    # equivocado, asi que revienta en el import y no a mitad del flujo.
    raise ValueError(f"META_ROUTE={ROUTE!r} no es valido; usa uno de {ROUTES}")

PUBLISHES_TO_FACEBOOK = ROUTE == "facebook"
BASE = f"{FB_HOST if PUBLISHES_TO_FACEBOOK else IG_HOST}/{GRAPH_VERSION}"

TIMEOUT = 60
POLL_INTERVAL = 5
POLL_MAX_SECONDS = 300


class MetaError(RuntimeError):
    """Error devuelto por la Graph API, con el mensaje de Meta ya extraido."""


def _check(resp: requests.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        raise MetaError(f"HTTP {resp.status_code}: respuesta no-JSON: {resp.text[:400]}")
    if "error" in payload:
        err = payload["error"]
        raise MetaError(
            f"HTTP {resp.status_code}: {err.get('message')} "
            f"(type={err.get('type')} code={err.get('code')} "
            f"subcode={err.get('error_subcode')})"
        )
    resp.raise_for_status()
    return payload


def _post(path: str, token: str, **params: Any) -> dict[str, Any]:
    params["access_token"] = token
    return _check(requests.post(f"{BASE}/{path}", data=params, timeout=TIMEOUT))


def _get(path: str, token: str, **params: Any) -> dict[str, Any]:
    params["access_token"] = token
    return _check(requests.get(f"{BASE}/{path}", params=params, timeout=TIMEOUT))


# --------------------------------------------------------------------------
# Comprobaciones de solo lectura
# --------------------------------------------------------------------------

def account_info(ig_user_id: str, token: str) -> dict[str, Any]:
    """Datos basicos de la cuenta. `ig_user_id` acepta `me` en la ruta instagram.

    `account_type` no esta expuesto en todas las rutas y versiones. Si Meta lo
    rechaza se reintenta sin el campo: el `check` existe para confirmar que el
    token funciona, y un campo de menos no es motivo para darlo por roto.
    """
    try:
        return _get(ig_user_id, token, fields="username,name,account_type")
    except MetaError:
        return _get(ig_user_id, token, fields="username,name")


def refresh_long_lived_token(token: str) -> dict[str, Any]:
    """Renueva el token de usuario de Instagram y devuelve el nuevo.

    Solo aplica a la ruta `instagram`. Condiciones de Meta: el token tiene que
    tener **mas de 24 h** y no haber caducado. Pasados 60 dias sin renovar ya no
    se puede recuperar; hay que repetir el login a mano. De ahi el cron mensual.
    """
    resp = requests.get(
        f"{IG_HOST}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=TIMEOUT,
    )
    return _check(resp)


def publishing_limit(ig_user_id: str, token: str) -> int:
    """Posts publicados por API en las ultimas 24 h. El limite de Meta es 100.

    Un carrusel cuenta como 1. A dos posts por semana esto siempre deberia ser
    0 o 1; cualquier cosa mayor significa que algo esta publicando de mas.
    """
    data = _get(
        f"{ig_user_id}/content_publishing_limit", token, fields="config,quota_usage"
    )
    entries = data.get("data") or [{}]
    return int(entries[0].get("quota_usage", 0))


def page_info(page_id: str, token: str) -> dict[str, Any]:
    return _get(page_id, token, fields="name")


# --------------------------------------------------------------------------
# Instagram: carrusel en tres fases
# --------------------------------------------------------------------------

def create_carousel_item(ig_user_id: str, token: str, image_url: str) -> str:
    data = _post(
        f"{ig_user_id}/media", token, image_url=image_url, is_carousel_item="true"
    )
    return data["id"]


def create_carousel(
    ig_user_id: str, token: str, children: list[str], caption: str
) -> str:
    data = _post(
        f"{ig_user_id}/media",
        token,
        media_type="CAROUSEL",
        children=",".join(children),
        caption=caption,
    )
    return data["id"]


def wait_until_finished(container_id: str, token: str) -> None:
    """Sondea el contenedor hasta que Meta lo marca listo para publicar."""
    deadline = time.monotonic() + POLL_MAX_SECONDS
    last = "UNKNOWN"
    while time.monotonic() < deadline:
        data = _get(container_id, token, fields="status_code,status")
        last = data.get("status_code", "UNKNOWN")
        if last == "FINISHED":
            return
        if last == "ERROR":
            raise MetaError(
                f"el contenedor {container_id} fallo al procesarse: {data.get('status')}"
            )
        time.sleep(POLL_INTERVAL)
    raise MetaError(
        f"el contenedor {container_id} sigue en {last} tras {POLL_MAX_SECONDS}s"
    )


def publish_carousel(ig_user_id: str, token: str, creation_id: str) -> str:
    data = _post(f"{ig_user_id}/media_publish", token, creation_id=creation_id)
    return data["id"]


def publish_instagram(
    ig_user_id: str, token: str, image_urls: list[str], caption: str
) -> str:
    """Flujo completo: contenedores hijo -> contenedor padre -> publicar."""
    children = [create_carousel_item(ig_user_id, token, url) for url in image_urls]
    for child in children:
        wait_until_finished(child, token)
    parent = create_carousel(ig_user_id, token, children, caption)
    wait_until_finished(parent, token)
    return publish_carousel(ig_user_id, token, parent)


# --------------------------------------------------------------------------
# Facebook: foto multiple en la Pagina (solo ruta `facebook`)
# --------------------------------------------------------------------------

def upload_unpublished_photo(page_id: str, token: str, image_url: str) -> str:
    data = _post(f"{page_id}/photos", token, url=image_url, published="false")
    return data["id"]


def publish_facebook(
    page_id: str, token: str, image_urls: list[str], message: str
) -> str:
    """Sube las fotos sin publicar y las adjunta a una entrada del feed.

    Ambos pasos van en la misma ejecucion a proposito: Facebook borra las fotos
    no publicadas a las 24 h.
    """
    photo_ids = [upload_unpublished_photo(page_id, token, url) for url in image_urls]
    attached = json.dumps([{"media_fbid": pid} for pid in photo_ids])
    data = _post(f"{page_id}/feed", token, message=message, attached_media=attached)
    return data["id"]
