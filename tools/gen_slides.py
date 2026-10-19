#!/usr/bin/env python3
"""Genera las slides de un post a partir de sus prompts.

Es el puente entre las dos mitades de la generacion de carruseles: la mitad
creativa deja escritos `prompts/NN-*.txt`, y esto los convierte en
`slides/NN.jpg` llamando a `gen_img.py` con las referencias que toquen.

    python tools/gen_slides.py --post 2026-10-O-tema --refs assets/referencias/mapa.json

Vive en el motor y no en el repo de cada cliente porque lo unico especifico de
cada marca son las referencias y el formato, y eso viene en el JSON. Para un
cliente nuevo se escribe otro mapa y este script no se toca.

El mapa define, por numero de slide, las referencias EN ORDEN. El orden importa:
el modelo le da mas peso a las primeras, y las plantillas de prompt hablan de
"la segunda imagen adjunta" y "la TERCERA imagen adjunta", asi que cambiarlo
rompe los prompts en silencio.

    {
      "aspect_ratio": "4:5",
      "modelo": "gemini-3-pro-image-preview",
      "referencias": {
        "01": ["assets/logo.png", "assets/ref-portada.png", "assets/ref-letra.png"],
        "02": ["assets/logo.png", "assets/ref-desarrollo.png", "assets/ref-letra.png"]
      }
    }

Por defecto NO regenera una slide que ya existe: generar cuesta dinero y la
revision manual puede haber aprobado ya las otras tres. Con `--slide 01` se
regenera una sola, que es el caso normal cuando una sale con una errata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
GEN_IMG = AQUI / "gen_img.py"

# `01-portada.txt` -> `01`. Lo que va detras del numero es para que un humano
# sepa que es cada fichero; el script solo necesita el numero.
PROMPT = re.compile(r"^(\d{2})\b.*\.txt$")


def fallar(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def cargar_mapa(path: Path) -> dict:
    try:
        mapa = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fallar(f"no existe el mapa de referencias {path}")
    except json.JSONDecodeError as exc:
        fallar(f"{path} no es JSON valido: {exc}")
    if "referencias" not in mapa:
        fallar(f"{path} no tiene la clave 'referencias'")
    return mapa


def prompts_del_post(post: Path) -> dict[str, Path]:
    carpeta = post / "prompts"
    if not carpeta.is_dir():
        fallar(f"{post.name} no tiene carpeta prompts/")
    encontrados: dict[str, Path] = {}
    for f in sorted(carpeta.iterdir()):
        m = PROMPT.match(f.name)
        if not m:
            continue
        num = m.group(1)
        if num in encontrados:
            fallar(f"dos prompts con el numero {num}: {encontrados[num].name} y {f.name}")
        encontrados[num] = f
    if not encontrados:
        fallar(f"{post.name}/prompts/ no tiene ningun NN-*.txt")
    return encontrados


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--post", required=True, help="carpeta del post")
    p.add_argument("--refs", required=True, help="JSON con el mapa de referencias")
    p.add_argument("--slide", help="generar solo esta slide (p.ej. 01)")
    p.add_argument("--force", action="store_true", help="regenerar aunque la slide ya exista")
    p.add_argument("--dry-run", action="store_true", help="imprime lo que haria, sin llamar a Gemini")
    args = p.parse_args()

    if not GEN_IMG.is_file():
        fallar(f"no encuentro {GEN_IMG}")
    if not args.dry_run and not os.environ.get("GEMINI_API_KEY"):
        fallar("falta la variable de entorno GEMINI_API_KEY")

    post = Path(args.post)
    if not post.is_dir():
        fallar(f"no existe la carpeta del post {post}")

    mapa = cargar_mapa(Path(args.refs))
    ar = mapa.get("aspect_ratio", "4:5")
    modelo = mapa.get("modelo", "gemini-3-pro-image-preview")
    referencias = mapa["referencias"]

    prompts = prompts_del_post(post)
    if args.slide:
        if args.slide not in prompts:
            fallar(f"no hay prompt para la slide {args.slide}; hay: {', '.join(sorted(prompts))}")
        prompts = {args.slide: prompts[args.slide]}

    (post / "slides").mkdir(exist_ok=True)

    hechas, saltadas, fallidas = [], [], []
    for num, prompt in sorted(prompts.items()):
        destino = post / "slides" / f"{num}.jpg"
        if destino.exists() and not args.force:
            print(f"[SKIP] {destino} ya existe (--force para regenerar)")
            saltadas.append(num)
            continue

        refs = referencias.get(num)
        if refs is None:
            fallar(f"el mapa no dice que referencias usar para la slide {num}")
        faltan = [r for r in refs if not Path(r).is_file()]
        if faltan:
            fallar(f"referencias que no existen para la slide {num}: {', '.join(faltan)}")

        cmd = [sys.executable, str(GEN_IMG), str(destino), str(prompt), *refs]
        print(f"[GEN]  slide {num}  <- {prompt.name}  ({len(refs)} referencias, {ar}, {modelo})")
        if args.dry_run:
            print("       " + " ".join(cmd))
            hechas.append(num)
            continue

        entorno = {**os.environ, "AR": ar, "MODEL": modelo}
        res = subprocess.run(cmd, env=entorno)
        if res.returncode != 0 or not destino.exists():
            print(f"[FAIL] la slide {num} no se genero", file=sys.stderr)
            fallidas.append(num)
        else:
            hechas.append(num)

    print(
        f"\nGeneradas: {len(hechas)}  |  ya existian: {len(saltadas)}  |  fallidas: {len(fallidas)}"
    )
    if fallidas:
        # Un carrusel al que le falta una slide no se puede publicar, asi que
        # esto tiene que salir en rojo aunque las otras tres hayan salido bien.
        print(f"[FAIL] slides sin generar: {', '.join(fallidas)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
