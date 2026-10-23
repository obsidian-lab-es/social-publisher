# social-publisher

Publicación automática de carruseles en Instagram y en la Página de Facebook
vinculada, a partir de un calendario en Markdown. Multi-tenant: un motor, N
clientes, sin que los datos de ninguno vivan en este repositorio.

Usa la **Graph API oficial de Meta**. No hay automatización de navegador ni
login con usuario y contraseña, que es lo que hace que Instagram bloquee cuentas.

## Dos rutas

Se elige con `META_ROUTE`. Cambia el host, el tipo de token y si se publica o no
en Facebook; los endpoints de publicación en Instagram son idénticos.

| | `instagram` (por defecto) | `facebook` |
|---|---|---|
| Host | `graph.instagram.com` | `graph.facebook.com` |
| Login | Business Login for Instagram | System User del portfolio |
| ¿Página de Facebook? | **No hace falta** | Obligatoria, con *Control total* |
| Publica en | Solo Instagram | Instagram + Página |
| Token | De usuario, **caduca a los 60 días** (renovable) | No caduca |
| `IG_USER_ID` | Opcional (`me` si no se indica) | Obligatorio |

La ruta `instagram` existe porque conseguir *Control total* de una Página ajena
puede ser un trámite imposible: si la creó otra persona y no responde, la ruta
`facebook` se queda bloqueada para siempre. A cambio hay que renovar el token.

## Cómo funciona

```
_calendario.md          →  fila en estado `programado` y vencida
      ↓
validate.py             →  JPEG, ratio 0.8–1.91, 2–10 slides, caption ≤ 2200
      ↓
r2.py                   →  slides a Cloudflare R2 → URLs públicas temporales
      ↓
meta.py                 →  IG: contenedores hijo → carrusel → media_publish
                           FB (solo ruta `facebook`):
                           /photos published=false → /feed attached_media
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

# Renueva el token de la ruta `instagram` y lo escribe en un fichero.
# No lo imprime nunca: iría a parar al log de Actions.
python -m publisher.publish refresh-token --out token.txt
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
| `META_ROUTE` | `instagram` (por defecto) o `facebook` |
| `META_TOKEN` | Token de usuario de Instagram, o de System User en la ruta `facebook` |
| `IG_USER_ID` | ID de la cuenta de Instagram. Opcional en la ruta `instagram` |
| `FB_PAGE_ID` | ID de la Página. **Solo** ruta `facebook` |
| `GH_PAT` | Solo para `mode: refresh` en Actions: PAT con permiso de escritura sobre secrets |
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
- En la ruta `instagram`, el token **caduca a los 60 días**. Solo se puede
  renovar si tiene más de 24 h y no ha caducado todavía; pasados los 60 días sin
  renovar hay que repetir el login a mano. De ahí el cron quincenal de
  `refresh`: tienen que fallar tres renovaciones seguidas, mes y medio sin que
  nadie mire los emails en rojo, para llegar a perder el token.

## `tools/gen_img.py`

Genera una imagen con Gemini a partir de un prompt y N imagenes de referencia.
Vive aqui, y no en el repo de cada cliente, porque es generico: lo que cambia
entre clientes son los prompts y las referencias de marca, que son privados y se
quedan en su repo.

```bash
GEMINI_API_KEY=... AR=4:5 MODEL=gemini-3-pro-image-preview   python tools/gen_img.py salida.jpg prompt.txt logo.png ref1.png ref2.png
```

`COST_LOG` elige donde se acumula el CSV de costes; por defecto, junto al
script. Ninguna clave va escrita en el codigo.

## `tools/gen_slides.py`

Convierte los `prompts/NN-*.txt` de un post en sus `slides/NN.jpg`, llamando a
`gen_img.py` con las referencias que toque para cada slide. Las referencias y el
formato vienen de un JSON del repo del cliente, asi que el script no sabe nada
de ninguna marca.

```bash
python tools/gen_slides.py --post 2026-10-O-tema --refs assets/referencias/mapa.json
```

No regenera una slide que ya existe salvo con `--force`: generar cuesta dinero y
la revision manual puede haber aprobado ya el resto del carrusel. `--slide 01`
regenera una sola, que es el caso normal cuando sale con una errata.

## `inbox/` — capa de interacción

Responder DMs y comentarios, con **aprobación humana obligatoria**. Módulo
aparte de `publisher/` porque publicar y conversar no se parecen: publicar es un
cron semanal e idempotente, conversar tiene ventanas de horas y un error se lee
como si lo hubiera escrito el cliente.

```bash
# Comprueba las reglas de ventana y la cola. Sin red, sin token.
python -m inbox.cli selftest

