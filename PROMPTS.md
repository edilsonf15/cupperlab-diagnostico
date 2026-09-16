# Motor de diagnóstico SEO + GEO — Estado del proyecto y prompts de IA

Documento de handoff para mejorar los prompts con Cowork y devolverlos para aplicar.
Todo lo de IA vive en `app/geo_ai.py`. El orquestador es `app/main.py`.

---

## 1. Cómo funciona el motor (pipeline de un análisis)

Al pedir un análisis de un dominio (`POST /api/analyze`), `main.py::_run_job` corre en este orden:

1. **`analyzer.analyze(url)`** — descarga la home (httpx con reintentos + UA real), la re-renderiza con Playwright (JS), detecta país por contenido/TLD, y marca `meta["blocked"]=True` si la web tiene anti-bots (Cloudflare/WAF) y no se pudo leer.
2. **`onpage.audit(url)`** — rastrea hasta ~70 páginas (sitemap + enlaces): títulos, metas, H1/H2, schema, enlaces rotos, dirección (NAP), mapa, testimonios, contenido.
3. **`perf2.measure(url)`** — velocidad real vía **PageSpeed Insights API** (Lighthouse + CrUX), móvil y escritorio. (API gratis, cuota alta — NO es la que falla.)
4. **`geo_ai.run_ai_geo_fast(domain, meta, lang)`** — **la parte de IA** (ver §3). Reconocimiento de marca, recomendación, competidores, ficha de Google, contenido.
5. **Ficha de Google** — se resuelve en `geo_ai` (IA, primaria) y `main.py` la enriquece con Places API/scraping SI están disponibles.
6. **`dims.compute` + `findings.compute`** — convierten todo en las 9 dimensiones y los hallazgos (misma fuente para pantalla, PDF y correo).

**Determinismo:** todas las llamadas de IA usan `temperature=0`. Mismo input → misma salida.

---

## 2. Estado actual (qué funciona y qué es frágil)

### Funciona bien
- Velocidad (PageSpeed), SEO on-page, técnico, schema, seguridad, contenido, testimonios, dirección (NAP), mapa.
- Marca, país y categoría cuando la web es legible.
- Reconocimiento de marca por IA (knows_brand / recognition).

### Frágil / dependiente de la IA (lo que "sigue mal")
- **Ficha de Google (gbp):** la **Places API (New) está DESHABILITADA** en el proyecto GCP `907452438504` (devuelve 403). Por eso la ficha la resuelve la **IA** (a veces inconsistente: una marca sale SÍ y otra vez NO). El scraping de Maps es intermitente (Google lo bloquea). → **Fix real opcional:** habilitar Places API en GCP para datos deterministas.
- **Competidores:** dependen 100% del criterio de la IA. Han oscilado entre (a) tiendas pequeñas/SEO, (b) gigantes globales, (c) genéricos repetidos. Ahora se piden por **segmento concreto** y **marcas nacionales**, pero sigue habiendo ruido ocasional (p. ej. un outlier de otro rubro).
- **País en dominios `.com` globales:** una marca colombiana con `.com` internacional (ej. leonisa.com) puede salir como EE.UU. porque su web apunta a ese mercado. Correcto técnicamente, pero puede no ser lo que el cliente espera.
- **Webs bloqueadas por anti-bots:** ahora la IA analiza por nombre igual (Mario Hernández, Arturo Calle), pero con menos contexto (categoría/segmento los deduce la IA sola).
- **"Consulta a la IA no completada" (limited):** aparece cuando la llamada grounded a la IA falla/tarda (límite temporal del proveedor). Es honesto, pero conviene reducir su frecuencia (retry / fallback a otro motor).

---

## 3. TODOS los prompts de IA (verbatim, con ubicación)

> Variables entre `{}` se rellenan en runtime. `L(es, en)` elige idioma. `{brand}` = marca, `{domain}` = dominio, `{place}`/`{country}` = país, `{sec_txt}` = categoría corta, `{full}` = URL.

### 3.1 `q_mega` — Briefing principal (grounded, con búsqueda web) · `geo_ai.py:~1226`
Una sola llamada que devuelve casi todo en formato `CAMPO: valor`.

