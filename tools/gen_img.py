#!/usr/bin/env python3
"""Genera una imagen con Gemini (Nano Banana Pro), igual que en el proyecto Kalma.

Uso:
    python gen_img.py <salida.jpg> <prompt.txt> [ref1.jpg ref2.png ...]

- <salida.jpg>  : ruta del fichero de imagen a crear.
- <prompt.txt>  : fichero de texto con el prompt (en `prompts/`).
- ref*          : 0..N imágenes de referencia (lugares y/o personas).

Aspect ratio: por defecto 9:16 (vertical, para vídeo). Cámbialo con la
variable de entorno AR, p.ej.  AR=16:9 python gen_img.py ...

Otras variables de entorno:
- GEMINI_API_KEY : obligatoria. La clave de la API de Gemini.
- COST_LOG : ruta del CSV de costes (por defecto, junto a este script).
- MODEL   : modelo a usar (por defecto gemini-2.5-flash-image).
- TIMEOUT : segundos de espera de la respuesta (por defecto 300). Con
            gemini-3-pro-image-preview y varias imágenes de referencia la
            generación puede pasar de 5 minutos; sube a 900 si da TimeoutError.
"""
import sys, os, json, csv, base64, datetime, urllib.request, urllib.error

# La clave iba escrita aqui dentro hasta el 19 oct 2026. Fuera: este script lo
# llama tambien la generacion automatica de carruseles, y una clave en el codigo
# no puede viajar a un runner.  ->  rota la clave vieja en Google AI Studio.
KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("MODEL", "gemini-2.5-flash-image")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"

# $ por 1M tokens (precio oficial Gemini API). "output" es el precio de los tokens de
# SALIDA (imagen generada); una imagen 1024px = ~1290 tokens = ~0,039 $.
PRICING = {
    "gemini-2.5-flash-image": {"input": 0.30, "output": 30.00},
    "gemini-3-pro-image-preview": {"input": 2.00, "output": 120.00},  # aprox., varía con resolución
}
# Al vivir el script en el repo del motor, por defecto el log cae en el clon y no
# en el vault. COST_LOG permite seguir acumulando el historico de siempre.
COST_LOG = os.environ.get("COST_LOG") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "coste_generacion.csv"
)

if len(sys.argv) < 3:
    print(__doc__); sys.exit(1)

if not KEY:
    sys.exit("falta la variable de entorno GEMINI_API_KEY")

out = sys.argv[1]
prompt = open(sys.argv[2], encoding="utf-8").read()
refs = sys.argv[3:]  # cualquier numero de imagenes de referencia
ar = os.environ.get("AR", "9:16")

parts = [{"text": prompt}]
for ref in refs:
    if ref and os.path.exists(ref):
        mime = "image/png" if ref.lower().endswith(".png") else "image/jpeg"
        b = base64.b64encode(open(ref, "rb").read()).decode()
        parts.append({"inline_data": {"mime_type": mime, "data": b}})
    elif ref:
        print("AVISO: referencia no encontrada:", ref)

body = {
    "contents": [{"parts": parts}],
    "generationConfig": {
        "responseModalities": ["IMAGE"],
        "imageConfig": {"aspectRatio": ar},
    },
}

req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=int(os.environ.get("TIMEOUT", "300")))
    data = json.loads(resp.read())
except urllib.error.HTTPError as e:
    print("HTTP ERROR", e.code, e.read().decode()[:500]); sys.exit(1)

def log_cost(usage):
    price = PRICING.get(MODEL)
    in_tok = usage.get("promptTokenCount", 0)
    out_tok = usage.get("candidatesTokenCount", 0)
    if price:
        cost = in_tok / 1e6 * price["input"] + out_tok / 1e6 * price["output"]
    else:
        cost = None

    new_file = not os.path.exists(COST_LOG)
    with open(COST_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["fecha", "archivo", "modelo", "tokens_in", "tokens_out", "coste_usd"])
        w.writerow([datetime.datetime.now().isoformat(timespec="seconds"), out, MODEL,
                    in_tok, out_tok, f"{cost:.4f}" if cost is not None else "?"])

    total = 0.0
    with open(COST_LOG, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                total += float(row["coste_usd"])
            except ValueError:
                pass

    if cost is not None:
        print(f"COSTE esta imagen: ${cost:.4f}  |  Total acumulado ({os.path.basename(COST_LOG)}): ${total:.4f}")
    else:
        print(f"COSTE desconocido para el modelo {MODEL} (sin tarifa configurada). Tokens in/out: {in_tok}/{out_tok}")


try:
    for p in data["candidates"][0]["content"]["parts"]:
        blob = p.get("inlineData") or p.get("inline_data")
        if blob:
            img = base64.b64decode(blob["data"])
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            open(out, "wb").write(img)
            print("OK", out, len(img), "bytes")
            log_cost(data.get("usageMetadata", {}))
            sys.exit(0)
    print("NO IMAGE IN RESPONSE", json.dumps(data)[:600])
except Exception as ex:
    print("PARSE ERR", ex, json.dumps(data)[:600]); sys.exit(1)
