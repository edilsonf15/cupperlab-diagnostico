# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Dueños de negocio y responsables de marketing (a menudo NO técnicos), en España y en cualquier país (la herramienta se prueba con webs de todo el mundo; interfaz y entregables bilingües ES/EN). Sospechan que están perdiendo clientes online pero no saben por qué. Son escépticos con las agencias y con el "humo de la IA". Llegan buscando una respuesta clara sobre su web, no un curso.

## Product Purpose

Herramienta gratuita de autoservicio (analisis.cupperlab.com) que diagnostica, EN VIVO y delante del usuario, qué tan visible es su negocio en Google (SEO) **y** en las respuestas de la IA (GEO/LLMO). Devuelve en pantalla un índice de visibilidad 0-100, cómo lo ve la IA, su competencia real y los arreglos que más mueven la aguja; y envía el informe completo en PDF con el plan de acción al correo. Éxito = el visitante entiende su problema, entrega sus datos (lead) y agenda una sesión de 30 min.

## Positioning

Casi todos los "test SEO" solo miran Google. Esta herramienta mide algo que la competencia no muestra: **cuando un cliente le pregunta a la IA (ChatGPT) por tu servicio, la IA nombra a tu competencia por su nombre y a ti no**. Lo demuestra en directo, con datos reales medidos en vivo (nada fabricado), detectando primero el país/mercado real de la web, y siendo honesta sea cual sea la web (no dice "apareces" si no apareces). El on-screen es corto y contundente; el detalle y el plan van en el PDF. La marca: "Mejoramos tu rentabilidad." — warm tech, no cold tech.

## Operating Context

El visitante llega desde una campaña o el boca a boca, normalmente con prisa y algo de desconfianza. Rellena un formulario (URL, nombre, correo, teléfono, consentimiento) y **se queda mirando** cómo el sistema analiza su web durante ~1 minuto: rastreo de páginas, HTTPS/robots/sitemap, consulta real a la IA por su marca y su servicio, detección de competencia, medición de velocidad móvil/escritorio. El análisis NO se debe percibir pausado en ningún momento: el progreso avanza y narra lo que hace. Al terminar ve su nota y sus dimensiones en pantalla; el PDF llega al correo; puede agendar 30 min en /agenda (horario real de Google embebido).

## Capabilities and Constraints

- Backend FastAPI + Jinja2 (la landing es `app/templates/index.html`); job asíncrono: `POST /api/analyze` {url,email,name,phone,company,lang} → `job_id` → poll `GET /api/status/{id}` {progress 0-100, done, result, error}. **Este contrato NO se puede romper.**
- El objeto `result` alimenta: score/grade, categorías (tecnico/onpage/geo con checks), geo_ai (knows_brand, recommended, gbp, competitors, ai_score), psi_full (mobile/desktop performance+lcp), signals.security, meta, indexation.
- Motor de IA del análisis = solo ChatGPT (OpenAI). En la landing NO se nombran herramientas (Lighthouse/PageSpeed) ni marcas de IA como reclamo técnico salvo "ChatGPT" como referente que el cliente entiende.
- Bilingüe ES/EN con selector de banderas; idioma recordado en localStorage; los resultados, correo y PDF salen en el idioma solicitado.
- Rápida y responsive en todos los dispositivos; sin librerías pesadas; respetar prefers-reduced-motion.
- Rendering vía Jinja: evitar `{{`, `{%`, `{#` literales en CSS/JS.
- Contenido legal (aviso legal, privacidad RGPD, cookies) es verbatim y obligatorio en el footer (entidad: PUBLICIDAD DIGITAL MULTIMEDIA INTERNACIONAL, S.L.U., NIF B-87580866, Madrid).

## Brand Commitments

- Nombre/marca: **Cupperlab** (logo oficial en `/static/img/cupperlab-logo-color.png`; nunca tipografiarlo ni generarlo). Favicon `/static/img/favicon.svg`.
- Firma canónica: **"Mejoramos tu rentabilidad."** Voz en español, tú, declarativa, anti-hype, orientada a resultado de negocio. Sin emoji en la voz de marca.
- Paleta de marca: cyan `#1CBCE4` (cyan de texto sobre claro `#0f9bc2`), naranja señal `#F46434`, carbón `#0B1016`/tinta, papel frío `#F7F9FB`. "El carbón manda, el cyan emite, el naranja avisa (~1 aparición por vista)."
- Tipos de marca: Sora (display), Plus Jakarta Sans (texto), JetBrains Mono (eyebrows/labels).
- Contacto real: +34 91 495 2585 · clientes@cupperlab.com · dpo@cupperlab.com · cupperlab.com · LinkedIn/Instagram (@cupperlab.es).

## Evidence on Hand

- Datos reales: el propio análisis en vivo del sitio del visitante (índice, dimensiones, competencia, cómo lo ve la IA). NO se deben fabricar cifras, testimonios, logos de clientes, ni claims comerciales que no existan.
- Assets propios: logo, favicon. Existe una página de agenda de 30 min con calendario real de Google (`/agenda`).
- La simulación del hero (chat de IA de ejemplo, competidores "A/B") debe etiquetarse claramente como simulación/ejemplo, no como dato real de una web concreta.

## Product Principles

- **Todo dato mostrado es real y medido en vivo; nada fabricado.** La simulación de ejemplo se marca como tal.
- **Ganar al cliente mostrando lo que más le cuesta** (los negativos, honestos) — pero de forma clara para no técnicos.
- **El país/mercado se detecta primero**; la herramienta funciona para webs de todo el mundo.
- **On-screen corto y contundente; el plan completo en el PDF** + sesión humana de 30 min como siguiente paso.
- **Nunca se percibe pausado:** el análisis en vivo es parte del producto, no una espera.
