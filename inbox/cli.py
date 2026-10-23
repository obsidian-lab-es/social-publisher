"""Orquestador y CLI de la capa de interaccion.

Cuatro subcomandos, en el mismo orden en que se usan:

    selftest  comprueba las reglas de ventana y la cola. No toca la red ni Meta.
    check     verifica que el token tiene los permisos de mensajes y comentarios.
    pull      lee DMs y comentarios nuevos y escribe borradores en `_bandeja.md`.
    send      envia SOLO lo que una persona ha marcado como `aprobado`.

`pull` y `send` aceptan `--dry-run`, y `pull` acepta `--demo` para trabajar con
datos simulados: el modulo entero se puede probar de punta a punta sin token,
que es lo que permite tenerlo escrito antes de que la cuenta de Meta exista.

Cadencia: esto **no** encaja en un cron mensual como la publicacion. Las
ventanas de Meta son de horas, asi que `pull` quiere varias pasadas al dia y
`send` justo despues de cada una.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from publisher.agenda import MADRID

from . import draft, fixtures, meta, queue, windows


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        _fail(f"falta la variable de entorno {name}")
    return value


def _ig_id() -> str:
    """Igual que en el publisher: en la ruta `instagram` el token ya dice quien es."""
    value = (os.environ.get("IG_USER_ID") or "").strip()
    return value or "me"


def _parse_api_time(value: str) -> datetime:
    """`2026-10-23T10:14:00+0000` -> datetime en hora de Madrid.

    Meta devuelve UTC con offset sin dos puntos, que `fromisoformat` no traga en
    todas las versiones de Python. Se normaliza antes de parsear.
    """
    raw = value.strip()
    if len(raw) >= 5 and (raw[-5] in "+-") and raw[-3] != ":":
        raw = raw[:-2] + ":" + raw[-2:]
    return datetime.fromisoformat(raw).astimezone(MADRID)


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def cmd_selftest(args: argparse.Namespace) -> int:
    """Afirmaciones sobre la logica que no necesita red.

    El repo no usa pytest: se verifica por CLI, igual que `validate` y el
    `--dry-run` del publisher, para que el workflow pueda ejecutarlo sin anadir
    dependencias.
    """
    import tempfile

    now = datetime(2026, 10, 23, 12, 0, tzinfo=MADRID)
    checks: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        checks.append((name, bool(condition)))

    # --- ventanas ------------------------------------------------------
    check(
        "DM de hace 2 h: se puede responder",
        windows.can_reply_dm(now - timedelta(hours=2), now).ok,
    )
    check(
        "DM de hace 30 h: sigue abierto con etiqueta HUMAN_AGENT (7 dias)",
        windows.can_reply_dm(now - timedelta(hours=30), now, human_agent=True).ok,
    )
    check(
        "DM de hace 30 h: cerrado si no se usa la etiqueta (24 h)",
        not windows.can_reply_dm(now - timedelta(hours=30), now, human_agent=False).ok,
    )
    check(
        "DM de hace 8 dias: cerrado incluso con etiqueta",
        not windows.can_reply_dm(now - timedelta(days=8), now).ok,
    )
    check(
        "margen de seguridad: a 10 min del cierre ya no se envia",
        not windows.can_reply_dm(
            now - windows.HUMAN_AGENT_WINDOW + timedelta(minutes=10), now
        ).ok,
    )
    check(
        "respuesta privada a comentario de hace 2 dias: se puede",
        windows.can_private_reply(now - timedelta(days=2), now, already_replied=False).ok,
    )
    check(
        "respuesta privada ya gastada: no se puede",
        not windows.can_private_reply(now - timedelta(days=2), now, already_replied=True).ok,
    )
    check(
        "respuesta privada a comentario de hace 8 dias: fuera de plazo",
        not windows.can_private_reply(now - timedelta(days=8), now, already_replied=False).ok,
    )
    check(
        "comentario de un Live: solo durante la emision",
        not windows.can_private_reply(
            now - timedelta(hours=1), now, already_replied=False, is_live=True
        ).ok,
    )

    # --- cola de aprobacion --------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        bandeja = Path(tmp) / "_bandeja.md"
        ledger = Path(tmp) / "_bandeja.log"

        item = queue.Item(
            id="dm:conv1:msg1",
            kind="dm",
            author="@demo_interesada",
            author_id="17841400000000001",
            received=now - timedelta(hours=3),
            closes=now + timedelta(days=6),
            estado=queue.PENDING,
            said="Hola, me interesa lo del ultimo post",
            draft="Hola! Te cuento por aqui.",
        )
        check("la bandeja escribe un item nuevo", queue.append_items(bandeja, [item]) == 1)
        check("no duplica el mismo item", queue.append_items(bandeja, [item]) == 0)

        parsed = queue.parse(bandeja)
        check("se vuelve a leer exactamente un item", len(parsed) == 1)
        check("conserva el texto del usuario", parsed[0].said.startswith("Hola, me interesa"))
        check("conserva el borrador", parsed[0].draft == "Hola! Te cuento por aqui.")
        check("nace en pendiente", parsed[0].estado == queue.PENDING)
        check("nada aprobado todavia", queue.approved(parsed) == [])

        check(
            "aprobar por ID funciona",
            queue.set_estado(bandeja, item.id, queue.APPROVED),
        )
        check("ahora si esta aprobado", len(queue.approved(queue.parse(bandeja))) == 1)
        check(
            "un ID que no existe no reescribe nada",
            not queue.set_estado(bandeja, "dm:no-existe", queue.SENT),
        )

        # El libro de envios es lo unico que recuerda la respuesta privada gastada.
        check(
            "sin libro: no se ha respondido en privado",
            not queue.already_private_replied(ledger, item.author_id),
        )
        pr = queue.Item(**{**item.__dict__, "kind": "private_reply"})
        queue.append_ledger(ledger, pr, "ok:mid_1")
        check(
            "con libro: ya se gasto la unica respuesta privada",
            queue.already_private_replied(ledger, item.author_id),
        )
        check(
            "author_id vacio se trata como gastado (ante la duda, no)",
            queue.already_private_replied(ledger, ""),
        )

    # --- guion de respuestas -------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        playbook_path = Path(tmp) / "respuestas.md"
        playbook_path.write_text(
            "## Disponibilidad\n"
            "- Palabras: disponible, sigue libre\n\n"
            "**Respuesta:**\n"
            "Hola! Te confirmo disponibilidad hoy mismo.\n\n"
            "## Por defecto\n\n"
            "**Respuesta:**\n"
            "Hola! Gracias por escribir.\n",
            encoding="utf-8",
        )
        entries = draft.load_playbook(playbook_path)
        check("el guion carga dos entradas", len(entries) == 2)

        text, reason = draft.draft_for("sigue DISPONIBLE el piso?", entries)
        check("casa la palabra clave ignorando mayusculas", "disponibilidad" in reason.lower())
        check("la respuesta sale sin marca de mano", draft.NEEDS_HUMAN not in text)

        text, reason = draft.draft_for("cuanto cobrais de comision?", entries)
        check("sin palabra clave cae en por defecto", "defecto" in reason)
        check("y se marca para escribir a mano", draft.NEEDS_HUMAN in text)

        text, reason = draft.draft_for("hola", [])
        check("sin guion, todo a mano", draft.NEEDS_HUMAN in text)

    # --- parseo de fechas de la API ------------------------------------
    check(
        "fecha de Meta sin dos puntos en el offset",
        _parse_api_time("2026-10-23T08:14:00+0000").hour == 10,
    )

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} comprobaciones correctas")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    """Confirma que el token trae los permisos de interaccion.

    Se comprueba cada permiso por separado y con una llamada de solo lectura: si
    falta `manage_messages` queremos saberlo ahora y no cuando haya un DM sin
    contestar.
    """
    token = _env("META_TOKEN")
    ig_id = _ig_id()
    problems = 0

    for label, fn in (
        ("mensajes (manage_messages)", lambda: meta.fetch_conversations(ig_id, token, limit=1)),
        ("publicaciones (basic)", lambda: meta.fetch_media(ig_id, token, limit=1)),
        ("metricas (manage_insights)", lambda: meta.fetch_insights(ig_id, token)),
    ):
        try:
            data = fn()
            print(f"[OK]   {label}: {len(data)} elementos")
        except meta.MetaError as exc:
            problems += 1
            print(f"[FAIL] {label}: {exc}", file=sys.stderr)

    # Los comentarios solo se pueden comprobar si hay alguna publicacion.
    try:
        media = meta.fetch_media(ig_id, token, limit=1)
        if media:
            comments = meta.fetch_comments(media[0]["id"], token, limit=1)
            print(f"[OK]   comentarios (manage_comments): {len(comments)} en el ultimo post")
        else:
            print("[WARN] la cuenta no tiene publicaciones; no se puede probar manage_comments")
    except meta.MetaError as exc:
        problems += 1
        print(f"[FAIL] comentarios (manage_comments): {exc}", file=sys.stderr)

    return 1 if problems else 0


# --------------------------------------------------------------------------
# pull
# --------------------------------------------------------------------------

def _items_from_conversations(
    conversations: list[dict], ig_id: str, now: datetime, entries: list[draft.Entry]
) -> list[queue.Item]:
    """Convierte conversaciones de la API en items pendientes.

    Se ignora la conversacion cuyo ultimo mensaje es nuestro: eso significa que
    ya esta contestada, y volver a encolarla seria escribir dos veces.
    """
    items: list[queue.Item] = []
    for conv in conversations:
        messages = (conv.get("messages") or {}).get("data") or []
        if not messages:
            continue
        # La API los devuelve del mas reciente al mas antiguo.
        last = messages[0]
        sender = (last.get("from") or {})
        if sender.get("id") in ("", None) or sender.get("id") == ig_id:
            continue

        received = _parse_api_time(last.get("created_time", ""))
        verdict = windows.can_reply_dm(received, now)
        text = last.get("message", "") or ""
        body, reason = draft.draft_for(text, entries)

        items.append(
            queue.Item(
                id=f"dm:{conv.get('id')}:{last.get('id')}",
                kind="dm",
                author="@" + str(sender.get("username") or sender.get("id")),
                author_id=str(sender.get("id")),
                received=received,
                closes=verdict.closes_at,
                estado=queue.PENDING,
                said=text,
                draft=body + ("" if verdict.ok else f"\n\n{draft.NEEDS_HUMAN} {verdict}"),
                extra={"motivo": reason},
            )
        )
    return items


def _items_from_comments(
    media: list[dict], token: str, now: datetime, entries: list[draft.Entry], demo: bool
) -> list[queue.Item]:
    items: list[queue.Item] = []
    for post in media:
        comments = (
            fixtures.comments_for(post["id"]) if demo else meta.fetch_comments(post["id"], token)
        )
        for comment in comments:
            author = (comment.get("from") or {})
            received = _parse_api_time(comment.get("timestamp", ""))
            text = comment.get("text", "") or ""
            body, reason = draft.draft_for(text, entries)
            verdict = windows.can_reply_comment(received, now)

            items.append(
                queue.Item(
                    id=f"comment:{comment.get('id')}",
                    kind="comment",
                    author="@" + str(comment.get("username") or author.get("id")),
                    author_id=str(author.get("id") or ""),
                    received=received,
                    closes=verdict.closes_at,
                    estado=queue.PENDING,
                    said=text,
                    draft=body + ("" if verdict.ok else f"\n\n{draft.NEEDS_HUMAN} {verdict}"),
                    extra={"motivo": reason, "post": str(post.get("permalink") or post.get("id"))},
                )
            )
    return items


def cmd_pull(args: argparse.Namespace) -> int:
    bandeja = Path(args.bandeja)
    entries = draft.load_playbook(Path(args.playbook))
    now = datetime.now(MADRID)
    ig_id = _ig_id()

    print(f"Ahora: {now:%Y-%m-%d %H:%M %Z}")
    print(f"Guion de respuestas: {len(entries)} entradas ({args.playbook})")
    if not entries:
        print(f"[WARN] sin guion: todo saldra marcado {draft.NEEDS_HUMAN}")

    if args.demo:
        print("[INFO] modo demo: datos simulados, no se llama a Meta")
        conversations = fixtures.conversations(now)
        media = fixtures.media()
        token = "DEMO"
    else:
        token = _env("META_TOKEN")
        try:
            conversations = meta.fetch_conversations(ig_id, token)
            media = meta.fetch_media(ig_id, token, limit=args.posts)
        except meta.MetaError as exc:
            _fail(str(exc))

    items = _items_from_conversations(conversations, ig_id, now, entries)
    items += _items_from_comments(media, token, now, entries, args.demo)

    nuevos = [i for i in items if i.id not in queue.known_ids(bandeja)]
    print(f"DMs y comentarios leidos: {len(items)} | nuevos: {len(nuevos)}")
    for item in nuevos:
        print(f"       {item}  <- {item.extra.get('motivo', '')}")

    if args.dry_run:
        print("\n--- DRY RUN: no se escribe en la bandeja ---")
        for item in nuevos:
            print(item.render())
        return 0

    written = queue.append_items(bandeja, nuevos)
    print(f"\nEscritos en {bandeja}: {written}")
    if written:
        print("Revisa los borradores y cambia 'Estado: pendiente' por 'aprobado'")
        print("Para contestar por DM a un comentario, cambia 'Tipo: comment' por 'private_reply'")
    return 0


# --------------------------------------------------------------------------
# send
# --------------------------------------------------------------------------

def cmd_send(args: argparse.Namespace) -> int:
    bandeja = Path(args.bandeja)
    ledger = Path(args.ledger)
    now = datetime.now(MADRID)
    ig_id = _ig_id()

    items = queue.approved(queue.parse(bandeja))
    print(f"Aprobados en la bandeja: {len(items)}")
    if not items:
        print("Nada que enviar. Salida limpia.")
        return 0

    batch = items[: queue.MAX_SEND_PER_RUN]
    if len(items) > queue.MAX_SEND_PER_RUN:
        print(
            f"[WARN] {len(items)} aprobados; envio solo {len(batch)} en esta pasada "
            "para no parecer un bot"
        )

    token = None if args.dry_run else _env("META_TOKEN")
    problems = 0

    for item in batch:
        # La ventana se vuelve a comprobar aqui, no solo en `pull`: entre que se
        # redacto el borrador y alguien lo aprobo pueden haber pasado dias.
        if item.kind == "dm":
            verdict = windows.can_reply_dm(item.received, now)
        elif item.kind == "private_reply":
            gastada = queue.already_private_replied(ledger, item.author_id)
            verdict = windows.can_private_reply(item.received, now, already_replied=gastada)
        elif item.kind == "comment":
            verdict = windows.can_reply_comment(item.received, now)
        else:
            verdict = windows.Verdict(False, f"tipo desconocido: {item.kind!r}")

        if not verdict.ok:
            problems += 1
            print(f"[SKIP] {item}: {verdict}")
            if not args.dry_run:
                queue.set_estado(bandeja, item.id, queue.FAILED, note=str(verdict))
            continue

        if not item.draft.strip() or draft.NEEDS_HUMAN in item.draft:
            problems += 1
            print(f"[SKIP] {item}: el borrador sigue marcado {draft.NEEDS_HUMAN}")
            continue

        if args.dry_run:
            print(f"[DRY]  {item.kind} -> {item.author}: {verdict}")
            print(f"       {item.draft.splitlines()[0][:80]}...")
            continue

        try:
            if item.kind == "dm":
                result = meta.send_dm(ig_id, token, item.author_id, item.draft)
            elif item.kind == "private_reply":
                comment_id = item.id.split(":", 1)[1]
                result = meta.private_reply(ig_id, token, comment_id, item.draft)
            else:
                comment_id = item.id.split(":", 1)[1]
                result = meta.reply_comment(comment_id, token, item.draft)
        except meta.MetaError as exc:
            problems += 1
            print(f"[FAIL] {item}: {exc}", file=sys.stderr)
            queue.set_estado(bandeja, item.id, queue.FAILED, note=str(exc)[:200])
            queue.append_ledger(ledger, item, f"error:{exc}"[:200])
            continue

        queue.set_estado(bandeja, item.id, queue.SENT, note=result)
        queue.append_ledger(ledger, item, f"ok:{result}")
        print(f"[OK]   {item.kind} enviado a {item.author} ({result})")

    return 1 if problems else 0


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inbox", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_self = sub.add_parser("selftest", help="comprueba las reglas sin tocar la red")
    p_self.set_defaults(func=cmd_selftest)

    p_chk = sub.add_parser("check", help="verifica los permisos del token")
    p_chk.set_defaults(func=cmd_check)

    p_pull = sub.add_parser("pull", help="lee DMs y comentarios y redacta borradores")
    p_pull.add_argument("--bandeja", default="_bandeja.md")
    p_pull.add_argument("--playbook", default="respuestas.md")
    p_pull.add_argument("--posts", type=int, default=5, help="ultimas publicaciones a revisar")
    p_pull.add_argument("--demo", action="store_true", help="datos simulados, sin token")
    p_pull.add_argument("--dry-run", action="store_true")
    p_pull.set_defaults(func=cmd_pull)

    p_send = sub.add_parser("send", help="envia solo lo aprobado a mano")
    p_send.add_argument("--bandeja", default="_bandeja.md")
    p_send.add_argument("--ledger", default="_bandeja.log")
    p_send.add_argument("--dry-run", action="store_true")
    p_send.set_defaults(func=cmd_send)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
