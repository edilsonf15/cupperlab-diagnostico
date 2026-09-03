# Cupperlab · Landing de Diagnóstico rápido SEO + GEO

Landing de captación con **dos velocidades**:

1. **Resultado en pantalla, rápido (~3-5 s):** salud técnica, SEO on-page y preparación para la
   IA (GEO/LLMO), con gauge y hallazgos. El cliente ve su nota al instante.
2. **En segundo plano (1-2 min):** consulta **real a la IA** (¿te conoce?, ¿te recomienda?),
   genera un **informe premium en PDF** con el diseño oficial de Cupperlab (Chromium) y lo
   **envía por correo** al cliente con el teléfono de Cupperlab. En paralelo registra el lead.

Replica el informe premium de la skill `cupperlab-informe-seo-geo` con las herramientas de este
entorno: la landing capta y engancha en segundos; el PDF cierra.

## Arquitectura

```
POST /api/analyze
  → analyze()            # rápido, sin IA → responde a la pantalla YA (queued:true)
  → _enrich_and_send()   # tarea en segundo plano:
        run_ai_geo()          # consulta real a la IA (si hay ANTHROPIC_API_KEY)
        report_pdf.build_pdf() # PDF premium con Chromium (Playwright)
        emailer.send_client_report(pdf)  # correo con el PDF adjunto
        + registro de lead + aviso al equipo
```

## Qué mide (todo real, nada inventado)

| Bloque | Comprobaciones |
|---|---|
| **Salud técnica** | HTTPS, respuesta y velocidad, robots.txt, sitemap, viewport móvil, **muestra de enlaces rotos (404)** |
| **SEO on-page** | title, meta description, H1, canonical, idioma, contenido, Open Graph |
| **Preparación IA (GEO)** | datos estructurados (schema), marca como entidad (sameAs), `llms.txt`, estructura de titulares, resumen citable |

> El análisis mide si tu web *está preparada* para la IA. El test real de *"¿la IA te menciona?"*
> (preguntas en vivo a ChatGPT/Gemini), la competencia y el plan van en el **informe premium**.

## Estructura

```
landing-diagnostico-seo-geo/
├── app/
│   ├── main.py            # FastAPI: rutas, rate-limit, tarea en segundo plano
│   ├── analyzer.py        # motor de análisis rápido + apply_ai_to_result()
│   ├── geo_ai.py          # consulta REAL a la IA (Anthropic) — GEO/LLMO
│   ├── report_pdf.py      # informe premium (plantilla oficial Cupperlab)
│   ├── pdf_render.py      # HTML → PDF con Chromium (Playwright)
│   ├── emailer.py         # envío SMTP con PDF adjunto (cliente + lead)
│   ├── templates/         # index.html (landing+resultados) · email_report.html
│   └── static/            # css de marca, js, logo y fuentes Cupperlab
├── data/                  # leads.jsonl (se crea solo)
├── Dockerfile · docker-compose.yml · requirements.txt · .env.example
```

## Puesta en marcha rápida (local)

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows · en Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # navegador para el PDF premium (una vez)
cp .env.example .env          # rellena teléfono, SMTP, ANTHROPIC_API_KEY, etc.
cd app && uvicorn main:app --reload --port 8000
```

Abre http://localhost:8000. Sin SMTP configurado funciona igual, pero los correos van en
**modo maqueta** (se registran en consola, no se envían).

## Despliegue con Docker

```bash
cp .env.example .env   # y edítalo
docker compose up -d --build
```

Queda escuchando en el puerto **8000**. Los leads se guardan en `./data/leads.jsonl`.

## Despliegue en Plesk

Dos caminos según lo que tenga el servidor:

**A) Plesk con soporte Docker (recomendado)**
1. Sube la carpeta al servidor (o clónala).
2. Plesk → *Docker* → construye la imagen desde el `Dockerfile`, o usa `docker compose up -d`.
3. En el dominio → *Docker Proxy Rules*: enruta `https://diagnostico.tudominio.com` → contenedor `:8000`.
4. Plesk gestiona el SSL (Let's Encrypt) del subdominio.

**B) Plesk con Python (Passenger / "Aplicación Python")**
1. Crea el subdominio (p. ej. `diagnostico.cupperlab.com`).
2. Plesk → *Python* → raíz de la app = carpeta del proyecto; archivo de arranque = `app/main.py`;
   application object = `app`.
3. Instala dependencias desde `requirements.txt` (botón de Plesk).
4. Variables de entorno (SMTP, teléfono, etc.) en el panel de la app Python.
5. Activa SSL del subdominio.

> En ambos casos configura las variables del `.env` (SMTP + teléfono). El SMTP puede ser el
> propio correo del servidor Plesk (`localhost:25`) o un servicio externo (Brevo, Resend, etc.).

## Variables de entorno (.env)

Ver `.env.example`. Las clave:

- `CUPPERLAB_PHONE`, `CUPPERLAB_EMAIL`, `CUPPERLAB_CALENDLY` → salen en la landing y el correo.
- `SMTP_*` → envío del diagnóstico al cliente. Vacío = modo maqueta.
- `LEAD_INBOX` → bandeja donde llegan los leads.
- `GOOGLE_PSI_API_KEY` → *opcional*; añade la velocidad real de Google PageSpeed a la nota técnica.
- `RATE_LIMIT_PER_HOUR` (30) y `ANALYSIS_HARD_TIMEOUT` (50 s) → límites de abuso y tiempo.

## Leads

Cada análisis añade una línea a `data/leads.jsonl` (dominio, score, nombre, email, teléfono,
IP, fecha) y dispara un correo a `LEAD_INBOX`. Fácil de conectar a ClickUp/HubSpot más adelante.

## Próximos pasos sugeridos

- Conectar los leads a **ClickUp/CRM** (hoy quedan en JSONL + correo).
- **anti-abuso**: la landing ya limita por IP; si se hace público conviene un captcha o token.
- **Puente al informe premium**: botón "quiero el informe completo" que encola la generación con
  la skill `cupperlab-informe-seo-geo`.
- Modo **multi-dominio (batch)** para analizar una cartera de webs.
