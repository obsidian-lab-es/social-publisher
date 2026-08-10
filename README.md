# social-publisher

Publicación automática de carruseles en Instagram y en la Página de Facebook
vinculada, a partir de un calendario en Markdown. Multi-tenant: un motor, N
clientes, sin que los datos de ninguno vivan en este repositorio.

Usa la **Graph API oficial de Meta**. No hay automatización de navegador ni
login con usuario y contraseña, que es lo que hace que Instagram bloquee cuentas.

## Cómo funciona

```
_calendario.md          →  fila en estado `programado` y vencida
      ↓
validate.py             →  JPEG, ratio 0.8–1.91, 2–10 slides, caption ≤ 2200
      ↓
r2.py                   →  slides a Cloudflare R2 → URLs públicas temporales
      ↓
meta.py                 →  IG: contenedores hijo → carrusel → media_publish
                           FB: /photos published=false → /feed attached_media
      ↓
_calendario.md          →  `publicado` + IDs de la API (idempotencia)
```

Cada repo de cliente es privado y solo contiene su calendario, sus copys y sus
slides. Invoca este workflow con `uses:` y le pasa sus propios secrets.

## Decisiones de diseño

**Un post por ejecución.** Aunque venzan varias filas, se publica una sola. Un
calendario mal editado no puede disparar una ráfaga.

**Idempotencia por estado.** El calendario es la única fuente de verdad. En
cuanto una fila pasa a `publicado` deja de ser elegible, así que repetir la
ejecución no duplica nada.

**Se guarda el estado aunque Facebook falle.** Si Instagram publica y Facebook
no, el job sale en rojo pero el commit del calendario se hace igual. Sin eso, el
siguiente intento republicaría en Instagram.

**Validación antes de tocar la red.** Todo lo que Meta rechazaría se detecta en
local. Un rechazo a mitad del flujo deja contenedores de carrusel a medias y es
mucho más difícil de diagnosticar.

**Zonas horarias fuera del cron.** El cron corre en UTC y España cambia entre
UTC+1 y UTC+2. La comparación se hace en el script contra `Europe/Madrid`, así
que el horario de verano deja de ser un caso especial.

**R2 en vez de un repo público.** Meta hace `cURL` de las imágenes, así que
necesitan URL pública. Alojarlas en GitHub obligaría a exponer también los copys
y el calendario del cliente.

## Uso

```bash
# Valida todos los posts. No toca la red.
python -m publisher.publish validate --posts-dir posts

# Comprueba token, permisos e IDs. Solo lectura, no publica.
python -m publisher.publish check

# Publica como mucho un post vencido.
python -m publisher.publish run --client-prefix invin [--dry-run]
```

## Formato del calendario

```markdown
| # | Fecha | Hora | Tema | Carpeta | Estado | IDs |
|---|-------|------|------|---------|--------|-----|
| A | 2026-09-01 | 11:00 | Tema del post | `2026-09-A-tema/` | programado | |
```

`Fecha` en ISO, `Hora` en HH:MM hora de Madrid. Solo se publican las filas en
`programado`. El resto de la tabla puede llevar la prosa que quieras: el parser
ignora todo lo que no sea una fila de 7 celdas con fecha ISO válida.

Cada carpeta de post necesita:

```
2026-09-A-tema/
├── descripcion.md      caption, se envía tal cual (emojis y saltos incluidos)
└── slides/01.jpg …     2–10 JPEG, ratio entre 0.8 y 1.91
```

## Configuración

| Variable | Para qué |
|---|---|
| `META_TOKEN` | Token de System User (no caduca) |
| `IG_USER_ID` | ID de la cuenta profesional de Instagram |
| `FB_PAGE_ID` | ID de la Página de Facebook |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL` | Cloudflare R2 |
| `GRAPH_VERSION` | Opcional, por defecto `v23.0` |

Los secrets van **por repo de cliente**, nunca a nivel de organización: así el
token de un cliente no puede alcanzar la cuenta de otro.

## Límites que impone Meta

- **100 posts por API cada 24 h** por cuenta. Un carrusel cuenta como 1.
- **Solo JPEG**, y ratio entre **0.8 y 1.91** en carruseles de feed (9:16 no vale).
- Las imágenes deben estar en una **URL pública** cuando se crea el contenedor.
- Publicar en Instagram por API **no** hace crosspost a la Página; hay que
  publicar en ambas explícitamente.
- Las **Stories** se pueden publicar por API, pero no se les pueden añadir
  stickers interactivos (encuestas, quiz), así que no están cubiertas aquí.

## Licencia

MIT
