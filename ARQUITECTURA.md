# Cómo funciona TODO — Motor de diagnóstico SEO + GEO de Cupperlab (v2)

Documento técnico para quien toque el motor (Cowork, Claude Code o una persona).
Complementa a `PROMPTS.md` (prompts de IA) y `bench/` (banco de pruebas).

- **App:** FastAPI + Jinja2 + JS vanilla. Python 3.12 (imagen Playwright).
- **Endpoint público:** `analisis.cupperlab.com` · **Repo:** `edilsonf15/cupperlab-diagnostico`
- **Integrado en el CMS:** `cupperlab.com/diagnostico` (iframe). El contrato de la API no cambió.

---

## 1. Principios v2 (lo que arregla los fallos de v1)

1. **Hechos medidos vs. opinión de la IA.** Ficha de Google, reseñas, categoría, ciudad, país, posición en Google, indexación, velocidad y schema salen de scripts contra fuentes oficiales, con `status`/`source`. La IA solo responde a *"¿qué contesta ChatGPT/Perplexity cuando un cliente pregunta por este servicio?"*.
2. **Nada desaparece en silencio.** Cada módulo devuelve `status: ok|partial|failed|skipped` y una `note`. Lo que no se pudo medir va a `result.not_measured[{key,name,note}]`; la pantalla y el PDF lo pintan en gris como "No medido (motivo)" y no entra en el índice.
3. **Un LLM nunca pisa un dato medido.** El país lo decide crawl + Places; la IA no lo toca.
4. **Presupuesto de tiempo por etapa y global** (`asyncio.wait_for`). Tope 120 s. El resultado sale con lo que haya.
5. **Caché 24 h** por dominio + idioma + `ENGINE_VERSION`, en SQLite (sobrevive deploys). Mismo dominio = 0 € de IA y APIs.
6. **Banco de pruebas** (`bench/`) con dominios de verdad conocida. Se corre antes de desplegar cambios de motor o prompts.

---

## 2. Flujo de un análisis

```
POST /api/analyze ──► store.job_create (SQLite) ──► _run_job
                                                     │  caché 24h? ──► resultado + PDF/correo
                                                     ▼
                                 _pipeline (tope global ANALYSIS_HARD_TIMEOUT=120 s)
  t=0   analyzer.analyze()           home + render JS + país por evidencia + técnico     ≤50 s
  t≈8   ┌ perf2.measure()            PageSpeed móvil+escritorio (2 intentos, ≤60 s c/u)   ┐ en
        ├ perf.measure_device()      analítica/píxeles con navegador                       │ paralelo
        └ onpage.audit()             rastreo ≤60 páginas, 404, contenido, señales locales ┘ ≤65 s
  t≈10  places.resolve()             ficha + categoría + ciudad + país (Places API)      ≤10 s
        _scope()                     ámbito ciudad|pais (ficha con dirección vs ecommerce)
  t≈12  geo_ai.run_geo()             P1 → P2 (motores × 3 búsquedas) + P4 → P3           ≤70 s
  t≈50  serp.run()                   Google real: marca, 3 búsquedas, site: (1 petición) ≤20 s
        serp.find_domains()          dominios de competidores citados por la IA sin web
  ...   recoge perf / analytics / onpage
        finalize_score → dims.compute → not_measured → findings.compute
        store.cache_put + store.job_finish  ──► pantalla (done=true)
        _build_and_send: PDF (2 intentos) → correo (3 intentos) → lead (SQLite + JSONL + aviso)
```

---

## 3. Módulos (`app/`)

| Módulo | Qué hace | Fuente | Estado sin clave |
|---|---|---|---|
| `main.py` | Orquestador, rutas, presupuestos, `not_measured`, PDF/correo | — | — |
| `store.py` | SQLite: jobs, caché 24 h, rate-limit, leads, bench_runs | `data/app.db` | — |
| `analyzer.py` | Home + render + país por evidencia (teléfono, moneda, hreflang, ccTLD) + SSL/robots/sitemap/seguridad | crawl | — |
| `onpage.py` | Rastreo multipágina, títulos/metas/H1, 404 (con tope), contenido, señales locales | crawl | — |
| `perf2.py` | Core Web Vitals móvil/escritorio + auditorías + CDN/compresión. Devuelve `status` y `notes` | PageSpeed API | `not_measured` |
| `places.py` | **Ficha de Google** (solo si `websiteUri` casa con el dominio, o nombre+ciudad), reseñas, valoración, `primaryType` → categoría de cliente, ciudad, país | Places API (New) | `gbp=None` + `not_measured` |
| `serp.py` | Posición de marca, top 5 para las búsquedas de cliente, `site:` + 404 sobre lo indexado, dominios de competidores. **1 petición batch** | Serper (Google) | `google=None`, `indexation=None` + `not_measured` |
| `geo_ai.py` | Test GEO: P1 búsquedas de cliente, P2 pregunta real por motor, P3 extracción JSON, P4 reconocimiento verificado. `share_of_voice`, `by_engine`, competidores con `cited_by` | OpenAI / Perplexity / Gemini | `status=skipped` + `not_measured` |
| `dims.py` | 9 dimensiones 0-100. GEO solo puntúa si algún motor respondió | local | — |
| `findings.py` | Hallazgos por grupo; GEO incluye "Cómo lo medimos" (motores + búsquedas) | local | — |
| `report_pdf.py` | PDF (Playwright). Lista `not_measured`; competidores con quién los cita | — | — |
| `emailer.py`, `booking.py`, `gcal.py`, `i18n.py`, `authority.py`, `perf.py` (render + analítica) | sin cambios | | |

