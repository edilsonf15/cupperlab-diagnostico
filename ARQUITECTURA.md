# Cómo funciona TODO — Motor de diagnóstico SEO + GEO de Cupperlab

Documento técnico completo para dar contexto a Cowork (o a cualquiera que toque el motor).
Complementa a `PROMPTS.md` (que detalla solo los prompts de IA).

- **App:** FastAPI + Jinja2 + JS vanilla. Python 3.
- **Navegador headless:** Playwright/Chromium (render de JS, scraping de Maps, PDF).
- **Endpoint público:** `analisis.cupperlab.com` · **Repo:** `edilsonf15/cupperlab-diagnostico`
- **Integrado en el CMS:** `cupperlab.com/diagnostico` (iframe/bloque nativo).

---

## 1. Visión general del flujo

```
Usuario mete su web  →  POST /api/analyze  →  crea job_id, corre _run_job en 2º plano
                                                   │
   El front hace polling a GET /api/status/{job_id}  (barra de progreso)
                                                   │
   _run_job orquesta TODO en paralelo y va marcando progreso (_set):
     1) analyzer.analyze()      → home + render + país + señales técnicas
     2) onpage.audit()          → rastreo multipágina (~70 URLs)
     3) perf2.measure()         → velocidad (PageSpeed API, móvil+escritorio)
     4) geo_ai.run_ai_geo_fast()→ IA: marca, competencia, ficha, contenido
     5) gbp / Places API        → enriquece la ficha de Google
     6) search.check_google/…   → posición y competencia en buscador
     7) dims.compute()          → las 9 dimensiones (0-100 c/u)
     8) findings.compute()      → hallazgos agrupados
     9) report_pdf + emailer    → PDF y correo con el informe
   →  result guardado en el job (y en caché por dominio 30 min)
```

Cuando `done=True`, el front pinta el resultado (las 9 dimensiones, la matriz de IA, los hallazgos) y se envía el PDF por correo.

---

## 2. Módulo por módulo (todo está en `app/`)

### `main.py` (564 líneas) — Orquestador y API
- **Rutas:** `/` (form), `/agenda` (reserva), `POST /api/book`, `POST /api/analyze`, `GET /api/status/{job_id}`, `GET /reporte/{token}.pdf`, `GET /salud`.
- **`_run_job`**: corre el pipeline completo (§1). Lanza tareas en paralelo (perf, gbp scrape, onpage) y las va uniendo.
- **Caché de resultados** (`_RESULT_CACHE`, `_cache_key`): 30 min por dominio+idioma; se limpia al reiniciar el contenedor (deploy).
- **Rate limit** por IP (`_rate_ok`, `RATE_LIMIT_PER_HOUR`, def. 30/h).
- **`_embed_headers`**: quita `X-Frame-Options` para poder incrustar en el CMS (iframe).
- **Resolución de la ficha de Google (paso 5):** la IA es primaria; Places API/scraping solo confirman o añaden nº exacto de reseñas (ver `PROMPTS.md §2`).

### `analyzer.py` (1564 líneas) — Lectura de la home + técnico
- **`analyze(url)`** → objeto `Result`. Descarga la home (`fetch_home`, reintenta https antes de caer a http, UA de navegador real, `verify=False`), la **re-renderiza con Playwright** (`perf.render_html`) para ejecutar JS, y fusiona ambas versiones.
- **`parse_home`**: título, metas, OG/Twitter, H1/H2, canonical, robots meta, favicon, viewport, **schema JSON-LD** (captura `@type` string y array), **FAQ** (schema o ≥3 preguntas), **NAP** (dirección/teléfono con contexto), **mapa** (solo iframe), hreflang, idioma.
- **`detect_country`**: país por contenido (prefijo telefónico, moneda, menciones, idioma-región) y por **TLD** (`.co`→Colombia, etc.). Si la web está bloqueada, no deduce país de la página de reto.
- **Detección de bloqueo (`meta["blocked"]`)**: si la home (o el render) es una página de reto de Cloudflare/WAF, se marca. La IA igual analiza por nombre (ver `geo_ai`).
- **SSL, robots.txt, sitemap.xml, llms.txt, compresión, CDN, HTTPS forzado, variantes www**.
- **Muestreo de enlaces rotos (404)** desde el sitemap.
- **`apply_ai_to_result`**: integra la respuesta de la IA en el result (corrige país, añade hallazgos).

### `onpage.py` (831 líneas) — Auditoría SEO multipágina
- **`audit(url)`**: rastrea hasta ~70 páginas (sitemap + BFS de enlaces internos, `SITEMAP_CAP`).
- **`_parse_page`**: por cada página → título/desc (longitudes), H1/H2, canonical, noindex, imágenes sin alt, word count, schema, URL amigable, anchor pobre, breadcrumbs, **señales locales** (teléfono, dirección con contexto, mapa iframe, horario, geo, **testimonios**), enlaces internos.
- **`_check_broken`**: estado HTTP real de todas las URLs; **excluye 401/403/405/429/451/503/999** (no son 404 reales).
- **`_aggregate`**: suma todo el sitio → issues (títulos/desc faltantes o duplicados, thin content, huérfanas, profundidad, etc.), schema agregado, `local` (señales de contacto), y un **score on-page**.
- **`_content`**: profundidad, casi-duplicados, frescura, coherencia de tema, señales E-E-A-T.