```
Usa búsqueda web y entra en {full}. Actúa como analista SEO y GEO. Analiza la empresa
"{brand}". Responde EXACTAMENTE en este formato, en texto plano, sin markdown y sin
enlaces, cada campo en UNA línea:
SECTOR: <sector concreto>
ZONA: <ciudad y PAÍS donde opera. Deduce el país por la dirección, el prefijo telefónico
       (+57 Colombia, +34 España, +52 México...), la moneda, el idioma-región y las
       menciones del sitio. Señal previa detectada: {country_hint}; confírmala o corrígela
       leyendo el sitio. NUNCA asumas España por defecto>
RECOMIENDA: <SI, NO o AVECES> si alguien pide ese tipo de servicio en esa zona SIN nombrar
       la marca, ¿la recomendarías?
COMPETENCIA: <NO uses resultados de búsqueda: usa TU CONOCIMIENTO. 4-6 marcas MÁS GRANDES,
       CONOCIDAS y líderes que compiten con "{brand}" en su país y MISMO segmento.
       PROHIBIDO tiendas pequeñas/desconocidas. Tampoco gigantes globales. Separadas por |>
FUENTES: <hasta 4 dominios en los que te apoyas, separadas por |>
BUSQUEDAS: <3 búsquedas que un cliente escribiría, con la ciudad, separadas por |>
FICHA_GOOGLE: <Busca en Google/Maps "{brand}". SI si TIENE ficha (mapa, reseñas, horario).
       Las marcas/cadenas conocidas casi siempre tienen. SI solo si estás seguro; NO solo
       si tras buscar no hay>
CATEGORIA: <categoría de la ficha (p. ej. 'Tienda de ropa'); rubro si no hay ficha>
RESENAS: <número TOTAL aproximado de reseñas; 0 si no hay ficha. SOLO el número>
VALORACION: <valoración media 0-5 si la ves; vacío si no>
KEYWORDS: <3-5 palabras clave, separadas por |>
ENTIDADES: <3-5 temas/entidades clave, separadas por |>
CONTENIDO: <una frase honesta sobre la calidad del contenido>
MEJORAS: <2-3 mejoras concretas de contenido, separadas por |>
```
**Nota:** el campo COMPETENCIA de aquí es un respaldo; los competidores que se muestran salen de `q_comp` (§3.2).

### 3.2 `q_comp` — Segmento + competidores + ficha (SIN búsqueda, conocimiento) · `geo_ai.py:~1309`
La fuente PRIMARIA de competidores. Fuerza a identificar el segmento concreto antes de listar.

```
Sin usar búsqueda web, solo con tu conocimiento. Primero identifica a qué se dedica
EXACTAMENTE la marca "{brand}": su SEGMENTO concreto de producto (p. ej. marroquinería y
bolsos de cuero, ropa interior, calzado deportivo, joyería, trajes de baño...), NO un
genérico como 'moda' o 'ropa'. Responde EXACTAMENTE en 4 líneas y nada más:
SEGMENTO: <el segmento concreto de "{brand}">
COMPETIDORES: <6 marcas competidoras DIRECTAS que vendan EXACTAMENTE lo mismo que ese
       SEGMENTO (bolsos de cuero -> marcas de bolsos; trajes de baño -> marcas de trajes de
       baño), del MISMO país ({place}) y nivel similar, que un cliente de ahí reconocería.
       Marcas NACIONALES; NO cadenas globales ni tiendas diminutas. Deben ROTAR según la
       marca y su segmento. Separadas por |. Vacío si no conoces>
FICHA: <¿tiene ficha de Google Business con reseñas? Responde SI, NO o NOSE>
RESENAS: <número aproximado de reseñas si lo sabes; vacío si no>
```
**Combinación de ficha:** cualquier SÍ fiable (de `q_mega` FICHA_GOOGLE o de aquí FICHA) → gbp=True. RESENAS "0" se trata como desconocido (None).

### 3.3 `q_cat` — Búsquedas de cliente / test de "¿apareces?" (grounded) · `geo_ai.py:~1296`
Mide en cuántas búsquedas de categoría aparece la marca (el "apareces 3/3").

```
Usa búsqueda web. Un cliente en {place} podría escribir estas búsquedas en un asistente de
IA. Para CADA búsqueda, recomienda 4-5 EMPRESAS o profesionales REALES de {sec_txt} en
{place}. Devuelve EXACTAMENTE este formato:
@@1@@ {búsqueda 1}
@@2@@ {búsqueda 2}
@@3@@ {búsqueda 3}
Debajo de cada marcador @@n@@, una empresa por línea como 'Nombre real | dominio.com'.
Reglas: solo negocios REALES con web propia; el 'Nombre' es la empresa, NO una
categoría/servicio/ciudad; si hay menos de 4-5 reales, lista solo las reales. Sin explicaciones.
```
Las búsquedas (`cat_queries`) se generan localmente: `mejores {sec_txt} en {place}`, `¿qué {sec_txt} me recomiendas en {place}?`, `quiero {sec_txt} en {place}, ¿qué marcas hay?`.