Eliminados en v2: `gbp.py` (scraping de Maps, bloqueado), `search.py` (DuckDuckGo, bloqueado), `run_ai_geo`/`run_ai_geo_fast` y sus parsers, `index_v1_backup.html`.

---

## 4. Objeto `result` (claves nuevas y las que leen pantalla/PDF/correo)

- `identity` = `{brand, domain, city, country, cc, gl, scope, category, snippet, places_found}` (lo medido antes de preguntar a la IA).
- `places` = contrato `{status, source, note, data{found, name, category_type, category_label, address, city, country_code, reviews, rating, maps_url, website, matched_by}}`.
- `serp` = `{status, source, note}`; `google` e `indexation` mantienen el shape legacy que lee el PDF.
- `perf2` = `{status, score, mobile|None, desktop|None, infra, notes{mobile, desktop}}`; `psi_full` legacy para PDF/score.
- `geo_ai` = claves legacy (`available, limited, recognition, knows, knows_brand, recommended, reco_hits, reco_total, questions[{q, appears, named, answer, by_engine}], competitors[{name, domain, cited_by, hits, source}], sources[{domain, url, own}], gbp, gbp_reviews_n, gbp_rating, gbp_category, ai_score, engines[], answered_names, brand, sector, zona, country, category_queries`) + `status, share_of_voice, by_engine, gbp_source, gbp_maps_url, prompts_version, debug`.
- `dims[{key, grp, name, score}]` solo con score numérico; `not_measured[{key, name, note}]` aparte (una dim con `score: null` rompería el PDF).
- `from_cache`, `engine_version`, `elapsed_total`.

Consumidores: `templates/index.html` (recalcula hallazgos en JS y sobreescribe scores con `dims` por `name`; pinta `not_measured`), `report_pdf.py` (dims, findings tech/schema, geo_ai, psi_full, google, indexation, not_measured), `templates/email_report.html` (dims por `key`).

---

## 5. Variables de entorno (Dokploy)

```
GOOGLE_PSI_API_KEY=        # velocidad (gratis)
GOOGLE_PLACES_API_KEY=     # ficha/categoría/ciudad. Habilitar "Places API (New)" en GCP 907452438504
SERPER_API_KEY=            # Google real. serper.dev (2.500 gratis, ~1 $/1.000)
OPENAI_API_KEY=            # P1/P3/P4 (mini) + ChatGPT con web_search (P2)
PERPLEXITY_API_KEY=        # P2 (sonar)
GEMINI_API_KEY=            # opcional, P2 si se añade a AI_ENGINES
AI_ENGINES=openai,perplexity
OPENAI_MODEL=gpt-4o-mini   # revisar el nombre vigente del modelo mini
ANALYSIS_HARD_TIMEOUT=120  STAGE_BUDGET_PSI=60  STAGE_BUDGET_ONPAGE=65  AI_GEO_BUDGET=45
RESULT_CACHE_TTL=86400     ENGINE_VERSION=v2.0  MAX_CONCURRENT_JOBS=3
```

`GET /salud` dice qué integraciones están activas: `{places, serper, ai_engines, psi, engine}`.

Coste estimado por análisis nuevo (2 motores): Places 0,03 € + Serper 0,005 € + OpenAI (4 P2 + 3 mini) ≈ 0,04-0,07 € + Perplexity (3 P2) ≈ 0,02 € → **≈ 0,10-0,13 €**. En caché: 0 €.

---

## 6. Despliegue y verificación

1. Dokploy: deploy manual con **Clean Cache ON** (capa `COPY app/`). El volumen `./data` guarda `app.db`, `reports/`, `leads.jsonl`.
2. Comprobar `GET /salud` → todas las integraciones `true`.
3. Lanzar un análisis y leer el JSON:
```bash
curl -s -X POST https://analisis.cupperlab.com/api/analyze -H "Content-Type: application/json" \
  -d '{"url":"rustikadecoracion.com","email":"tucorreo@cupperlab.com","lang":"es"}'
curl -s https://analisis.cupperlab.com/api/status/<job_id> | python -m json.tool
# Mirar: result.identity, result.not_measured, result.places.status, result.serp.status,
#        result.geo_ai.{status, category_queries, share_of_voice, competitors[*].cited_by}
```
4. Banco de pruebas: `python bench/run.py` (o `--only dominio`, `--no-cache`). Debe decir `TODO OK` antes de desplegar cambios de motor/prompts.
5. Compilar: `python -m py_compile app/*.py`.

---

## 7. Decisiones de diseño (por qué así)

- **Places en vez de IA para la ficha**: un LLM no puede saber si un negocio tiene ficha; con el prompt de v1 ("las marcas conocidas casi siempre tienen") respondía SI por sesgo. Places devuelve el dato y la ficha se acepta solo si su web es la del cliente.
- **Serper en vez de scraping**: DuckDuckGo/Bing/Maps por scraping estaban bloqueados y devolvían `null` en todos los análisis. Google real cuesta 0,005 € por análisis.
- **Pregunta del cliente sin instrucciones**: el test GEO debe medir lo que un cliente vería. Pedir "4-5 empresas con dominio" cambia la respuesta y deja de ser una medición.
- **Ámbito ciudad/país**: una tienda física de Madrid se evalúa en Madrid, no en "España"; un ecommerce nacional, al revés. Lo decide la ficha (dirección) y las señales de ecommerce de la home.
- **`not_measured` aparte de `dims`**: el PDF hace `max()`/`>` sobre los scores; un `null` lo rompía. Y una dimensión que desaparece sin explicación era el "a veces mide la velocidad y a veces no".
- **SQLite y no Redis**: un fichero en el volumen basta para 1-2 workers; cero infraestructura nueva en el VPS.