### `perf2.py` (368 líneas) — Velocidad (motor nuevo)
- **`measure(url)`**: llama a **PageSpeed Insights API** (`_psi`, con reintentos) para **móvil y escritorio**. Devuelve score global + Core Web Vitals (LCP, CLS, INP, TTFB) con dato de laboratorio (Lighthouse) y de campo (CrUX).
- **Auditorías de ahorro:** imágenes WebP/AVIF, minificación JS/CSS, lazy-load, scripts de terceros.
- **Sonda de cabeceras propia:** CDN y compresión gzip/brotli (lo que PSI no expone claro).
- `perf.py` (viejo) sigue usándose para `render_html` (Playwright) y `bing_site_search`.

### `geo_ai.py` (1517 líneas) — La parte de IA (GEO/LLMO)
Ver **`PROMPTS.md`** para los prompts. Resumen de funciones:
- **`run_ai_geo_fast(domain, meta, lang)`**: el flujo rápido (1 ronda en paralelo). Deriva marca/servicio/país, lanza `q_mega` + `q_comp` + sondas por motor (`q_mem`, `q_brand`, `q_cat`), y arma: reconocimiento, recomendación, competidores, **ficha de Google**, keywords, entidades, evaluación de contenido, score de IA.
- **`_ask`**: llama al proveedor (OpenAI Responses API con `web_search`, o Gemini con `google_search`, o Anthropic). `temperature=0`. Grounded = con búsqueda web en vivo.
- **`derive_brand`**: marca desde og:site_name / título (casa con el dominio) / dominio. Ignora títulos "basura" (Cloudflare, "Just a moment").
- **`derive_sector` / `short_category`**: categoría corta por palabras clave (para las búsquedas de cliente).
- **`_country_from_domain`**: país por ccTLD.
- **Motores:** `_pick_engines` elige ChatGPT/Gemini según claves disponibles.

### `search.py` (306 líneas) — Buscador y Places API
- **`check_gbp`**: **Places API (New)** por nombre+zona (varias formulaciones). Devuelve nombre, categoría, nº de reseñas, valoración. ⚠️ **HOY devuelve 403: la Places API está DESHABILITADA en el proyecto GCP `907452438504`.**
- **`check_google`**: posición del dominio en las búsquedas de categoría (vía Serper API o DuckDuckGo).
- **`check_indexation`**: cuántas páginas indexa el buscador vs el sitemap; muestrea 404 en lo indexado.

### `gbp.py` (164 líneas) — Ficha de Google por scraping (respaldo)
- **`check_gbp_scrape`**: abre Google Maps con Playwright y busca la ficha por nombre/zona/dominio. Intermitente (Google bloquea con el muro de consentimiento). Solo confirma; la IA es la fuente primaria.

### `dims.py` (231 líneas) — Las 9 dimensiones (0-100)
`compute(data)` calcula 9 scores ponderados, en el mismo orden que la pantalla:
1. **Visibilidad en la IA (GEO)** `_geo` — reconocimiento + recomendación.
2. **Velocidad móvil** / 3. **Velocidad escritorio** (`perf2`).
4. **SEO on-page** (`onpage`).
5. **Contenido** (`_content`).
6. **Salud técnica** `_tech` — SSL, robots, sitemap, 404, indexación.
7. **Datos estructurados (Schema)** `_schema`.
8. **Seguridad** `_security` — HTTPS, cabeceras, cookies, exposiciones.
9. **Presencia local y reputación** `_local` — ficha Google, reseñas/testimonios, NAP, mapa, LocalBusiness, horario, categoría.
Cada dimensión es una lista de señales `ok/warn/bad` con peso → `_score`.

### `findings.py` (595 líneas) — Hallazgos (qué corregir)
`compute(data, lang)` agrupa hallazgos por categoría, mismo orden que la web. Funciones por grupo: `geo_findings`, `perf_findings` (solo si móvil o escritorio <80), `onpage_findings`, `content_findings`, `tech_findings`, `schema_findings`, `local_findings`, `security_findings`. Cada hallazgo: `{sev, t (título), d (detalle)}` bilingüe.

### `report_pdf.py` (1836 líneas) — Informe en PDF
`build_plan` + render HTML→PDF (Playwright). Portada, gauge de salud, 9 dimensiones, matriz de IA (reconocimiento + recomendación + competidores + ejemplo real de búsqueda), hallazgos y plan de acción.

