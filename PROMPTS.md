# Prompts del motor v2 (test GEO) — `app/geo_ai.py`

Documento de referencia de los 4 prompts que usa el motor. Cambió la filosofía respecto a v1:

| v1 | v2 |
|---|---|
| La IA decidía ficha de Google, reseñas, categoría, país y competidores | Todo eso se **mide** (Places API, Serper, crawl). La IA solo responde lo que solo la IA puede: qué dice de la marca y a quién recomienda |
| Un prompt "actúa como analista SEO y GEO" con 14 campos `CAMPO: valor` | La pregunta a la IA es la **del cliente**, en texto libre (P2). La estructura se saca después con un modelo mini y **JSON con esquema** (P3) |
| Las 3 búsquedas iban en una sola llamada (el modelo copiaba la misma lista) | **Una llamada por búsqueda y por motor** |
| Búsquedas = eslogan de la web ("mejores Especialistas en muebles de diseño contemporáneo en España") | Búsquedas generadas como cliente (P1) a partir de la categoría de Google + ciudad; filtro anti-eslogan |
| `knows` = "la respuesta mide más de 25 caracteres" | `conoce` solo cuenta si el **sector y el país que cree la IA coinciden con los medidos** (P4) |
| Competidores "de memoria, sin buscar" (con ejemplos ancla) | Competidores = los que las IAs **citaron de verdad** + top 5 de Google, con quién los cita |

Reglas comunes: `temperature=0`; JSON con esquema en P1, P3 y P4; P2 es texto libre a propósito; sin ejemplos con marcas reales (anclan); ES y EN; `{variables}` en runtime. `PROMPTS_VERSION = "2.0"` viaja en el resultado.

---

## P1 · Categoría en lenguaje de cliente + 3 búsquedas reales
Modelo mini, sin búsqueda. Salida `{"categoria": str, "busquedas": [str, str, str]}`.

Entrada: marca, categoría de Google (Places `primaryType` traducido), ciudad, país, ámbito (`ciudad` | `pais`, lo decide `main._scope`), título + description + H1 de la home (≤600 caracteres).

```
Eres un cliente potencial, no un analista. Con los datos de abajo, escribe cómo buscaría un cliente este tipo de negocio en un asistente de IA si NO conociera la marca.

Datos medidos (no los cuestiones):
- Negocio: {brand}
- Categoría según Google: {category}
- Ubicación: {city}, {country}
- Ámbito: {scope}   (ciudad = negocio local; pais = vende/atiende en todo el país)
- Texto de su web: "{snippet}"

Devuelve JSON con:
- "categoria": cómo llamaría un cliente a este tipo de negocio, en 2-5 palabras, en minúsculas, sin adjetivos publicitarios ni la marca (una categoría genérica del sector, no un eslogan).
- "busquedas": exactamente 3 frases distintas, de 5 a 12 palabras, tal y como las escribiría un cliente en {country} en un chat de IA. Si el ámbito es "ciudad", las 3 incluyen "{city}"; si es "pais", ninguna incluye ciudad y como mucho una menciona "{country}". Una de las tres debe pedir explícitamente recomendaciones ("recomiéndame", "cuáles son los mejores", "dónde puedo"). No uses la marca ni frases de su web.
```

Después del prompt, el código **descarta** cualquier búsqueda que contenga la marca o que comparta ≥70 % de sus palabras con el texto de la web, y rellena con plantillas neutras (`dónde encontrar {categoría} en {ciudad}`…).

## P2 · La pregunta del cliente (una llamada por búsqueda × motor)
ChatGPT (`web_search`), Perplexity (`sonar`), Gemini (`google_search`) según `AI_ENGINES`. **Sin system prompt, sin formato, sin instrucciones.** Se guardan la respuesta cruda (ejemplo real en el PDF) y las citas.

```
Estoy en {ciudad_o_pais}. {busqueda}
```