# Prueba el flujo entero con datos simulados, sin cuenta de Meta.
python -m inbox.cli pull --demo --bandeja _bandeja.md --playbook respuestas.md

# Verifica que el token trae manage_messages, manage_comments y manage_insights.
python -m inbox.cli check

# Lee DMs y comentarios nuevos y escribe borradores en la bandeja.
python -m inbox.cli pull

# Envía SOLO lo que una persona ha marcado como `aprobado`.
python -m inbox.cli send --dry-run
```

**El bot redacta, la persona aprueba.** `pull` escribe bloques en `_bandeja.md`
con estado `pendiente`. Nada sale hasta que alguien lo cambia a `aprobado` a
mano, igual que el calendario solo publica filas en `programado`. Es el mismo
invariante y por la misma razón: un fichero mal editado no puede disparar
mensajes en la cuenta de un cliente.

**Borradores por plantilla, no por modelo.** Salen de `respuestas.md`, que
escribe el cliente. En inmobiliaria «sí, sigue disponible» dicho por un bot es
una promesa que alguien tiene que sostener, y un modelo acierta *casi* siempre.
Lo que no encaja en ninguna entrada se marca `[ESCRIBIR A MANO]` y `send` se
niega a enviarlo aunque esté aprobado. El hueco para un modelo está pensado para
después: reformular una plantilla ya aprobada, no elegir qué se dice.

**La ventana se comprueba dos veces**, en `pull` y otra vez en `send`: entre
redactar y aprobar pueden pasar días, y enviar fuera de plazo es un error de la
API, no un mensaje que llega tarde.

**Comentario → DM.** Para convertir una respuesta pública en mensaje privado,
se cambia `Tipo: comment` por `Tipo: private_reply` en la bandeja. Es la única
vía legítima de iniciar un DM, y el libro de envíos (`_bandeja.log`) es lo único
que recuerda que ya se gastó: Meta permite **una sola** por comentarista y no
avisa.

## Límites de Meta en mensajes y comentarios

Verificados en la documentación oficial el 23 oct 2026:

- **La conversación la inicia siempre el usuario.** No existe endpoint para
  escribir primero a un seguidor nuevo. El *Follow to DM* de ManyChat es una
  capacidad de partner en beta que Meta concede cuenta por cuenta; no está en la
  API pública.
- **Ventana de 24 h** para responder un DM, ampliable a **7 días** con la
  etiqueta `HUMAN_AGENT`, que es la que usa este módulo: el ciclo de aprobación
  humana no siempre cabe en un día.
- **Respuesta privada a un comentario: 7 días** y **un único mensaje por
  comentarista**. En un Live, solo durante la emisión.
- **Seguimiento** tras una respuesta privada: solo si la persona contesta, y
  dentro de las 24 h siguientes.
- **Cuentas de terceros exigen Advanced Access**, que es App Review +
  Verificación de negocio. Y si el revisor no puede probar la app (sin interfaz,
  sin *Facebook Login for Business*) solo aprueban `instagram_basic` e
  `instagram_manage_comments`: **sin mensajes**. Automatizar DMs para clientes
  que no son propios necesita, por tanto, una interfaz mínima revisable.

## Licencia

MIT