### Otros
- **`emailer.py`**: envía el PDF por SMTP.
- **`booking.py` / `gcal.py`**: reserva de cita (agenda) + Google Calendar.
- **`i18n.py`**: idioma (es/en) por request.
- **`authority.py`**: señales de autoridad de marca.
- **`templates/index.html`**: la UI (form, loader, render de dimensiones y matriz de IA en JS). **Ojo:** el texto de la tarjeta "Presencia local" y algunos hallazgos se generan aquí en JS, además de en `findings.py`/`dims.py` (hay que tocar los dos si se cambia el texto).
- **`static/js/app.js`**: lógica del front (polling, animaciones).

---

## 3. Salidas (dónde se ve el resultado)

El mismo `result` alimenta **3 salidas** (deben coincidir):
1. **Pantalla** — `templates/index.html` + `static/js/app.js` (usa `data.dims` y `data.findings`).
2. **PDF** — `report_pdf.py`.
3. **Correo** — `emailer.py` con el PDF adjunto.

`dims.py` y `findings.py` son la **fuente única** para que las tres coincidan.

---

## 4. APIs externas y claves (variables de entorno en Dokploy)

| Servicio | Para qué | Variable | Estado |
|---|---|---|---|
| **PageSpeed Insights** | Velocidad / CWV | `GOOGLE_PSI_API_KEY` | ✅ Funciona (gratis, cuota alta) |
| **Places API (New)** | Ficha de Google (reseñas/categoría) | `GOOGLE_PLACES_API_KEY` o reusa `GOOGLE_PSI_API_KEY` | ⚠️ **DESHABILITADA** en GCP 907452438504 (403) |
| **OpenAI** | IA GEO (ChatGPT + web_search) | `OPENAI_API_KEY` | ✅ (motor primario) |
| **Gemini** | IA GEO (respaldo) | `GEMINI_API_KEY` | Según clave |
| **Serper** | Posición en Google | `SERPER_API_KEY` / `SEARCH_PROVIDER=serper` | Opcional (si no, DuckDuckGo) |
| **SMTP** | Envío del PDF | vars SMTP | ✅ |

Otras: `RATE_LIMIT_PER_HOUR`, `RESULT_CACHE_TTL` (1800s), `ANALYSIS_HARD_TIMEOUT` (50s), `AI_GEO_BUDGET` (30s), `PUBLIC_BASE_URL`.

---

## 5. Despliegue

- **Diagnóstico** (`edilsonf15/cupperlab-diagnostico`): auto-deploy en push a `main`, pero conviene **deploy manual en Dokploy con "Clean Cache" ON** (evita capa Docker `COPY app/` cacheada con código viejo). Al reiniciar el contenedor se **limpia la caché de resultados**.
  - Dokploy: `http://82.223.116.53:3000` · proyecto `zleVM-vOaCrKOOrVvmah8` · servicio `kmMPXR_o5aigE75B9YuU1`.
- **CMS** (`dardcode/Admin-Cupperlab`): auto-deploy en push a `main`. La página `cupperlab.com/diagnostico` es nativa (bloque + migraciones SQL en `database/migrations/`). Si cambia el menú/caché, se toca `content_entries.updated_at` con una migración (Last-Modified del navegador).

---

## 6. Cómo verificar un cambio (sin abrir la UI)

```bash
# 1) lanzar análisis
curl -s -X POST https://analisis.cupperlab.com/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url":"koaj.co","email":"tucorreo@cupperlab.com","lang":"es"}'
# → {"job_id":"..."}

# 2) leer el resultado (repetir hasta done=true)
curl -s https://analisis.cupperlab.com/api/status/<job_id> | python -m json.tool
# Campos clave: result.geo_ai.{gbp,gbp_reviews_n,competitors,recognition,knows_brand},
#               result.meta.{country,has_address,has_testimonials,blocked},
#               result.dims, result.findings, result.perf2.mobile.score
```

Compilar antes de desplegar: `python -m py_compile app/geo_ai.py app/main.py app/analyzer.py app/onpage.py`.

---

## 7. Problemas conocidos (lo que "sigue mal")

1. **Ficha de Google inconsistente** → Places API deshabilitada; depende de la IA. Fix real: habilitar Places API (New) en GCP.
2. **Competidores con ruido** → dependen del criterio de la IA (ver `PROMPTS.md §6`).
3. **País en `.com` global** → puede fijar el mercado internacional en vez del local.
4. **"Consulta a la IA no completada"** → la llamada grounded a la IA se limita/tarda; falta retry a un segundo motor.
5. **Texto duplicado front/back** → algunos textos viven en `index.html` (JS) y en `findings.py`/`dims.py`; cambiar ambos.

Para mejorar los **prompts**, ver **`PROMPTS.md`** (tiene los 5 prompts verbatim, cómo se combinan y las reglas para no romper el parser).

---

*Este documento y `PROMPTS.md` son el paquete de contexto para iterar el motor con Cowork.*