## P3 · Extracción de negocios (una llamada para todas las respuestas)
Modelo mini, JSON estricto `{"respuestas": [{"id": int, "negocios": [{"nombre": str, "dominio": str|null}]}]}`.

```
Te paso respuestas de asistentes de IA a preguntas de un cliente. Extrae los negocios, marcas o profesionales concretos que cada respuesta recomienda o menciona, en el orden en que aparecen.

Reglas:
- Solo entidades con nombre propio (empresas, tiendas, marcas, profesionales). No incluyas categorías, ciudades, plataformas de reseñas ni directorios (mapas, redes sociales, marketplaces genéricos) salvo que la respuesta los recomiende como el negocio en sí.
- Si la respuesta da un dominio o URL para ese negocio, inclúyelo en "dominio" sin protocolo ni www. Si no, null. No inventes dominios.
- Si una respuesta no recomienda ningún negocio concreto (solo consejos genéricos), devuelve una lista vacía para ella.
- Respeta el nombre tal y como aparece. Devuelve una entrada por respuesta, ids 1..N.

Respuestas:
[1]
...
```

El código decide si **apareces** comparando `dominio` con el tuyo o el nombre normalizado (similitud ≥ 0,85). Los competidores sin dominio se resuelven con Serper (`serp.find_domains`).

## P4 · Reconocimiento de marca (de memoria y con búsqueda)
Dos llamadas: modelo mini sin búsqueda (JSON estricto) y motor primario con búsqueda (JSON en el texto, parseo tolerante).

```
{Sin usar búsqueda web, solo con lo que ya sabes: | Usa búsqueda web. }¿Conoces la empresa "{brand}" cuyo sitio web es {domain}?

Responde en JSON:
- "conoce": true solo si tienes información concreta sobre ESA empresa (no sobre otra con nombre parecido). false si no la conoces o solo puedes suponer por el nombre.
- "descripcion": si conoce=true, una frase de máximo 30 palabras sobre qué hace, que empiece por "{brand}". Si false, cadena vacía.
- "sector": en 2-4 palabras, a qué se dedica según lo que sabes. Vacío si no sabes.
- "pais": país donde opera según lo que sabes. Vacío si no sabes.
- "confianza": "alta", "media" o "baja".
No inventes. Es preferible conoce=false que una suposición.
```

Regla de código: `reconoce = conoce && confianza != "baja" && sector_compatible(sector, categoría medida) && pais_compatible(pais, país medido)`. `strong` = de memoria; `weak` = solo con búsqueda; `none` = ninguna.

---

## Cómo se combinan

- **Reconocimiento** → P4 (mem + web) → `recognition`, `brand_description`, `sources` (citas externas al describirte).
- **¿Apareces?** → P2 × P3 → por motor `hits/valid`; global `reco_hits/reco_total`, `share_of_voice` (menciones tuyas / menciones totales) y `recommended` (True si apareces en ≥ la mitad de las respuestas válidas; False si en ninguna; None si a medias o sin datos).
- **Competidores** → P3 (citados por las IAs, con `cited_by`) + Google top 5 (Serper, `source: google`).
- **Ficha / reseñas / categoría / ciudad / país** → **Places API** (`places.py`), nunca la IA.
- **Posición en Google e indexación** → **Serper** (`serp.py`).
- `ai_score` (entra en el índice global) = 40 % reconocimiento (strong 1 / weak 0,55) + 60 % `reco_hits/reco_total`.

## Reglas al tocar prompts

1. No pedir hechos a la IA (ficha, reseñas, país, categoría): se miden.
2. P2 se queda como texto de cliente. Cualquier instrucción añadida ("recomienda 4-5 empresas con dominio") sesga la respuesta y deja de ser una medición real.
3. Sin ejemplos con marcas reales.
4. Editar ES y EN.
5. Tras cambiar un prompt: `python bench/run.py` y comparar con la corrida anterior (`bench_runs` en `data/app.db`). Subir `PROMPTS_VERSION` y `ENGINE_VERSION` (invalida la caché).