### 3.4 `q_mem` — Reconocimiento "de memoria" (SIN búsqueda) · `geo_ai.py:~1257`
Mide si la IA conoce la marca por sí sola (clave para GEO).

```
En una frase, ¿qué es "{brand}" y a qué se dedica? Empieza por el nombre.
Si NO tienes información fiable de esa marca, responde solo NO_LO_SE.
```

### 3.5 `q_brand` — Descripción con búsqueda en vivo (grounded) · `geo_ai.py:~1262`
Prueba real de cómo te cita la IA y en qué fuentes se apoya.

```
Usa búsqueda web. En 2-3 frases, ¿qué es "{brand}" ({domain}) y a qué se dedica?
Básate SOLO en lo que encuentres en la web sobre ESA empresa; no inventes.
Si no encuentras información fiable, responde solo NO_LO_SE.
```

---

## 4. Cómo se combinan (para entender el impacto de cada prompt)

- **Reconocimiento (recognition):** `q_mem` (de memoria) + `q_brand` (con web). Si conoce de memoria → "strong"; solo con web → "weak"; nada → "none".
- **¿Apareces? (recommended):** `q_cat` mide en cuántas búsquedas sale la marca (`reco_hits/reco_total`).
- **Competidores:** `q_comp` (primario) → si vacío, `q_cat`/probe → si vacío, `q_mega` COMPETENCIA.
- **Ficha Google:** `q_mega` FICHA_GOOGLE + `q_comp` FICHA (cualquier SÍ gana) → luego Places/scraping enriquecen reseñas exactas.
- **País/sector/categoría/keywords/contenido:** `q_mega`.

---

## 5. Reglas al mejorar prompts (mantener SIEMPRE)

1. **Salida estructurada** (`CAMPO: valor`, una línea por campo): el parser (`_f`) depende de esto. No romper el formato.
2. **Anti-alucinación explícita:** "SI solo si estás seguro", "no inventes", "NUNCA asumas país por el nombre", "vacío/NO_LO_SE si no sabes".
3. **Conocimiento vs búsqueda:** competidores y ficha por CONOCIMIENTO (marcas reales que el modelo sabe); reconocimiento/apariencia por BÚSQUEDA en vivo.
4. **Segmento concreto antes que genérico** (marroquinería ≠ "moda"): que roten por marca.
5. **Sin ejemplos que anclen** a nombres concretos si no quieres que se repitan (los ejemplos de "Studio F, Koaj..." hacían que salieran siempre).
6. **Idioma:** casi todo tiene versión ES y EN (`L(es, en)`). Editar AMBAS.
7. Tras editar: `python -m py_compile app/geo_ai.py`, y verificar con un análisis real (`POST /api/analyze`) leyendo el JSON de `/api/status/{job_id}` (campos: `geo_ai.gbp`, `geo_ai.competitors`, `geo_ai.recognition`, `meta.country`).

---

## 6. Ideas concretas de mejora (para discutir con Cowork)

- **Ficha Google determinista:** habilitar Places API (New) en GCP `907452438504` (un clic) → reseñas/categoría exactas; la IA seguiría de respaldo.
- **Reducir "limited":** que la llamada grounded reintente y, si falla, caiga a un segundo motor (p. ej. Gemini) antes de rendirse.
- **Competidores más finos:** pedir a la IA que valide que cada competidor pertenece AL MISMO segmento (auto-filtro) y descartar los que no.
- **País por dominio local:** si existe versión `.com.co`/`.es`, preferirla para fijar el mercado del cliente.
- **Un solo prompt maestro vs varios:** hoy son 5 llamadas (q_mega, q_comp, q_cat, q_mem, q_brand). Se puede consolidar para bajar latencia y "limited", a costa de precisión por campo.

---

*Repo del motor:* `edilsonf15/cupperlab-diagnostico` · *Deploy:* manual en Dokploy con **Clean Cache ON** (auto-deploy en push a main). *Endpoint:* analisis.cupperlab.com.
