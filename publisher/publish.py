"""Orquestador y CLI.

Tres subcomandos, que se corresponden con los pasos de verificacion del plan:

    validate  valida todos los posts en local. No toca la red.
    check     comprueba token, permisos e IDs con llamadas de solo lectura.
    run       publica como mucho un post vencido. Con --dry-run no llama a Meta.

    refresh-token  renueva el token de Instagram (solo ruta `instagram`).

La ruta se elige con `META_ROUTE` (ver `meta.py`). En la ruta `instagram` no se
publica en Facebook y `FB_PAGE_ID` no hace falta.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from . import agenda, meta, validate
from .r2 import R2, R2NotConfigured

# Tope de seguridad: aunque venzan varias filas, cada ejecucion publica una sola.
# Evita que un calendario mal editado dispare una rafaga de posts.
MAX_PER_RUN = 1

# El limite real de Meta son 100 posts/24h. Abortamos mucho antes: a dos posts
# por semana, ver 10 significa que algo va mal, no que haya mucho trabajo.
QUOTA_ABORT_THRESHOLD = 10


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        _fail(f"falta la variable de entorno {name}")
    return value


def _ig_id() -> str:
    """ID de la cuenta de Instagram.

    En la ruta `instagram` el token ya identifica la cuenta, asi que `me` vale y
    `IG_USER_ID` pasa a ser opcional: un ID menos que copiar y que equivocar.
    """
    value = (os.environ.get("IG_USER_ID") or "").strip()
    if value:
        return value
    if meta.PUBLISHES_TO_FACEBOOK:
        _fail("falta la variable de entorno IG_USER_ID")
    return "me"


def _looks_like_post(path: Path) -> bool:
    """Distingue una carpeta de post de vecinas como `assets/` o `_borradores/`."""
    if not path.is_dir() or path.name.startswith((".", "_")):
        return False
    return (path / "slides").is_dir() or (path / "descripcion.md").is_file()


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    posts_dir = Path(args.posts_dir)
    if not posts_dir.is_dir():
        _fail(f"no existe {posts_dir}")

    post_dirs = sorted(p for p in posts_dir.iterdir() if _looks_like_post(p))
    if not post_dirs:
        _fail(f"no hay carpetas de post en {posts_dir}")

    bad = 0
    for post in post_dirs:
        errors = validate.validate_post(post)
        if errors:
            bad += 1
            print(f"[FAIL] {post.name}")
            for err in errors:
                print(f"       - {err}")
        else:
            slides = validate.slides_of(post)
            print(f"[OK]   {post.name} ({len(slides)} slides)")

    print(f"\n{len(post_dirs) - bad}/{len(post_dirs)} posts validos")
    return 1 if bad else 0


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    token = _env("META_TOKEN")
    ig_id = _ig_id()

    print(f"Ruta: {meta.ROUTE}  ({meta.BASE})")
    try:
        info = meta.account_info(ig_id, token)
        print(f"[OK]   Instagram: @{info.get('username')} ({info.get('name', '')})".rstrip())
        print(f"[OK]   Tipo de cuenta: {info.get('account_type', 'no expuesto en esta ruta')}")
        used = meta.publishing_limit(ig_id, token)
        print(f"[OK]   Cuota usada en 24h: {used}/100")
        if meta.PUBLISHES_TO_FACEBOOK:
            page = meta.page_info(_env("FB_PAGE_ID"), token)
            print(f"[OK]   Pagina de Facebook: {page.get('name')}")
        else:
            print("[INFO] Facebook desactivado en esta ruta (no hace falta Pagina)")
    except meta.MetaError as exc:
        _fail(str(exc))

    try:
        r2 = R2.from_env()
        print(f"[OK]   R2 configurado: bucket={r2.bucket} base={r2.public_base}")
    except R2NotConfigured as exc:
        print(f"[WARN] R2 sin configurar: {exc}")

    return 0


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    calendar_path = Path(args.calendar)
    posts_dir = Path(args.posts_dir)
    dry_run = args.dry_run

    if not calendar_path.is_file():
        _fail(f"no existe el calendario {calendar_path}")

    now = datetime.now(agenda.MADRID)
    rows = agenda.parse(calendar_path)
    due = agenda.due_rows(rows, now)

    print(f"Ahora: {now:%Y-%m-%d %H:%M %Z}")
    print(f"Filas en el calendario: {len(rows)} | vencidas y en '{agenda.PUBLISHABLE}': {len(due)}")

    if not due:
        print("Nada que publicar. Salida limpia.")
        return 0

    row = due[0]
    if len(due) > MAX_PER_RUN:
        skipped = ", ".join(r.ref for r in due[MAX_PER_RUN:])
        print(f"[WARN] {len(due)} filas vencidas; publico solo {row.ref}. Pendientes: {skipped}")

    print(f"Objetivo: {row}")
    post_dir = posts_dir / row.slug

    errors = validate.validate_post(post_dir)
    if errors:
        for err in errors:
            print(f"       - {err}", file=sys.stderr)
        _fail(f"{row.slug} no pasa la validacion; no se llama a Meta")

    slides = validate.slides_of(post_dir)
    caption = validate.read_caption(post_dir)
    print(f"Slides: {', '.join(s.name for s in slides)}")
    print(f"Caption: {len(caption)} caracteres")

    # --- subida a R2 ---------------------------------------------------
    try:
        r2 = R2.from_env()
        urls = r2.upload_slides(args.client_prefix, row.slug, slides)
        print("URLs publicas:")
        for url in urls:
            print(f"       {url}")
    except R2NotConfigured as exc:
        if not dry_run:
            _fail(f"R2 es obligatorio para publicar: {exc}")
        urls = [f"https://EJEMPLO/{args.client_prefix}/{row.slug}/{s.name}" for s in slides]
        print(f"[WARN] R2 sin configurar ({exc}); uso URLs de ejemplo para el dry-run")

    if dry_run:
        print("\n--- DRY RUN: no se llama a Meta ---")
        print(f"IG  POST /{{ig}}/media  x{len(urls)}  is_carousel_item=true")
        print(f"IG  POST /{{ig}}/media  media_type=CAROUSEL children={len(urls)}")
        print("IG  POST /{ig}/media_publish")
        if meta.PUBLISHES_TO_FACEBOOK:
            print(f"FB  POST /{{page}}/photos published=false  x{len(urls)}")
            print("FB  POST /{page}/feed  attached_media")
        print("\n--- Caption tal cual se enviaria ---")
        print(caption)
        return 0

    # --- publicacion real ----------------------------------------------
    token = _env("META_TOKEN")
    ig_id = _ig_id()
    page_id = _env("FB_PAGE_ID") if meta.PUBLISHES_TO_FACEBOOK else None

    try:
        used = meta.publishing_limit(ig_id, token)
        print(f"Cuota usada en 24h: {used}/100")
        if used >= QUOTA_ABORT_THRESHOLD:
            _fail(
                f"cuota anormalmente alta ({used}); aborto por precaucion. "
                "Revisa si algo mas esta publicando."
            )

        ig_media_id = meta.publish_instagram(ig_id, token, urls, caption)
        print(f"[OK]   Instagram publicado: {ig_media_id}")

        # Si Facebook falla, Instagram ya esta publicado. Se registra igual para
        # no perder el ID y que un reintento no duplique el post de Instagram.
        if page_id:
            try:
                fb_post_id = meta.publish_facebook(page_id, token, urls, caption)
                print(f"[OK]   Facebook publicado: {fb_post_id}")
            except meta.MetaError as exc:
                fb_post_id = "ERROR"
                print(f"[WARN] Instagram OK pero Facebook fallo: {exc}", file=sys.stderr)
        else:
            fb_post_id = None
    except meta.MetaError as exc:
        _fail(str(exc))

    agenda.mark_published(calendar_path, row, ig_media_id, fb_post_id)
    agenda.append_log(calendar_path.parent / "_publicados.log", row, ig_media_id, fb_post_id)
    print(f"Calendario actualizado: {row.ref} -> {agenda.PUBLISHED}")

    return 1 if fb_post_id == "ERROR" else 0


# --------------------------------------------------------------------------
# refresh-token
# --------------------------------------------------------------------------

def cmd_refresh_token(args: argparse.Namespace) -> int:
    """Renueva el token de Instagram y lo escribe en un fichero.

    Nunca se imprime por pantalla: la salida de Actions queda guardada y un
    token con permiso de publicacion en la cuenta del cliente no puede acabar
    en un log. El workflow lee el fichero y lo mete en el secret.
    """
    if meta.PUBLISHES_TO_FACEBOOK:
        _fail("la ruta `facebook` usa token de System User, que no caduca: nada que renovar")

    token = _env("META_TOKEN")
    try:
        data = meta.refresh_long_lived_token(token)
    except meta.MetaError as exc:
        _fail(f"no se pudo renovar el token: {exc}")

    new_token = data.get("access_token")
    if not new_token:
        _fail(f"Meta no devolvio access_token (respuesta: {sorted(data)})")

    days = int(data.get("expires_in", 0)) // 86400
    out = Path(args.out)
    out.write_text(new_token, encoding="utf-8")
    print(f"[OK]   token renovado, caduca en {days} dias")
    print(f"[OK]   escrito en {out}")
    if days < 10:
        print("[WARN] menos de 10 dias de margen; revisa que el cron se este ejecutando")
    return 0


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publisher", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="valida todos los posts en local")
    p_val.add_argument("--posts-dir", default="posts")
    p_val.set_defaults(func=cmd_validate)

    p_chk = sub.add_parser("check", help="comprueba token, permisos e IDs (solo lectura)")
    p_chk.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run", help="publica como mucho un post vencido")
    p_run.add_argument("--calendar", default="_calendario.md")
    p_run.add_argument("--posts-dir", default="posts")
    p_run.add_argument("--client-prefix", default="cliente")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_tok = sub.add_parser(
        "refresh-token", help="renueva el token de Instagram (ruta instagram)"
    )
    p_tok.add_argument(
        "--out", required=True, help="fichero donde escribir el token nuevo"
    )
    p_tok.set_defaults(func=cmd_refresh_token)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
