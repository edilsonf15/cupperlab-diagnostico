"""
Informe premium en PDF: reutiliza el sistema de diseño de la plantilla oficial de
Cupperlab (portada con banda degradada, gauge, niveles, tablas, tarjetas de cita
de IA, plan) y lo rellena con los datos verificados en vivo. Se renderiza con
Chromium (pdf_render) para fidelidad total. Fuentes y logo van embebidos (data
URI) para que el HTML sea autocontenido en cualquier entorno.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from pathlib import Path

from pdf_render import html_to_pdf
from i18n import L

BASE = Path(__file__).resolve().parent
FONTS = BASE / "static" / "assets" / "fonts"
LOGO = BASE / "static" / "assets" / "cupperlab-logo.png"

CY = "#1cbce4"; CY6 = "#0f9bc2"; OR = "#f46434"; OR7 = "#d94f22"
INK9 = "#0e1319"; GREEN = "#1f9d6b"; AMBER = "#d9920a"; RED = "#d0402a"; NAVY = "#12324a"

_FONT_MAP = [
    ("Sora", 400, "sora-400.woff2"), ("Sora", 600, "sora-600.woff2"),
    ("Sora", 700, "sora-700.woff2"), ("Sora", 800, "sora-800.woff2"),
    ("Plus Jakarta Sans", 400, "jakarta-400.woff2"), ("Plus Jakarta Sans", 600, "jakarta-600.woff2"),
    ("Plus Jakarta Sans", 700, "jakarta-700.woff2"),
    ("JetBrains Mono", 500, "jetmono-500.woff2"), ("JetBrains Mono", 700, "jetmono-700.woff2"),
]


def _font_faces() -> str:
    out = []
    for fam, wt, fn in _FONT_MAP:
        p = FONTS / fn
        if not p.exists():
            continue
        b64 = base64.b64encode(p.read_bytes()).decode()
        out.append(f'@font-face{{font-family:"{fam}";font-weight:{wt};font-display:swap;'
                   f'src:url("data:font/woff2;base64,{b64}") format("woff2");}}')
    return "\n".join(out)


def _logo_uri() -> str:
    try:
        return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()
    except Exception:  # noqa: BLE001
        return ""


def _color(s: int) -> str:
    # MISMOS umbrales que la pantalla: >82 verde (óptimo), 50-82 ámbar (mejorable), <50 rojo (crítico).
    return GREEN if s > 82 else (AMBER if s >= 50 else RED)


def _lvl_color(s: int) -> str:
    return GREEN if s >= 70 else AMBER if s >= 50 else OR if s >= 35 else RED


def _chk_pct(cat: dict, key: str) -> int:
    for c in cat.get("checks", []):
        if c["key"] == key and c["possible"]:
            return round(100 * c["earned"] / c["possible"])
    return 0


def build_plan(r: dict) -> list[dict]:
    """Acciones en lenguaje de cliente con impacto/esfuerzo/fase (como el informe)."""
    s = r.get("signals", {}); m = r.get("meta", {}); ai = r.get("geo_ai") or {}
    psi = r.get("psi_full") or {}
    mob = (psi.get("mobile") or {}).get("performance")
    plan: list[dict] = []

    def add(text, impacto, esfuerzo, fase):
        plan.append({"text": text, "impacto": impacto, "esfuerzo": esfuerzo, "fase": fase})

    rb = s.get("robots_info") or {}
    an = s.get("analytics") or {}
    sec = s.get("security") or {}
    # --- Seguridad (lo más urgente si hay algo expuesto) ---
    if sec.get("exposed"):
        add(L("URGENTE (seguridad): bloquear los archivos sensibles accesibles (", "URGENT (security): block the accessible sensitive files (") +
            ", ".join(e["path"] for e in sec["exposed"][:3]) + L("): pueden filtrar código o credenciales.", "): they can leak code or credentials."),
            "Alto", "Bajo", 1)
    if sec.get("headers_missing"):
        add(L("Anadir las cabeceras de seguridad que faltan (", "Add the missing security headers (") + ", ".join(sec["headers_missing"][:3]) +
            L(") para proteger a tus visitantes de ataques comunes.", ") to protect your visitors from common attacks."), "Medio", "Bajo", 2)
    if sec.get("leaks"):
        add(L("Ocultar la versión del servidor/CMS que hoy es publica, para dificultar ataques dirigidos.",
            "Hide the server/CMS versión that is currently public, to make targeted attacks harder."),
            "Bajo", "Bajo", 2)
    # --- Base técnica (que exista y sea rastreable) ---
    if not s.get("https"):
        add(L("Activar la conexión segura (HTTPS): sin candado Google penaliza y el navegador avisa de web no segura.",
            "Enable the secure connection (HTTPS): without the padlock Google penalizes you and the browser warns visitors the site is not secure."),
            "Alto", "Bajo", 1)
    if not m.get("viewport"):
        add(L("Adaptar la web a móvil (viewport): la mayoria de tus clientes te abren desde el celular.", "Make the site mobile-friendly (viewport): most of your clients open it from their phone."), "Alto", "Medio", 1)
    if rb.get("blocks_all"):
        add(L("URGENTE: tu robots.txt bloquea TODO el sitio (Disallow: /). Google no puede rastrearte. Quitar ese bloqueo.",
            "URGENT: your robots.txt blocks the ENTIRE site (Disallow: /). Google cannot crawl you. Remove that block."),
            "Alto", "Bajo", 1)
    elif not rb.get("present"):
        add(L("Publicar un robots.txt que guie el rastreo de Google y declare el mapa del sitio.", "Publish a robots.txt that guides Google's crawling and declares the sitemap."), "Medio", "Bajo", 1)
    if rb.get("blocks_render") and not rb.get("blocks_all"):
        add(L("URGENTE: tu robots.txt bloquea CSS/JS que Google necesita para ver tu web (", "URGENT: your robots.txt blocks CSS/JS Google needs to render your site (") +
            ", ".join(rb["blocks_render"][:3]) + L("). Permitir esos recursos para que no te vea rota.", "). Allow those resources so it does not see your site broken."), "Alto", "Bajo", 1)
    if rb.get("blocks_content") and not rb.get("blocks_all"):
        add(L("Revisar el robots.txt: hoy bloquea secciones de contenido (", "Review the robots.txt: it currently blocks content sections (") +
            ", ".join(rb["blocks_content"][:3]) + L("). Si ahí hay páginas que quieres posicionar, no aparecerán en Google.", "). If those hold pages you want to rank, they will not show on Google."), "Medio", "Bajo", 2)
    if rb.get("present") and rb.get("suggest_block"):
        add(L("Afinar el robots.txt: bloquear ", "Fine-tune the robots.txt: block ") + ", ".join(rb["suggest_block"]) +
            L(" para que Google no gaste rastreo en páginas sin valor y priorice las que venden.", " so Google does not waste its crawl budget on low-value pages and prioritizes the ones that sell."), "Medio", "Bajo", 2)
    if not s.get("sitemap"):
        add(L("Crear el mapa del sitio (sitemap.xml) para que Google y la IA descubran todas tus páginas.", "Create the sitemap (sitemap.xml) so Google and AI can discover all your pages."), "Alto", "Bajo", 1)
    elif not s.get("sitemap_in_robots"):
        add(L("Declarar el mapa del sitio dentro del robots.txt para que Google lo encuentre antes.", "Declare the sitemap inside the robots.txt so Google finds it sooner."), "Bajo", "Bajo", 2)
    if _broken(r)[0] > 0:
        add(f"{L('Reparar los', 'Fix the')} {_broken(r)[0]} {L('enlace(s) roto(s) (404) y limpiar el mapa del sitio (quitar etiquetas y páginas vacias).', 'broken link(s) (404) and clean up the sitemap (remove tags and empty pages).')}",
            "Medio", "Medio", 2)
    # --- On-page (que Google entienda y muestre) ---
    if not m.get("title") or not (25 <= len(m.get("title", "")) <= 65):
        add(L("Escribir títulos unicos por página (55-60 caracteres) con el servicio y la ciudad.", "Write unique titles per page (55-60 characters) including the service and the city."), "Alto", "Bajo", 1)
    if not m.get("description"):
        add(L("Escribir una meta descripción por página: es el resumen que Google muestra y que la IA cita.", "Write a meta description per page: it is the summary Google shows and that AI quotes."), "Alto", "Bajo", 1)
    if m.get("h1_count", 0) != 1:
        add(L("Marcar un titular principal (H1) claro y unico en cada página.", "Set one clear, unique main heading (H1) on each page."), "Medio", "Bajo", 1)
    if not m.get("canonical"):
        add(L("Anadir la URL canonica para que Google no vea páginas duplicadas.", "Add the canonical URL so Google does not see duplicate pages."), "Medio", "Bajo", 2)
    if not m.get("lang"):
        add(L("Declarar el idioma de la web (atributo lang) para paises e IA.", "Declare the site language (lang attribute) for countries and AI."), "Bajo", "Bajo", 2)
    if m.get("word_count", 0) < 300:
        add(L("Ampliar el contenido de las páginas clave: texto propio que responda lo que busca el cliente.", "Expand the content of your key pages: original text that answers what the client is looking for."), "Medio", "Medio", 2)
    if not (m.get("og_title") and m.get("og_image")):
        add(L("Poner la vista previa al compartir (Open Graph) para ganar clics al enlazarte en redes y chats.", "Set up the share preview (Open Graph) to win clicks when you are linked on social media and chats."), "Medio", "Bajo", 1)
    it = m.get("img_total", 0); ia = m.get("img_alt", 0)
    if it and ia / it < 0.7:
        add(f"{L('Describir las imagenes (texto ALT): hoy', 'Describe your images (ALT text): today')} {ia} {L('de', 'of')} {it} {L('lo tienen. Ayuda al SEO y a la accesibilidad.', 'have it. It helps SEO and accessibility.')}",
            "Medio", "Bajo", 2)
    # --- GEO / IA (que la IA te lea, te entienda y te recomiende) ---
    if rb.get("ai_blocked"):
        add(L("URGENTE: tu robots.txt BLOQUEA a los bots de IA (", "URGENT: your robots.txt BLOCKS the AI bots (") + ", ".join(rb["ai_blocked"][:4]) +
            L("). La IA no puede leerte ni citarte. Permitir su rastreo.", "). AI cannot read or cite you. Allow their crawling."), "Alto", "Bajo", 1)
    if not m.get("schema_types"):
        add(L("Anadir los datos estructurados (schema: organizacion, servicios) para que Google y la IA entiendan tu negocio.",
            "Add structured data (schema: organization, services) so Google and AI understand your business."),
            "Alto", "Bajo", 1)
    if not m.get("has_faq"):
        add(L("Crear una sección de Preguntas frecuentes con FAQ schema: la IA cita respuestas directas de ahi.",
            "Create a Frequently Asked Questions section with FAQ schema: AI quotes direct answers from there."),
            "Alto", "Bajo", 1)
    if not m.get("has_sameas"):
        add(L("Conectar tu marca como entidad (Organization + perfiles/sameAs) para que la IA sepa que eres una empresa real.",
            "Connect your brand as an entity (Organization + profiles/sameAs) so AI knows you are a real company."),
            "Medio", "Bajo", 2)
    if not m.get("has_contact"):
        add(L("Mostrar ficha de contacto clara (nombre, teléfono, dirección) y marcarla con schema: la IA confía en negocios verificables.",
            "Show a clear contact block (name, phone, address) and mark it up with schema: AI trusts verifiable businesses."),
            "Medio", "Bajo", 2)
    if ai.get("gbp") is False:
        add(L("Activar y verificar tu ficha de Google Business (hoy no la encontramos): es clave para el mapa, las búsquedas locales y la IA local.",
            "Activate and verify your Google Business listing (we could not find it today): it is key for the map, local searches and local AI."),
            "Alto", "Bajo", 1)
    elif ai.get("gbp") is not True and m.get("has_contact"):
        add(L("Verificar y optimizar tu ficha de Google Business (categoría, fotos, reseñas): clave para mapas y para la IA local.",
            "Verify and optimize your Google Business listing (category, photos, reviews): key for maps and for local AI."),
            "Medio", "Bajo", 1)
    if m.get("word_count", 0) < 500:
        add(L("Ampliar el contenido con páginas por servicio y por pregunta del cliente: la IA necesita texto propio que citar.",
            "Expand the content with pages per service and per client question: AI needs original text to cite."),
            "Alto", "Medio", 2)
    if not s.get("llms_txt"):
        add(L("Publicar una guia para los buscadores con IA (llms.txt).", "Publish a guide for AI search engines (llms.txt)."), "Medio", "Bajo", 2)
    if ai.get("available") and not ai.get("error"):
        if not ai.get("knows_brand"):
            add(L("Hacer que la IA te reconozca: ficha de empresa clara, perfiles consistentes y rastro externo "
                "(directorios, prensa, reseñas) que la IA pueda citar.", "Get AI to recognize you: a clear company profile, consistent listings and an external footprint "
                "(directories, press, reviews) that AI can cite."), "Alto", "Medio", 2)
        if ai.get("recommended") is not True:
            add(L("Entrar en las recomendaciones de la IA: una página por servicio con el vocabulario del cliente "
                "y señales de autoridad para que te mencione junto a tu competencia.", "Get into AI's recommendations: a page per service using the client's vocabulary "
                "and authority signals so it mentions you alongside your competitors."), "Alto", "Medio", 2)
    # --- Datos y velocidad ---
    if not an.get("has_any"):
        add(L("Instalar analitica (Google Analytics 4 + Tag Manager) para saber que páginas te traen clientes.",
            "Install analytics (Google Analytics 4 + Tag Manager) to know which pages bring you clients."),
            "Medio", "Bajo", 1)
    elif an.get("duplicated"):
        add(L("Corregir la analitica duplicada: dejar una sola medición para que tus datos sean fiables.", "Fix the duplicated analytics: keep a single measurement so your data is reliable."), "Medio", "Bajo", 1)
    if mob is not None and mob < 60:
        add(f"{L('Acelerar el móvil (hoy', 'Speed up mobile (today')} {mob}/100): {L('comprimir imagenes y aligerar la portada para bajar de 2,5 s de carga.', 'compress images and lighten the homepage to load in under 2.5s.')}",
            "Alto", "Medio", 1)
    # --- Datos estructurados (schema) específicos: lo que la web marca en su dimensión ---
    st_types = [x.lower() for x in (m.get("schema_types") or [])]
    _has = lambda *ks: any(any(k in t for k in ks) for t in st_types)
    if st_types and not _has("website"):
        add(L("Añadir el marcado WebSite + SearchAction para que Google pueda mostrar tu buscador en los resultados.",
              "Add WebSite + SearchAction markup so Google can show your site search in the results."), "Bajo", "Bajo", 2)
    if st_types and not _has("product", "service", "offer"):
        add(L("Marcar tus productos o servicios con schema para que Google y la IA sepan qué ofreces.",
              "Mark up your products or services with schema so Google and AI know what you offer."), "Medio", "Bajo", 2)
    if st_types and not _has("review", "aggregaterating", "rating"):
        add(L("Marcar tus valoraciones (Review) para que puedan salir las estrellas en Google.",
              "Mark up your ratings (Review) so star ratings can appear in Google."), "Medio", "Bajo", 2)
    # --- Presencia local: mapa y horario (reseñas y ficha ya se cubren arriba) ---
    if m.get("has_map") is False:
        add(L("Incrustar un mapa de Google en tu página de contacto: ayuda a los clientes a llegar y refuerza lo local.",
              "Embed a Google map on your contact page: it helps customers reach you and reinforces the local signal."), "Bajo", "Bajo", 2)
    if m.get("has_hours") is False:
        add(L("Publicar tu horario de atención (en la web y en tu ficha de Google).",
              "Publish your opening hours (on the site and on your Google listing)."), "Bajo", "Bajo", 2)
    # --- Contenido y relevancia (E-E-A-T): autor, 'sobre nosotros', frescura ---
    oc = (r.get("onpage") or {}).get("content") or {}
    if oc:
        if oc.get("about_page") is False:
            add(L("Crear una página 'Sobre nosotros' clara: genera confianza (E-E-A-T) para Google, la IA y el cliente.",
                  "Create a clear 'About us' page: it builds trust (E-E-A-T) for Google, AI and the customer."), "Medio", "Bajo", 2)
        if oc.get("author") is False:
            add(L("Firmar los contenidos con su autor (y marcado Author): da autoridad ante Google y la IA.",
                  "Sign your content with its author (and Author markup): it gives authority with Google and AI."), "Bajo", "Bajo", 3)
        _dated = oc.get("dated_pages") or 0; _fresh = oc.get("fresh_pages") or 0
        if _dated and _fresh < max(1, round(_dated * 0.4)):
            add(L("Actualizar y fechar tus artículos clave: Google y la IA prefieren el contenido reciente.",
                  "Update and date your key articles: Google and AI prefer recent content."), "Medio", "Medio", 3)
        if (oc.get("duplicates") or {}).get("count", 0) > 0:
            add(L("Unificar las páginas casi duplicadas para no repartir tu fuerza en Google.",
                  "Consolidate near-duplicate pages so you don't split your strength in Google."), "Medio", "Medio", 2)
    add(L("Medir cada semana tu posición en buscadores y si la IA ya te reconoce y te recomienda.", "Track your search rankings every week and whether AI now recognizes and recommends you."), "Medio", "Bajo", 2)
    return plan


def _pill(status: str, label: str) -> str:
    return f'<span class="pill {status}">{label}</span>'


def _comp_names(ai: dict, n: int = 5) -> str:
    out = []
    for c in (ai.get("competitors") or [])[:n]:
        out.append(c.get("name", "") if isinstance(c, dict) else str(c))
    return ", ".join(x for x in out if x)


_SEV = {"alto": ("Alto", OR7, OR, 82), "medio": ("Medio", "#a9790a", AMBER, 55),
        "bajo": ("Bajo", CY6, CY, 35), "critico": ("Critico", RED, RED, 92)}


def _severity_bars(r: dict) -> str:
    """Puntos debiles en tabla clara: problema, gravedad y que te cuesta."""
    items = r.get("findings_improve", [])
    if not items:
        return (f'<div class="block callout g"><b>{L("Sin fallos graves.", "No serious issues.")}</b> {L("No detectamos problemas criticos "
                "en el análisis rápido. Toca mantener y monitorizar.", "We did not detect critical problems "
                "in the quick analysis. Now it is about maintaining and monitoring.")}</div>')
    order = {"alto": 0, "medio": 1, "bajo": 2}
    items = sorted(items, key=lambda f: order.get(f.get("severity", "medio"), 1))[:6]
    pill = {"alto": ("crit", L("Alto", "High")), "medio": ("med", L("Medio", "Medium")), "bajo": ("low", L("Bajo", "Low"))}
    trs = ""
    for f in items:
        sev = f.get("severity", "medio")
        cls, lab = pill.get(sev, ("med", L("Medio", "Medium")))
        trs += (f'<tr><td><b>{f.get("title","")}</b></td>'
                f'<td class="c"><span class="pill {cls}">{lab}</span></td>'
                f'<td>{f.get("detail","")}</td></tr>')
    return f"""<div class="block">
      <div class="sectic">{L("Puntos debiles detectados · que corregir y por qué", "Weak points detected · what to fix and why")}</div>
      <table class="t"><thead><tr><th>{L("Punto debil", "Weak point")}</th><th class="c">{L("Gravedad", "Severity")}</th><th>{L("Que te cuesta hoy", "What it costs you today")}</th></tr></thead>
      <tbody>{trs}</tbody></table>
      <p style="font-size:8.5px;color:#7b8694;margin:7px 0 0;font-style:italic">{L("Ordenado por gravedad (impacto en captacion, posicionamiento y en que la IA te recomiende). Todo verificado en vivo.", "Sorted by severity (impact on lead generation, ranking and whether AI recommends you). All verified live.")}</p>
    </div>"""


def _priority_table(r: dict) -> str:
    plan = build_plan(r)
    order = {"Alto": 0, "Medio": 1, "Bajo": 2}
    plan = sorted(plan, key=lambda p: order.get(p["impacto"], 1))
    def imp_pill(v):
        return f'<span class="pill {"crit" if v=="Alto" else "med" if v=="Medio" else "ok"}">{L(v, {"Alto":"High","Medio":"Medium","Bajo":"Low"}.get(v, v))}</span>'
    rows = "".join(
        f'<tr><td>{p["text"]}</td><td class="c">{imp_pill(p["impacto"])}</td></tr>'
        for p in plan)
    return f"""<table class="t"><thead><tr><th>{L("Acción (en lenguaje de negocio)", "Action (in business terms)")}</th>
      <th class="c">{L("Impacto", "Impact")}</th></tr></thead>
      <tbody>{rows}</tbody></table>"""


_IA_KW = ["ia", "llms", "schema", "entidad", "organization", "faq", "resen", "reseñ",
          "ficha de google", "google business", "sameas", "citar", "recomiend", "perfiles",
          "wikidata", "wikipedia", "datos estructurados"]


def _acción_tipo(text: str) -> str:
    t = (text or "").lower()
    return "IA" if any(k in t for k in _IA_KW) else "SEO"


def _plan_unificado(r: dict) -> str:
    """UN solo cuadro con TODO lo que hay que mejorar, del más crítico al medio,
    etiquetando cada acción como SEO (Google) o IA (buscadores con IA)."""
    plan = build_plan(r)
    sev_rank = {"Alto": 0, "Medio": 1, "Bajo": 2}
    plan = sorted(plan, key=lambda p: sev_rank.get(p.get("impacto"), 1))
    imp_pill = {"Alto": ("crit", L("Crítico", "Critical")), "Medio": ("med", L("Medio", "Medium")), "Bajo": ("med", L("Medio", "Medium"))}
    rows = ""
    for i, p in enumerate(plan, 1):
        tipo = _acción_tipo(p["text"])
        tcls = "tia" if tipo == "IA" else "tseo"
        tlab = L("IA", "AI") if tipo == "IA" else "SEO"
        pc, pl = imp_pill.get(p["impacto"], ("med", p["impacto"]))
        rows += (f'<tr><td class="c"><span class="pk">{i}</span></td>'
                 f'<td>{p["text"]}</td>'
                 f'<td class="c"><span class="pill {tcls}">{tlab}</span></td>'
                 f'<td class="c"><span class="pill {pc}">{pl}</span></td></tr>')
    return f"""<table class="t"><thead><tr><th class="c">#</th>
      <th>{L("Acción a realizar (en lenguaje de negocio)", "Action to take (in business terms)")}</th>
      <th class="c">{L("Area", "Area")}</th><th class="c">{L("Prioridad", "Priority")}</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <div class="seglg" style="margin-top:9px">
        <div class="i"><span class="sw" style="background:{CY6}"></span>{L("IA: para que los buscadores con IA (ChatGPT, Gemini, Google IA) te reconozcan y te recomienden", "AI: so AI search engines (ChatGPT, Gemini, Google AI) recognize and recommend you")}</div>
        <div class="i"><span class="sw" style="background:{GREEN}"></span>{L("SEO: para posicionar en Google (lo clásico)", "SEO: to rank on Google (the classic side)")}</div>
      </div>"""


def _target(score: int) -> int:
    return min(score + (28 if score < 45 else 22 if score < 65 else 12), 92)


def _objetivo_box(score: int) -> str:
    return f"""<div class="objbox">
      <div class="r"><span class="a">{score}</span><span class="ar">&#8594;</span><span class="b">~{_target(score)}</span></div>
      <div class="l">{L("Objetivo de salud SEO/GEO tras el plan", "Target SEO/GEO health after the plan")}</div></div>"""


def _priority_segbar(r: dict) -> str:
    items = r.get("findings_improve", [])
    n_alto = sum(1 for f in items if f.get("severity") == "alto")
    n_med = sum(1 for f in items if f.get("severity") == "medio")
    n_bajo = sum(1 for f in items if f.get("severity") == "bajo")
    total = max(n_alto + n_med + n_bajo, 1)
    segs = [(L("Criticos/Altos", "Critical/High"), n_alto, RED), (L("Medios", "Medium"), n_med, AMBER), (L("Menores", "Minor"), n_bajo, CY6), (L("Mejora", "Improve"), 1, "#5a6675")]
    bar = ""
    for lab, cnt, col in segs:
        w = max(10, round(100 * (cnt if cnt else 0.4) / (total + 0.4)))
        bar += f'<div class="sg" style="width:{w}%;background:{col}">{lab if w > 14 else ""}</div>'
    return f"""<div class="block card">
      <div class="sectic">{L("Reparto de los hallazgos por prioridad", "Breakdown of findings by priority")}</div>
      <div class="segbar">{bar}</div>
      <div class="seglg">
        <div class="i"><span class="sw" style="background:{RED}"></span>{L("Criticos/Altos: lo que más frena hoy tu captacion y tu visibilidad en la IA", "Critical/High: what most holds back your lead generation and AI visibility today")}</div>
        <div class="i"><span class="sw" style="background:{AMBER}"></span>{L("Medios: mejoras de indexación y experiencia", "Medium: indexing and experience improvements")}</div>
        <div class="i"><span class="sw" style="background:{CY6}"></span>{L("Menores: ajustes finos", "Minor: fine-tuning")}</div>
      </div></div>"""


def _plan_two_col(r: dict) -> str:
    """Un solo cuadro con todos los pasos juntos (2 columnas dentro del mismo box, sin fechas)."""
    plan = build_plan(r)
    mid = (len(plan) + 1) // 2
    cols = [plan[:mid], plan[mid:]]

    def side(items, start):
        return "".join(f'<li><span class="k">{start+i}</span><b>{p["text"]}</b></li>'
                       for i, p in enumerate(items))
    left = side(cols[0], 1)
    right = side(cols[1], mid + 1)
    return f"""<div class="block card">
      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td width="50%" valign="top" style="padding-right:14px"><ul class="actcol">{left}</ul></td>
        <td width="50%" valign="top" style="padding-left:14px;border-left:1px solid #eef1f4"><ul class="actcol">{right}</ul></td>
      </tr></table></div>"""


def _que_esperamos(r: dict) -> str:
    score = r.get("score", 0); tgt = _target(score)
    ai = r.get("geo_ai") or {}
    reconoce = L("que la IA te reconozca y te recomiende al pedir tu servicio", "for AI to recognize and recommend you when someone asks for your service") \
        if not (ai.get("knows_brand") and ai.get("recommended")) else L("consolidar tu presencia en la IA y ganar la categoría", "consolidating your presence in AI and winning the category")
    return f"""<div class="two" style="margin-top:2px">
      {_objetivo_box(score)}
      <div class="card"><h3>{L("Qué esperamos ver", "What we expect to see")}</h3>
      <p style="font-size:9.5px;color:#3d4855;line-height:1.55">{L("Primero: la web con textos propios por página, un titular claro y el móvil más rápido; tu marca ganando su propia búsqueda. Después:", "First: the site with original text per page, a clear headline and a faster mobile experience; your brand winning its own search. Then:")} {reconoce} {L("y primeras posiciones en búsquedas de tu categoría. Pasar de", "and top positions in searches for your category. Moving from")} <b style="color:{INK9}">{score}</b> {L("a", "to")} <b style="color:{GREEN}">~{tgt}</b> {L("de salud SEO/GEO es trabajo de textos, señales y contenido: rápido de mover y medible desde el primer día.", "in SEO/GEO health is a matter of text, signals and content: fast to move and measurable from day one.")}</p></div>
    </div>"""


def _porque_como(r: dict) -> str:
    """Por qué importa cada carencia (el 'como se corrige' va en el Plan de acción)."""
    m = r.get("meta", {}); s = r.get("signals", {})
    an = s.get("analytics") or {}
    por = []
    if not m.get("title") or not (25 <= len(m.get("title", "")) <= 65):
        por.append(("no", L("Con el <b>título</b> mal dimensionado, Google recorta o ignora como te presenta en los resultados.", "With a poorly sized <b>title</b>, Google trims or ignores how it presents you in the results.")))
    if not m.get("description"):
        por.append(("no", L("Sin <b>meta descripción</b>, Google inventa el resumen y la IA no tiene una frase clara con que citarte.", "Without a <b>meta description</b>, Google makes up the summary and AI has no clear sentence to quote you with.")))
    if m.get("h1_count", 0) != 1:
        por.append(("mid", L("El <b>H1</b> le dice a Google de que va la página; si falta o hay varios, se diluye el mensaje.", "The <b>H1</b> tells Google what the page is about; if it is missing or there are several, the message gets diluted.")))
    if not m.get("schema_types"):
        por.append(("no", L("Sin <b>datos estructurados</b>, la IA tiene cero etiquetas con que entender tu negocio y a quien sirves.", "Without <b>structured data</b>, AI has zero labels to understand your business and who you serve.")))
    if not m.get("has_sameas"):
        por.append(("mid", L("Tu <b>marca no esta conectada como entidad</b>: la IA no sabe si eres una empresa real y verificable.", "Your <b>brand is not connected as an entity</b>: AI cannot tell whether you are a real, verifiable company.")))
    if (m.get("img_total", 0) and m.get("img_alt", 0) / max(m["img_total"], 1) < 0.7):
        por.append(("mid", L("Imagenes <b>sin texto ALT</b>: menos resultados enriquecidos en Google y menos accesibilidad.", "Images <b>without ALT text</b>: fewer rich results in Google and less accessibility.")))
    if not s.get("llms_txt"):
        por.append(("mid", L("Sin <b>guia para IA (llms.txt)</b>, los buscadores con IA no saben que priorizar de tu sitio.", "Without an <b>AI guide (llms.txt)</b>, AI search engines do not know what to prioritize from your site.")))
    if not an.get("has_any"):
        por.append(("mid", L("Sin <b>analitica</b> no sabes que páginas convierten, así que no puedes mejorar con datos.", "Without <b>analytics</b> you do not know which pages convert, so you cannot improve with data.")))
    por = por[:5] or [("ok", L("La base on-page esta bien; quedan ajustes finos que refuerzan lo que ya funciona.", "The on-page basics are fine; only fine-tuning remains to reinforce what already works."))]

    def lis(items):
        out = ""
        for kind, txt in items:
            col = {"no": RED, "mid": AMBER, "ok": GREEN}[kind]
            ico = {"no": "&#10005;", "mid": "!", "ok": "&#10003;"}[kind]
            out += f'<li><span class="i" style="background:{col}">{ico}</span>{txt}</li>'
        return out
    return f"""<div class="block card">
      <div class="sectic" style="margin-bottom:8px">{L("Por qué importa cada carencia (como se corrige, en el plan de acción)", "Why each gap matters (how to fix it is in the action plan)")}</div>
      <ul class="chk">{lis(por)}</ul></div>"""


_STAT = {"crit": (RED, "&#10005;"), "hi": (OR7, "!"), "med": (AMBER, "!"), "ok": (GREEN, "&#10003;")}
_ORD = {"crit": 0, "hi": 1, "med": 2, "ok": 3}


def _check_list(items: list) -> str:
    """Lista limpia de comprobaciones (tabla, sin tarjetas), fallos primero."""
    items = sorted(items, key=lambda x: _ORD.get(x[2], 2))
    rows = ""
    for name, code, st, obs in items:
        col, ico = _STAT.get(st, (AMBER, "!"))
        rows += (f'<tr><td class="ckd"><span class="ckdot" style="background:{col}">{ico}</span></td>'
                 f'<td class="ckn"><b>{name}</b> <span class="ckcode" style="color:{col}">{code}</span></td>'
                 f'<td class="cko">{obs}</td></tr>')
    return f'<table class="cktbl"><tbody>{rows}</tbody></table>'


def _broken(r: dict):
    """404 del rastreo COMPLETO del sitio (mismo dato que la pantalla), no la muestra
    del home. Devuelve (count, checked, ejemplos)."""
    opb = (r.get("onpage") or {}).get("broken") or {}
    s = r.get("signals") or {}
    count = opb.get("count") if opb.get("count") is not None else (s.get("links_broken") or 0)
    checked = opb.get("checked") if opb.get("checked") is not None else (s.get("links_checked") or 0)
    ex = opb.get("broken") or s.get("broken_examples") or []
    return count, checked, ex


def _tech_rows(r: dict) -> str:
    s = r["signals"]; m = r["meta"]
    _bc, _bk, _ = _broken(r)
    rb = s.get("robots_info") or {}
    if rb.get("blocks_all"):
        robots_st, robots_code, robots_obs = "crit", L("Bloquea todo", "Blocks everything"), L("Disallow: / · Google no puede rastrear el sitio", "Disallow: / · Google cannot crawl the site")
    elif not s["robots"]:
        robots_st, robots_code, robots_obs = "hi", L("Falta", "Missing"), L("Sin robots.txt: no guias el rastreo de Google", "No robots.txt: you are not guiding Google's crawling")
    else:
        extra = f'{rb.get("disallow_count",0)} {L("reglas", "rules")}' + (L(", declara sitemap", ", declares sitemap") if rb.get("has_sitemap") else L(", no declara el sitemap", ", does not declare the sitemap"))
        robots_st = "ok" if rb.get("has_sitemap") else "med"
        robots_code, robots_obs = "OK", extra
    rows = [
        (L("Conexión segura (HTTPS)", "Secure connection (HTTPS)"), "OK" if s["https"] else L("Falla", "Fails"), "ok" if s["https"] else "crit",
         L("Certificado válido", "Valid certificate") if s["https"] else L("Sin candado de seguridad", "No security padlock")),
        (L("Respuesta del servidor", "Server response"), f'{s["home_status"]}', "ok" if s["home_status"] < 300 else "hi",
         f'{L("Responde en", "Responds in")} {s["home_time"]}s'),
        ("robots.txt", robots_code, robots_st, robots_obs),
        (L("Mapa del sitio (sitemap)", "Sitemap"), "OK" if s["sitemap"] else L("Falta", "Missing"), "ok" if s["sitemap"] else "hi",
         f'{s["sitemap_total"]} {L("URLs listadas", "URLs listed")}' if s["sitemap"] else L("No encontrado", "Not found")),
        (L("llms.txt (guia para IA)", "llms.txt (guide for AI)"), "OK" if s["llms_txt"] else "404", "ok" if s["llms_txt"] else "hi",
         L("Presente", "Present") if s["llms_txt"] else L("No existe: sin guia para los buscadores con IA", "Does not exist: no guide for AI search engines")),
        (L("Enlaces rotos (404)", "Broken links (404)"), f'{_bc}/{_bk}',
         "ok" if _bc == 0 else ("crit" if _bc >= 5 else "med"),
         L("Sin enlaces rotos en el rastreo", "No broken links in the crawl") if _bc == 0 else f'{_bc} {L("de", "of")} {_bk} {L("dan error", "return an error")}'),
        (L("Preparada para móvil", "Mobile-ready"), "OK" if m["viewport"] else L("Falta", "Missing"), "ok" if m["viewport"] else "med",
         L("Etiqueta viewport presente", "Viewport tag present") if m["viewport"] else L("Sin viewport móvil", "No mobile viewport")),
    ]
    return _check_list(rows)


def _robots_block(r: dict) -> str:
    """Recuadro con el análisis del robots.txt: que bloquea y que conviene bloquear."""
    rb = (r.get("signals") or {}).get("robots_info") or {}
    if not rb.get("present"):
        return (f'<div class="block callout o"><b>robots.txt.</b> {L("No encontramos robots.txt. Conviene publicarlo "
                "para guiar a Google (que rastree lo importante) y declarar ahi tu mapa del sitio.", "We could not find a robots.txt. It is worth publishing one "
                "to guide Google (so it crawls what matters) and to declare your sitemap there.")}</div>')
    parts = []
    tone = "o"
    if rb.get("blocks_all"):
        tone = "r"
        parts.append('<span style="color:' + RED + '"><b>' + L("Bloquea TODO el sitio (User-agent: * Disallow: /)", "Blocks the ENTIRE site (User-agent: * Disallow: /)") + '</b>: ' + L("Google no puede rastrearte. Corregir ya.", "Google cannot crawl you. Fix it now.") + '</span>')
    if rb.get("ai_blocked") and not rb.get("blocks_all"):
        tone = "r"
        parts.append('<span style="color:' + RED + '"><b>' + L("Bloqueas a los bots de IA", "You block the AI bots") + '</b> (' +
                     ", ".join(rb["ai_blocked"][:5]) + '): ' + L("la IA no puede leerte ni citarte. Permitir su rastreo.", "AI cannot read or cite you. Allow their crawling.") + '</span>')
    if rb.get("blocks_render") and not rb.get("blocks_all"):
        tone = "r"
        parts.append('<span style="color:' + RED + '"><b>' + L("Bloqueas CSS/JS que Google necesita para ver tu web", "You block CSS/JS Google needs to render your site") + '</b> (' +
                     ", ".join(rb["blocks_render"][:3]) + '): ' + L("sin esos recursos Google ve la página rota y te baja posiciones. Permitelos.", "without those resources Google sees the page broken and lowers your rankings. Allow them.") + '</span>')
    if rb.get("blocks_content") and not rb.get("blocks_all"):
        if tone != "r":
            tone = "o"
        parts.append(L("Bloqueas secciones de contenido (<b>", "You block content sections (<b>") + ", ".join(rb["blocks_content"][:3]) +
                     L("</b>): si ahi hay páginas que quieres posicionar, no aparecerán en Google. Bloquea solo lo que no debe indexarse (admin, carrito, búsquedas).",
                       "</b>): if those hold pages you want to rank, they will not show on Google. Block only what should not be indexed (admin, cart, search)."))
    if rb.get("good_blocks"):
        parts.append(L("Ya bloqueas bien ", "You already block well ") + ", ".join(rb["good_blocks"]) +
                     L(" (evita que Google gaste rastreo en páginas sin valor).", " (it keeps Google from spending its crawl budget on low-value pages)."))
    elif rb.get("disallow_sample"):
        parts.append(L("Hoy bloquea: <b>", "Currently blocks: <b>") + ", ".join(rb["disallow_sample"][:5]) + "</b>.")
    if rb.get("suggest_block"):
        parts.append(L("Conviene bloquear también ", "It is worth also blocking ") + ", ".join(rb["suggest_block"]) + ".")
    if not rb.get("has_sitemap"):
        parts.append(L("No declara el <b>sitemap</b> dentro del robots: anadirlo ayuda a que Google lo descubra antes.", "It does not declare the <b>sitemap</b> inside robots: adding it helps Google discover it sooner."))
    if not parts:
        parts.append(L("Bien configurado: guia el rastreo y declara el sitemap.", "Well configured: it guides crawling and declares the sitemap."))
    return f'<div class="block callout {tone}"><b>{L("Análisis del robots.txt.", "robots.txt analysis.")}</b> ' + " ".join(parts) + "</div>"


def _short_url(u: str) -> str:
    u = str(u or "")
    u = re.sub(r"^https?://", "", u)
    i = u.find("/")
    path = u[i:] if i >= 0 else "/"
    return path if len(path) <= 42 else path[:39] + "..."


def _onpage_multi(r: dict) -> str:
    """On-page sobre TODAS las páginas rastreadas, con URLs de ejemplo que fallan cada punto."""
    op = r.get("onpage") or {}
    iss = op.get("issues") or {}
    if not iss:
        return ""
    pages = op.get("pages_crawled") or (op.get("totals") or {}).get("pages") or (r.get("signals") or {}).get("pages_found") or 0

    def ex(key, n=3):
        e = (iss.get(key) or {}).get("examples") or []
        us = []
        for it in e[:n]:
            u = it.get("url") if isinstance(it, dict) else it
            s = _short_url(u)
            if s:
                us.append(s)
        return (L(" · p. ej. ", " · e.g. ") + ", ".join(us)) if us else ""

    def cnt(key):
        return (iss.get(key) or {}).get("count", 0)

    rows = []
    img = iss.get("img_no_alt") or {}
    ti = img.get("total_imgs", 0); miss = img.get("total", 0); ta = max(ti - miss, 0)
    cov = round(100 * ta / ti) if ti else 100
    if ti and miss:
        rows.append((L("Texto ALT en imágenes", "ALT text on images"), f"{cov}%", "ok" if cov >= 70 else ("med" if cov >= 30 else "hi"),
                     f"{ta}/{ti} {L('imágenes con ALT en todo el sitio', 'images with ALT across the site')}" + ex("img_no_alt")))
    if cnt("og_missing"):
        rows.append((L("Vista previa al compartir (Open Graph)", "Share preview (Open Graph)"), f'{cnt("og_missing")}', "hi",
                     f'{cnt("og_missing")} {L("páginas sin Open Graph", "pages without Open Graph")}' + ex("og_missing")))
    if cnt("title_missing") or cnt("title_bad_len"):
        n = cnt("title_missing") + cnt("title_bad_len")
        rows.append((L("Títulos de página", "Page titles"), f'{n}', "med",
                     f'{n} {L("páginas con título ausente o mal dimensionado", "pages with missing or poorly sized title")}' + (ex("title_missing") or ex("title_bad_len"))))
    if cnt("desc_missing"):
        rows.append((L("Meta descripciones", "Meta descriptions"), f'{cnt("desc_missing")}', "hi",
                     f'{cnt("desc_missing")} {L("páginas sin meta descripción", "pages without meta description")}' + ex("desc_missing")))
    if cnt("h1_missing") or cnt("h1_multiple"):
        n = cnt("h1_missing") + cnt("h1_multiple")
        rows.append((L("Titular principal (H1)", "Main heading (H1)"), f'{n}', "med",
                     f'{cnt("h1_missing")} {L("sin H1", "without H1")}, {cnt("h1_multiple")} {L("con varios", "with several")}' + (ex("h1_missing") or ex("h1_multiple"))))
    if cnt("thin"):
        rows.append((L("Contenido escaso (páginas pobres)", "Thin content (poor pages)"), f'{cnt("thin")}', "med",
                     f'{cnt("thin")} {L("páginas con muy poco texto propio", "pages with very little original text")}' + ex("thin")))
    if cnt("canonical_missing"):
        rows.append((L("URL canonica", "Canonical URL"), f'{cnt("canonical_missing")}', "med",
                     f'{cnt("canonical_missing")} {L("páginas sin canonical", "pages without canonical")}' + ex("canonical_missing")))
    if cnt("orphans"):
        rows.append((L("Páginas huerfanas", "Orphan pages"), f'{cnt("orphans")}', "med",
                     f'{cnt("orphans")} {L("páginas sin enlaces internos que apunten a ellas", "pages with no internal links pointing to them")}' + ex("orphans")))
    if cnt("no_schema"):
        rows.append((L("Datos estructurados (schema)", "Structured data (schema)"), f'{cnt("no_schema")}', "hi",
                     f'{cnt("no_schema")} {L("páginas sin schema", "pages without schema")}' + ex("no_schema")))
    if not rows:
        return ""
    intro = (f'<p class="sub" style="margin-bottom:10px">{L("Rastreamos", "We crawled")} <b>{pages}</b> '
             f'{L("páginas de tu sitio, una a una. Estos son los puntos a corregir con ejemplos reales de páginas que los fallan:", "pages of your site, one by one. These are the points to fix, with real examples of pages that fail them:")}</p>')
    return intro + _check_list(rows)


def _onpage_rows(r: dict) -> str:
    m = r["meta"]
    tl = len(m["title"]); dl = len(m["description"])
    rows = [
        (L("Título de la página", "Page title"), "OK" if 25 <= tl <= 65 else (L("Largo", "Long") if tl > 65 else (L("Corto", "Short") if tl else L("Falta", "Missing"))),
         "ok" if 25 <= tl <= 65 else ("med" if tl else "crit"), f'{tl} {L("caracteres", "characters")}'),
        (L("Titular principal (H1)", "Main heading (H1)"), "OK" if m["h1_count"] == 1 else (L("Varios", "Several") if m["h1_count"] > 1 else L("Falta", "Missing")),
         "ok" if m["h1_count"] == 1 else ("med" if m["h1_count"] > 1 else "crit"), f'{m["h1_count"]} {L("en la home", "on the home page")}'),
        (L("Meta descripción", "Meta description"), "OK" if 70 <= dl <= 165 else (L("Corta", "Short") if dl else L("Falta", "Missing")),
         "ok" if 70 <= dl <= 165 else ("med" if dl else "crit"), f'{dl} {L("caracteres", "characters")}'),
        (L("URL canonica", "Canonical URL"), "OK" if m["canonical"] else L("Falta", "Missing"), "ok" if m["canonical"] else "med",
         L("Presente", "Present") if m["canonical"] else L("Sin canonical", "No canonical")),
        (L("Vista previa (Open Graph)", "Share preview (Open Graph)"), "OK" if (m["og_title"] and m["og_image"]) else L("Incompleta", "Incomplete"),
         "ok" if (m["og_title"] and m["og_image"]) else "hi",
         L("Título e imagen", "Title and image") if (m["og_title"] and m["og_image"]) else L("Se comparte sin tarjeta", "Shared without a card")),
        (L("Datos estructurados (schema)", "Structured data (schema)"), "OK" if m["schema_types"] else L("Pobre", "Poor"), "ok" if m["schema_types"] else "hi",
         (", ".join(m["schema_raw_types"][:4]) if m["schema_types"] else L("Sin datos estructurados", "No structured data"))),
        (L("Idioma declarado", "Declared language"), "OK" if m["lang"] else L("Falta", "Missing"), "ok" if m["lang"] else "med",
         m["lang"] or L("Sin atributo lang", "No lang attribute")),
    ]
    it = m.get("img_total", 0); ia = m.get("img_alt", 0)
    cov = round(100 * ia / it) if it else 100
    rows.append((L("Texto ALT en imagenes", "ALT text on images"), "OK" if cov >= 70 else (L("Parcial", "Partial") if cov >= 30 else L("Pobre", "Poor")),
                 "ok" if cov >= 70 else ("med" if cov >= 30 else "hi"),
                 f"{ia}/{it} {L('imagenes con ALT', 'images with ALT')} ({cov}%)" if it else L("sin imagenes", "no images")))
    # Indexabilidad: meta robots noindex es CRITICO (te saca de Google y la IA)
    if m.get("robots_noindex"):
        rows.insert(0, (L("Indexabilidad (meta robots)", "Indexability (meta robots)"), "noindex", "crit",
                        L("META ROBOTS = noindex: le pides a Google y a la IA que NO te muestren", "META ROBOTS = noindex: you are asking Google and AI NOT to show you")))
    return _check_list(rows)


def _sitemap_comp_block(r: dict) -> str:
    comp = (r.get("signals") or {}).get("sitemap_comp")
    if not comp or not comp.get("total"):
        return ""
    labels = [("páginas", L("Páginas reales", "Real pages")), ("entradas", L("Noticias / blog", "News / blog")),
              ("etiquetas", L("Etiquetas / categorías", "Tags / categories")), ("fichas", L("Fichas / descargas", "Listings / downloads")),
              ("otras", L("Otras (feeds, adjuntos)", "Other (feeds, attachments)"))]
    trs = ""
    for k, lab in labels:
        n = comp.get(k, 0)
        if n:
            verdict = "ok" if k in ("páginas", "entradas") else "med"
            trs += f'<tr><td>{lab}</td><td class="c"><b>{n}</b></td><td class="c">{_pill(verdict, L("util", "useful") if verdict=="ok" else L("revisar", "review"))}</td></tr>'
    return f"""
      <div class="sectic" style="margin-top:12px">{L("Composicion del mapa del sitio", "Sitemap composition")} · {comp['total']} URLs</div>
      <table class="t"><thead><tr><th>{L("Tipo de URL", "URL type")}</th><th class="c">{L("Cuantas", "How many")}</th><th class="c">{L("Veredicto", "Verdict")}</th></tr></thead>
      <tbody>{trs}</tbody></table>"""


def _levels(r: dict) -> str:
    # Las MISMAS 9 dimensiones que ve el cliente en la pantalla (fuente única: dims.py)
    out = ""
    for d in (r.get("dims") or []):
        val = d.get("score", 0)
        col = _color(val)  # mismos umbrales que la web
        out += (f'<div class="lv"><div class="top"><span class="nm">{_esc(d.get("name",""))}</span>'
                f'<span class="vl" style="color:{col}">{val}</span></div>'
                f'<div class="tr"><div class="fl" style="width:{max(2,val)}%;background:{col}"></div></div></div>')
    return out


_ICO = {"Gemini": "GEM", "ChatGPT": "GPT", "Claude": "CLD"}


def _esc(t) -> str:
    return str(t or "").replace("<", "&lt;").replace(">", "&gt;")


def _clean_gap(t: str) -> str:
    """Limpia el texto libre de la IA: quita enlaces markdown, URLs sueltas y colas."""
    t = t or ""
    t = re.sub(r"\[([^\]]+)\]\((?:https?://)?[^)]+\)", r"\1", t)  # [txt](url) -> txt
    t = re.sub(r"\((?:https?://)?[a-z0-9.\-/_?=&%:]+\)", "", t, flags=re.I)  # (url)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    # corta si termina en palabra a medias tras un punto
    return t


def _geo_tactics(r: dict) -> list[str]:
    """Todo lo que hay que inyectarle a la IA para que te lea y te recomiende,
    guiado por las señales reales del sitio."""
    m = r.get("meta", {}); s = r.get("signals", {}); ai = r.get("geo_ai") or {}
    rb = s.get("robots_info") or {}
    st = [x.lower() for x in (m.get("schema_types") or [])]
    recg = ai.get("recognition") or ("strong" if ai.get("knows_brand") else ("weak" if ai.get("knows_with_web") else "none"))
    tips = []
    if rb.get("ai_blocked"):
        tips.append("<b>" + L("Permitir el rastreo de los bots de IA", "Allow the AI bots to crawl") + "</b> (" + L("hoy bloqueas ", "you currently block ") + ", ".join(rb["ai_blocked"][:4]) +
                    L("): si no pueden leerte, no pueden citarte. Es lo primero.", "): if they cannot read you, they cannot cite you. This comes first."))
    # COMO SE GANA EL RECONOCIMIENTO REAL POR LA IA (si hoy no te reconoce bien)
    if recg in ("none", "weak"):
        tips.append(L("<b>Ganar reconocimiento de la IA con presencia externa</b>: consigue que te MENCIONEN fuentes "
                    "que la IA lee (directorios de tu sector, prensa local, comparativas, medios), no solo tu web. "
                    "La IA reconoce a quien aparece citado por otros.", "<b>Earn AI recognition with an external presence</b>: get MENTIONED by sources "
                    "that AI reads (industry directories, local press, comparisons, media), not just your own site. "
                    "AI recognizes those who are cited by others."))
        tips.append(L("<b>Marca coherente en todas partes</b>: el MISMO nombre, dirección y teléfono (NAP) en tu web, "
                    "Google, redes y directorios. Las contradicciones hacen que la IA dude de quien eres.", "<b>Consistent brand everywhere</b>: the SAME name, address and phone (NAP) on your site, "
                    "Google, social media and directories. Inconsistencies make AI doubt who you are."))
    if ai.get("knows_brand") is False:
        tips.append(L("<b>Definir tu marca como entidad</b>: página 'Quienes somos' clara, sameAs a tus perfiles "
                    "oficiales y, si aplica, ficha en Wikidata/Wikipedia, para que la IA sepa quien eres sin darle tu web.", "<b>Define your brand as an entity</b>: a clear 'About us' page, sameAs to your official "
                    "profiles and, if applicable, a Wikidata/Wikipedia entry, so AI knows who you are without being given your site."))
    if not any(x in st for x in ("organization", "localbusiness", "professionalservice")) or not m.get("has_sameas"):
        tips.append(L("Marcar tu <b>ficha de empresa (Organization/LocalBusiness + sameAs)</b>: nombre, dirección, teléfono, zona y perfiles oficiales.", "Mark up your <b>company profile (Organization/LocalBusiness + sameAs)</b>: name, address, phone, area and official profiles."))
    if "faqpage" not in st and not m.get("has_faq"):
        tips.append(L("Anadir <b>Preguntas frecuentes con datos estructurados (FAQ schema)</b>: la IA cita respuestas directas de ahi.", "Add <b>Frequently Asked Questions with structured data (FAQ schema)</b>: AI quotes direct answers from there."))
    if not st or not (set(st) & {"faqpage", "organization", "localbusiness", "product", "article", "service"}):
        tips.append(L("Poner <b>datos estructurados utiles (schema)</b> de tus servicios/productos para que la IA entienda tu oferta.", "Add <b>useful structured data (schema)</b> for your services/products so AI understands your offering."))
    if m.get("word_count", 0) < 500:
        tips.append(L("Crear <b>una página por servicio</b> con contenido propio que responda las preguntas reales del cliente (la IA necesita texto que citar).", "Create <b>a page per service</b> with original content that answers the client's real questions (AI needs text to cite)."))
    if not (m.get("h1_count") == 1 and m.get("h2_count", 0) >= 3):
        tips.append(L("Ordenar la <b>estructura de titulares</b> (un H1 claro y varios H2 por tema) para que la IA extraiga tus respuestas.", "Organize your <b>heading structure</b> (one clear H1 and several H2 by topic) so AI can extract your answers."))
    if not m.get("has_contact"):
        tips.append(L("Mostrar una <b>ficha de contacto clara</b> (nombre, teléfono, dirección): la IA prioriza negocios verificables.", "Show a <b>clear contact block</b> (name, phone, address): AI prioritizes verifiable businesses."))
    dlen = len(m.get("description") or "")
    if not (70 <= dlen <= 165):
        tips.append(L("Escribir un <b>resumen citable (meta descripción)</b> de 70-160 caracteres por página: es lo que la IA usa para citarte.", "Write a <b>quotable summary (meta description)</b> of 70-160 characters per page: it is what AI uses to cite you."))
    if not s.get("llms_txt"):
        tips.append(L("Publicar <b>llms.txt</b> como guia para los buscadores con IA.", "Publish <b>llms.txt</b> as a guide for AI search engines."))
    # Reseñas: señal clave para que la IA recomiende. Adaptado a tu ficha real.
    gbp = ai.get("gbp"); gn = ai.get("gbp_reviews_n")
    if gbp is False:
        tips.append(L("Tu <b>ficha de Google Business con reseñas</b>: la IA recomienda a negocios con opiniones reales y buena valoración.", "Your <b>Google Business listing with reviews</b>: AI recommends businesses with real opinions and a good rating."))
    elif gbp and (gn is None or (isinstance(gn, int) and gn < 15)):
        tips.append(L("<b>Conseguir más reseñas en tu ficha de Google</b>: tienes ficha pero pocas valoraciones, y la IA prioriza a los negocios mejor valorados. Pide reseñas a tus clientes de forma sistemática.", "<b>Gather more reviews on your Google listing</b>: you have a listing but few ratings, and AI prioritizes the best-rated businesses. Ask your clients for reviews systematically."))
    else:
        tips.append(L("<b>Sumar reseñas y casos de exito verificables</b> (Google, directorios, prensa): la IA cita fuentes con reputación.", "<b>Add verifiable reviews and success stories</b> (Google, directories, press): AI cites reputable sources."))
    return tips[:8]


def _geo_plan_block(r: dict) -> str:
    """Bloque 'Todo lo que necesitas para salir en la IA' — al final, con el plan."""
    ai = r.get("geo_ai") or {}
    gap_c = _clean_gap(ai.get("gap")) if ai else ""
    tactics = _geo_tactics(r)
    if not tactics and not gap_c:
        return ""
    tac_lis = "".join(f'<li><span class="i" style="background:{CY6}">+</span>{t}</li>' for t in tactics)
    return (f'<div class="block card" style="border-color:{CY};border-width:2px;margin-top:12px">'
            f'<div class="sectic" style="margin-bottom:8px;color:{CY6}">{L("Todo lo que necesitas para salir en la IA", "Everything you need to show up in AI")}</div>'
            + (f'<p style="font-size:9.5px;color:#3d4855;line-height:1.55;margin-bottom:10px">{_esc(gap_c)}</p>' if gap_c else "")
            + f'<ul class="chk">{tac_lis}</ul></div>')


def _ai_section(r: dict) -> str:
    ai = r.get("geo_ai") or {}
    answered = ai.get("answered_names") or []
    if ai.get("limited"):
        return f"""
        <div class="eyebrow"><span class="bar"></span>04 · {L("Cómo te ve la inteligencia artificial", "How artificial intelligence sees you")}</div>
        <h2 class="sec">{L("Cómo te ve la IA", "How AI sees you")}</h2>
        <div class="block callout o">{L("En este análisis no pudimos completar la consulta en vivo a la IA (límite temporal del servicio). No significa que la IA no te reconozca; lo reintentamos. El resto del diagnóstico está completo.", "We couldn't complete the live AI query in this analysis (a temporary service limit). It doesn't mean AI doesn't recognize you; we'll retry. The rest of the diagnosis is complete.")}</div>"""
    if not (ai.get("available") and not ai.get("error") and answered):
        geo = r["categories"].get("geo", {})
        return f"""
        <div class="eyebrow"><span class="bar"></span>04 · {L("Cómo te ve la inteligencia artificial", "How artificial intelligence sees you")}</div>
        <h2 class="sec">{L("Como te ve la IA", "How AI sees you")}</h2>
        <p class="sub">{L("Medimos las señales que la IA usa para entenderte y citarte, y le preguntamos por ti en vivo.", "We measure the signals AI uses to understand and cite you, and we ask it about you live.")}</p>
        <div class="block scorewrap"><div class="gauge">{_gauge(geo.get('score',0), L('Preparación IA', 'AI readiness'))}</div>
        <div class="levels">{_levels(r)}</div></div>"""

    brand = ai.get("brand", r["domain"])
    country = ai.get("country") or ""
    zona = ai.get("zona") or country
    knows = ai.get("knows_brand"); reco = ai.get("recommended")
    recg = ai.get("recognition") or ("strong" if knows else ("weak" if ai.get("knows_with_web") else "none"))
    mentions = (ai.get("mentions") or ai.get("web_description") or ai.get("brand_description") or "").strip()
    comps = _comp_names(ai) or L("otras firmas de tu sector", "other firms in your industry")

    # Tarjeta 1: ¿la IA sabe quién eres? Mensaje claro y directo para el cliente.
    rec_tag = {"strong": L("SÍ TE CONOCE", "KNOWS YOU"), "weak": L("SOLO SI LE DAS TU WEB", "ONLY IF GIVEN YOUR SITE"), "none": L("NO TE CONOCE", "DOESN'T KNOW YOU")}[recg]
    rec_v = {"strong": "yes", "weak": "", "none": "no"}[recg]
    if recg == "strong":
        m_line = L("<b>Sí te conoce por su cuenta.</b> La IA sabe quién eres sin que le pases tu web, así que puede recomendarte cuando alguien pregunta por tu sector. Vas por delante de la mayoría.",
                   "<b>It knows you on its own.</b> AI knows who you are without being given your site, so it can recommend you when someone asks about your sector. You're ahead of most.")
        m_src = L("Reconocimiento por su cuenta (de memoria)", "Recognized on its own (from memory)")
    elif recg == "weak":
        m_line = L("<b>La IA no te conoce por su cuenta.</b> Solo sabe de ti si le das tu página web; por tu nombre no te tiene en memoria. Resultado: cuando un cliente le pregunta por tu servicio sin conocerte, <b>no apareces</b>. La meta es que te reconozca por tu nombre, sin darle la web.",
                   "<b>AI doesn't know you on its own.</b> It only knows you if you hand it your website; by name it doesn't have you in memory. So when a customer asks about your service without knowing you, <b>you don't show up</b>. The goal is for it to recognize you by name.")
        m_src = L("Solo te reconoce con tu web delante", "Only recognized with your site in front of it")
    else:
        m_line = L("<b>La IA no sabe quién eres.</b> Ni por tu nombre ni dándole tu web encuentra información fiable de tu marca. Hoy, para quien pregunta a la IA antes de comprar, es como si no existieras.",
                   "<b>AI doesn't know who you are.</b> Neither by name nor when given your site does it find reliable information about your brand. Today, for anyone who asks AI before buying, it's as if you didn't exist.")
        m_src = L("Sin rastro de tu marca en la IA", "No trace of your brand in AI")
    card1 = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L("¿La IA sabe quién es", "Does AI know who")} "{_esc(brand)}"{L("?", " is?")}</div>
        <div class="qt">{m_line}</div></div></div>
      <div class="src"><span>{m_src}</span><span class="v {rec_v}">{rec_tag}</span></div>
    </div>"""

    # Tarjeta ficha de Google Business (real, buscada por nombre y dominio)
    gbp = ai.get("gbp"); gbp_rev = _esc(ai.get("gbp_reviews") or "")
    gbp_n = ai.get("gbp_reviews_n")
    few_reviews = gbp and (gbp_n is None or (isinstance(gbp_n, int) and gbp_n < 15))
    gbp_card = ""
    if gbp is not None:
        if gbp and not few_reviews:
            gbp_card = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L("Tu ficha de Google Business / Maps", "Your Google Business / Maps listing")}</div>
        <div class="qt">{L("Encontramos tu ficha activa", "We found your active listing")}{(' (' + gbp_rev + ')') if gbp_rev else ''}. {L("Buenas reseñas: son una de las fuentes que la IA cita para recomendarte. Mantenlas y sigue pidiendo más.", "Good reviews: they are one of the sources AI cites to recommend you. Keep them up and keep asking for more.")}</div></div></div>
      <div class="src"><span>{L("Buscada por nombre en", "Searched by name in")} {_esc(zona)} {L("y por tu dominio", "and by your domain")}</span><span class="v yes">{L("TIENES FICHA", "YOU HAVE A LISTING")}</span></div>
    </div>"""
        elif gbp and few_reviews:
            gbp_card = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L("Tu ficha de Google Business / Maps", "Your Google Business / Maps listing")}</div>
        <div class="qt">{L("Tienes ficha", "You have a listing")}{(' (' + gbp_rev + ')') if gbp_rev else ''}, {L("pero <b>te faltan reseñas y valoraciones</b>. Las opiniones buenas son una de las señales que la IA usa para recomendarte: sin ellas, apareces por detras de la competencia mejor valorada. Hay que pedir reseñas a tus clientes de forma sistemática.", "but <b>you are short on reviews and ratings</b>. Good opinions are one of the signals AI uses to recommend you: without them, you appear behind better-rated competitors. You need to ask your clients for reviews systematically.")}</div></div></div>
      <div class="src"><span>{L("Buscada por nombre en", "Searched by name in")} {_esc(zona)} {L("y por tu dominio", "and by your domain")}</span><span class="v">{L("FALTAN RESEÑAS", "REVIEWS MISSING")}</span></div>
    </div>"""
        else:
            gbp_card = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L("Tu ficha de Google Business / Maps", "Your Google Business / Maps listing")}</div>
        <div class="qt">{L("No encontramos una ficha activa. Es clave para el mapa, las búsquedas locales y para que la IA te cite con reseñas reales.", "We could not find an active listing. It is key for the map, local searches and for AI to cite you with real reviews.")}</div></div></div>
      <div class="src"><span>{L("Buscada por nombre en", "Searched by name in")} {_esc(zona)} {L("y por tu dominio", "and by your domain")}</span><span class="v no">{L("SIN FICHA", "NO LISTING")}</span></div>
    </div>"""
    _realcomps = _comp_names(ai, 4)
    _c2_line = (f'{L("Nombró a:", "It named:")} {_realcomps}.' if _realcomps
                else L("En esta consulta no nos dio nombres concretos de competidores.", "In this query it gave us no specific competitor names."))
    _c2_v, _c2_lab = (("yes", L('TE RECOMIENDA', 'RECOMMENDS YOU')) if reco is True
                      else (("no", L('NO TE RECOMIENDA', 'DOES NOT RECOMMEND YOU')) if reco is False
                            else ("", L('SOLO A VECES', 'ONLY SOMETIMES'))))
    card2 = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L('Le preguntamos a la IA: "¿Qué empresas recomiendas para este servicio?"', 'We asked AI: "Which companies do you recommend for this service?"')}</div>
        <div class="qt">{_c2_line}</div></div></div>
      <div class="src"><span>{L("Consulta a la IA en vivo · sobre tu categoría (sin nombrarte)", "Live AI query · about your category (without naming you)")}</span>
        <span class="v {_c2_v}">{_c2_lab}</span></div>
    </div>"""

    # UNA búsqueda real de cliente como ejemplo (la IA repite los mismos competidores
    # en todas, así que mostrar más es redundante). El veredicto X/N sí resume las N.
    questions = ai.get("questions") or []
    q_cards = ""
    for q in questions[:1]:
        ap = q.get("appears")
        named = ", ".join(q.get("named", [])[:4]) or "—"
        vt = L("APARECES", "YOU APPEAR") if ap is True else (L("NO APARECES", "YOU DON'T APPEAR") if ap is False else L("SIN DATO", "NO DATA"))
        vc = "yes" if ap is True else ("no" if ap is False else "")
        q_cards += f"""
    <div class="aiq">
      <div class="q"><div class="ico">{L('IA','AI')}</div><div>
        <div class="ask">{L('Un cliente busca:', 'A customer searches:')} "{_esc(q.get('q',''))}"</div>
        <div class="qt">{L('La IA recomienda a:', 'AI recommends:')} {_esc(named)}.</div></div></div>
      <div class="src"><span>{L('Búsqueda de categoría', 'Category search')}{(' · ' + _esc(country)) if country else ''}</span>
        <span class="v {vc}">{vt}</span></div>
    </div>"""
    _hits = ai.get("reco_hits"); _tot = ai.get("reco_total")
    _hits_txt = (f' · {L("apareces en", "you appear in")} {_hits}/{_tot}'
                 if isinstance(_hits, int) and isinstance(_tot, int) and _tot else f' · {L("¿sales tú?", "do you show up?")}')
    q_block = (f'<div class="sectic" style="margin-top:12px">{L("Ejemplo de búsqueda real de un cliente", "Example of a real customer search")}'
               f'{(L(" en ", " in ") + _esc(country)) if country else ""}{_hits_txt}</div>{q_cards}') if q_cards else ""

    # Veredicto según reconocimiento + recomendacion (verde/ambar/rojo)
    if recg == "strong" and reco:
        topnote = L("<b>Buena señal:</b> la IA te reconoce y te incluye cuando piden tu servicio. Toca mantener la ventaja.",
                    "<b>Good signal:</b> AI recognizes you and includes you when people ask for your service. Now it's about keeping the edge.")
        vcol = "g"
        verdict = L("La IA te reconoce y te recomienda: vas por delante de la mayoria en tu zona.",
                    "AI recognizes and recommends you: you're ahead of most in your area.")
    elif recg == "none":
        topnote = L("<b>La IA no te encuentra.</b> Ni sabiendo tu nombre te reconoce: hoy no existes para quien "
                    "pregunta a la IA antes de comprar.",
                    "<b>AI can't find you.</b> Not even with your name does it recognize you: today you don't exist "
                    "for anyone who asks AI before buying.")
        vcol = "r"
        verdict = f'{L("La IA no sabe quién eres y recomienda a", "AI does not know who you are and recommends")} {comps}: {L("hoy no apareces cuando preguntan por tu servicio.", "today you do not show up when people ask for your service.")}'
    else:
        topnote = L("<b>La IA te lee, pero no te tiene de memoria.</b> Si le pasas tu web, te describe bien; pero cuando "
                    "un cliente pregunta por tu servicio sin conocerte, la IA no te menciona y nombra a la competencia. "
                    "Ahí es donde hoy se te escapan clientes.",
                    "<b>AI can read you, but does not remember you.</b> If you give it your site, it describes you well; but "
                    "when a customer asks for your service without knowing you, AI does not mention you and names competitors "
                    "instead. That is where you lose customers today.")
        vcol = "o"
        verdict = f'{L("La IA te reconoce a medias y cuando piden tu servicio nombra a", "AI half-recognizes you and when people ask for your service it names")} {comps}: {L("trabajemos para que te cite a ti primero.", "let us work so it cites you first.")}'

    # La matriz multi-IA de arriba ya resume reconocimiento/recomendación/citas por
    # motor, así que las tarjetas de detalle (card1/card2/ejemplo) sobran aquí. Dejamos
    # matriz + veredicto (la ficha de Google va en 'Presencia local').
    return f"""
    <div class="keep">
    <div class="eyebrow"><span class="bar"></span>04 · {L('Cómo te ve la inteligencia artificial', 'How artificial intelligence sees you')}</div>
    <h2 class="sec">{L('Cómo te ve la IA cuando preguntan por ti', 'How AI sees you when people ask about you')}</h2>
    <p class="sub">{L('Le preguntamos EN VIVO a varias IA (ChatGPT, Perplexity y Gemini): por tu marca, por tu servicio y con búsquedas reales de cliente en', 'We asked several AIs LIVE (ChatGPT, Perplexity and Gemini): about your brand, your service and with real customer searches in')} {_esc(zona or L('tu zona','your area'))}. {L('Cada vez más gente busca así antes de decidir.', 'More and more people search this way before deciding.')}</p>
    {_ai_matrix(r)}
    <div class="block callout {vcol}"><b>{L('Veredicto IA.', 'AI verdict.')}</b> {verdict}</div>
    </div>"""


def _ai_matrix(r: dict) -> str:
    """Matriz multi-IA (ChatGPT / Perplexity / Gemini): ¿te reconoce? ¿te recomienda?
    ¿te cita? — medido en vivo en cada motor. Estilo nativo del PDF (tabla)."""
    ai = r.get("geo_ai") or {}
    engines = ai.get("engines") or []
    if not engines:
        return ""

    def rec_cell(e):
        rg = e.get("recognition")
        if rg == "strong":
            return GREEN, L("Sí", "Yes")
        if rg == "weak":
            return AMBER, L("A medias", "Partly")
        if rg == "none":
            return RED, "No"
        return (GREEN, L("Sí", "Yes")) if e.get("knows") else (RED, "No")

    def reco_cell(e):
        rc = e.get("recommended")
        h, t = e.get("reco_hits"), e.get("reco_total")
        cnt = f" ({h}/{t})" if isinstance(h, int) and isinstance(t, int) and t else ""
        if rc is True:
            return GREEN, L("Sí", "Yes") + cnt
        if rc is False:
            return RED, "No" + cnt
        return AMBER, L("A veces", "Sometimes") + cnt

    def cite_cell(e):
        n = e.get("cites") or 0
        col = GREEN if n >= 2 else (AMBER if n == 1 else RED)
        txt = (f"{n} " + L("fuentes", "sources")) if n != 1 else ("1 " + L("fuente", "source"))
        return col, txt

    def chip(cv):
        return f'<span style="color:{cv[0]};font-weight:700">{_esc(cv[1])}</span>'

    hcell = 'padding:6px 10px;font-size:8px;letter-spacing:.06em;color:#7b8694'
    dcell = 'padding:7px 10px;border-top:1px solid #e7ebf0;text-align:center;font-size:10.5px'
    rows = ""
    for e in engines:
        rows += (f'<tr><td style="padding:7px 10px;border-top:1px solid #e7ebf0;font-weight:700;color:{INK9};font-size:11px">{_esc(e.get("name",""))}</td>'
                 f'<td style="{dcell}">{chip(rec_cell(e))}</td>'
                 f'<td style="{dcell}">{chip(reco_cell(e))}</td></tr>')
    legend = (f'<div style="font-size:8.5px;color:#7b8694;margin-top:7px;line-height:1.55">'
              f'<b>{L("¿Te reconoce?","Knows you?")}</b> {L("si la IA sabe quién eres al preguntar por tu marca (Sí / a medias, solo con tu web / no).","whether the AI knows who you are when asked about your brand (Yes / partly, only with your site / no).")} '
              f'<b>{L("¿Te recomienda?","Recommends you?")}</b> {L("si te incluye cuando un cliente pide tu servicio SIN nombrarte; el (X/N) es en cuántas búsquedas reales apareces.","whether it includes you when a customer asks for your service WITHOUT naming you; (X/N) is in how many real searches you appear.")}</div>')
    srcrows = ""
    for e in engines:
        proof = (e.get("proof") or "").strip()
        ss = [s for s in (e.get("sources") or []) if isinstance(s, dict) and s.get("domain")][:8]
        if not proof and not ss:
            continue
        blk = f'<div style="margin-top:6px"><b style="font-size:9px;color:{INK9}">{_esc(e.get("name",""))}</b>'
        if proof:
            blk += f' <span style="font-size:9px;color:#39434f">— "{_esc(proof)}"</span>'
        if ss:
            def _disp(s):
                u = s.get("url") or ("https://" + s["domain"])
                d = re.sub(r"^https?://(www\.)?", "", u).rstrip("/")
                return d[:44] + "…" if len(d) > 44 else d
            links = " · ".join(
                f'<a href="{_esc(s.get("url") or ("https://" + s["domain"]))}" style="color:#0f9bc2;text-decoration:underline">{_esc(_disp(s))}</a>'
                for s in ss)
            blk += (f'<div style="font-size:8px;color:#7b8694;margin-top:1px">{L("fuentes (página exacta que te cita)","sources (exact page that cites you)")}: {links}</div>')
        blk += "</div>"
        srcrows += blk
    srcblock = (f'<div style="margin-top:10px">'
                f'<div class="mono" style="font-size:7.5px;letter-spacing:.06em;color:#9aa4b0;margin-bottom:1px">{L("PRUEBA REAL · LO QUE DICE CADA IA DE TI Y LAS FUENTES QUE USA (HAZ CLIC PARA COMPROBAR)","REAL PROOF · WHAT EACH AI SAYS ABOUT YOU AND THE SOURCES IT USES (CLICK TO VERIFY)")}</div>{srcrows}</div>') if srcrows else ""
    return (f'<div class="sectic" style="margin-top:10px">{L("Cómo te ven las distintas IA (medido en vivo)", "How the different AIs see you (measured live)")}</div>'
            f'<table role="presentation" width="100%" style="border-collapse:collapse;border:1px solid #e7ebf0">'
            f'<tr style="background:#f7f9fb">'
            f'<td class="mono" style="{hcell}">IA</td>'
            f'<td class="mono" style="{hcell};text-align:center">{L("¿TE RECONOCE?","KNOWS YOU?")}</td>'
            f'<td class="mono" style="{hcell};text-align:center">{L("¿TE RECOMIENDA?","RECOMMENDS YOU?")}</td>'
            f'</tr>{rows}</table>{legend}{srcblock}')


def _crawl_structure_block(r: dict) -> str:
    """Estructura del sitio medida por NUESTRO rastreo (fiable, no depende de
    buscadores): cuantas páginas tiene y cuantas revisamos una a una."""
    s = r.get("signals") or {}
    pf = s.get("pages_found", 0)
    broken, checked, _ = _broken(r)
    if not pf and not checked:
        return ""
    sm = s.get("sitemap_total", 0)
    fuente = (f'{L("según tu mapa del sitio", "according to your sitemap")} ({sm} URLs)' if sm else L("por los enlaces internos de tu web", "from your site's internal links"))
    rota = (f' {L("De ellas,", "Of those,")} <b>{broken}</b> {L("daban error 404.", "returned a 404 error.")}' if broken else L(" No encontramos enlaces rotos.", " We found no broken links."))
    return (f'<div class="block callout o"><b>{L("Estructura de tu sitio (rastreo página por página).", "Your site structure (page-by-page crawl).")}</b> '
            f'{L("Tu web tiene del orden de", "Your site has around")} <b>{pf}</b> {L("páginas", "pages")} {fuente}. {L("Revisamos", "We checked")} {checked} {L("una a una.", "one by one.")}{rota} '
            f'{L("El número exacto que Google tiene indexado se confirma con Search Console (lo activamos al empezar).", "The exact number Google has indexed is confirmed with Search Console (we enable it at the start).")}</div>')


def _index_block(r: dict) -> str:
    ix = r.get("indexation")
    if not ix:
        return ""
    n = ix.get("sample_count", 0)
    tot = ix.get("sitemap_total", 0)
    prov = ix.get("provider", L("el buscador", "the search engine"))
    if not ix.get("indexed") and not isinstance(ix.get("indexed_estimate"), int):
        # medición con buscador proxy poco fiable: NO afirmamos que no estás indexado
        return ('<div class="block callout o"><b>' + L("Indexación.", "Indexing.") + '</b> ' + L("No pudimos confirmar tu indexación de forma automática (medimos con un buscador proxy, no con Google directo). El número exacto de páginas que Google tiene indexadas se confirma en Search Console (Cobertura), que activamos al empezar.", "We couldn't confirm your indexing automatically (we measure with a proxy engine, not Google directly). The exact number of pages Google has indexed is confirmed in Search Console (Coverage), which we enable at the start.") + '</div>')
    est = ix.get("indexed_estimate")
    concl = ix.get("conclusion") or ""
    extra = f' {L("Tu mapa del sitio lista", "Your sitemap lists")} {tot} URLs.' if tot else ""
    est_txt = f' {L("El buscador indexa del orden de", "The search engine indexes around")} <b>{est}</b> {L("páginas.", "pages.")}' if isinstance(est, int) else ""
    bi = ix.get("broken_indexed") or []
    base = ('<div class="block callout o"><b>' + L("Indexación (comprobada con navegador propio via site:).", "Indexing (checked with our own browser via site:).") + '</b> '
            + f'{L("Rastreamos", "We crawled")} {_esc(prov)} {L("página por página.", "page by page.")}{est_txt}{extra}'
            + (f' {_esc(concl)}' if concl else ' ' + L("El número exacto se confirma con Search Console.", "The exact number is confirmed with Search Console."))
            + '</div>')
    if bi:
        trs = "".join(f'<tr><td class="u">{_esc(b["url"])}</td><td class="c">{b["status"]}</td></tr>' for b in bi[:6])
        base += ('<div class="block callout r"><b>' + L("Páginas indexadas que dan error (404).", "Indexed pages returning an error (404).") + '</b> ' + L("Google las tiene "
                 "indexadas pero ya no existen: hay que redirigirlas o recuperarlas.", "Google has them "
                 "indexed but they no longer exist: they must be redirected or restored.") + '</div>'
                 f'<table class="t"><thead><tr><th>{L("Página indexada", "Indexed page")}</th><th class="c">{L("Estado", "Status")}</th></tr></thead><tbody>{trs}</tbody></table>')
    return base


_SEC_EXPLAIN = [
    ("hsts", ("Obliga al navegador a usar siempre HTTPS: evita que intercepten la conexión.",
              "Forces the browser to always use HTTPS: prevents the connection from being intercepted.")),
    ("content-security", ("Controla que scripts y recursos puede cargar tu web: frena inyecciones y robo de datos.",
                          "Controls which scripts and resources your site can load: stops injections and data theft.")),
    ("x-frame", ("Impide que tu web se incruste en otra para engañar al usuario (clickjacking).",
                 "Prevents your site from being embedded in another to trick the user (clickjacking).")),
    ("x-content-type", ("Evita que el navegador interprete archivos como algo que no son (sniffing).",
                        "Stops the browser from interpreting files as something they are not (sniffing).")),
    ("referrer", ("Controla que información se envia al salir de tu web (privacidad del usuario).",
                  "Controls what information is sent when leaving your site (user privacy).")),
    ("permissions", ("Limita el acceso a cámara, micrófono o ubicación: reduce la superficie de ataque.",
                     "Limits access to camera, microphone or location: reduces the attack surface.")),
]


def _sec_explain(name: str) -> str:
    n = (name or "").lower()
    for key, (es, en) in _SEC_EXPLAIN:
        if key in n:
            return L(es, en)
    return L("Cabecera de seguridad recomendada.", "Recommended security header.")


def _security_section(r: dict) -> str:
    sec = (r.get("signals") or {}).get("security")
    if not sec:
        return ""
    score = sec.get("score", 0)
    # Cada cabecera EXPLICADA (que hace y por qué importa)
    rows = [(n, "OK", "ok", _sec_explain(n)) for n in sec.get("headers_present", [])]
    rows += [(n, L("Falta", "Missing"), "hi", _sec_explain(n)) for n in sec.get("headers_missing", [])]
    checks = _check_list(rows) if rows else ""

    exposed_html = ""
    if sec.get("exposed"):
        trs = "".join(f'<tr><td class="u">{_esc(e["path"])}</td><td>{_esc(e["what"])}</td></tr>'
                      for e in sec["exposed"])
        exposed_html = ('<div class="block callout r"><b>' + L("Vulnerabilidad: archivos sensibles accesibles ahora mismo.", "Vulnerability: sensitive files accessible right now.") + '</b> '
                        + L("Cualquiera puede abrirlos sin permiso:", "Anyone can open them without permission:") + '</div>'
                        f'<table class="t"><thead><tr><th>{L("Ruta expuesta", "Exposed path")}</th><th>{L("Riesgo", "Risk")}</th></tr></thead><tbody>{trs}</tbody></table>')
    bits = []
    if sec.get("cms"):
        bits.append(f"{L('Tecnologia detectada:', 'Detected technology:')} <b>{_esc(sec['cms'])}</b>.")
    if sec.get("leaks"):
        bits.append(L("El servidor revela:", "The server reveals:") + " <b>" + ", ".join(_esc(x) for x in sec["leaks"][:3]) + "</b> " + L("(conviene ocultarlo).", "(best to hide it)."))
    if sec.get("cookie_flags"):
        bits.append(L("Cookies:", "Cookies:") + " " + ", ".join(sec["cookie_flags"]) + ".")
    tech_html = ('<div class="block callout o">' + " ".join(bits) + "</div>") if bits else ""

    return f"""
    <div class="keep">
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Seguridad y tecnologia', 'Security and technology')}</div>
    <h2 class="sec">{L('Qué tan segura y protegida está tu web', 'How safe and protected your site is')}</h2>
    <p class="sub">{L('Revisamos cabeceras de seguridad, fugas de versión del servidor y archivos sensibles accesibles.', 'We check security headers, server versión leaks and accessible sensitive files.')}
    {L('Una web insegura pierde confianza de clientes y de Google. Nota de seguridad:', 'An insecure site loses the trust of customers and of Google. Security score:')} <b style="color:{_color(score)}">{score}/100</b>.</p>
    {exposed_html}
    <div class="sectic" style="margin-top:8px">{L('Cabeceras de seguridad', 'Security headers')}</div>
    </div>
    {checks}
    {tech_html}"""


def _local_section(r: dict) -> str:
    """Presencia local y reputación: ficha de Google, reseñas, NAP, mapa. Es lo que
    te hace salir en el mapa y en 'cerca de mi', y de lo que más mira la IA local."""
    ai = r.get("geo_ai") or {}; m = r.get("meta") or {}
    gbp = ai.get("gbp"); gn = ai.get("gbp_reviews_n")
    _rat = ai.get("gbp_rating")
    grev = ai.get("gbp_reviews") or ""
    if not grev and (gn or _rat is not None):   # arma "4,9★ · 12 reseñas" con el dato del script
        grev = ((f"{str(_rat).replace('.', ',')}★ · " if _rat is not None else "")
                + (f"{gn} " + L("reseñas", "reviews") if gn else L("sin reseñas", "no reviews")))
    has_phone = m.get("has_phone"); has_addr = m.get("has_address")
    has_map = m.get("has_map"); has_hours = m.get("has_hours")
    # Si no hay ninguna señal local medida, no forzamos la sección
    if gbp is None and not any(x is not None for x in (has_phone, has_addr, has_map, has_hours)):
        return ""
    rows = []
    if gbp is True and (gn is None or (isinstance(gn, int) and gn >= 15)):
        rows.append((L("Ficha de Google Business", "Google Business listing"), "OK", "ok",
                     L("Activa", "Active") + (f" ({_esc(grev)})" if grev else "")))
    elif gbp is True:
        rows.append((L("Ficha de Google Business", "Google Business listing"), "OK", "ok", L("Activa", "Active")))
        rows.append((L("Reseñas y valoraciones", "Reviews and ratings"), L("Pocas", "Few"), "hi",
                     (f"{_esc(grev)} · " if grev else "") + L("faltan reseñas: la IA y los clientes priorizan negocios mejor valorados", "reviews missing: AI and customers prioritize better-rated businesses")))
    elif gbp is False:
        rows.append((L("Ficha de Google Business", "Google Business listing"), L("Falta", "Missing"), "crit",
                     L("Sin ficha no sales en el mapa ni en búsquedas locales", "Without a listing you do not show on the map or in local searches")))
    if has_phone is not None or has_addr is not None:
        nap_ok = bool(has_phone and has_addr)
        rows.append((L("Datos de contacto (NAP)", "Contact info (NAP)"), "OK" if nap_ok else L("Incompleto", "Incomplete"),
                     "ok" if nap_ok else "med",
                     L("Nombre, dirección y teléfono visibles", "Name, address and phone visible") if nap_ok else L("Falta teléfono o dirección clara", "Missing phone or clear address")))
    if has_map is not None:
        rows.append((L("Mapa de ubicación", "Location map"), "OK" if has_map else L("Falta", "Missing"), "ok" if has_map else "med",
                     L("Google Maps incrustado", "Google Maps embedded") if has_map else L("Sin mapa en contacto: ayuda a llegar y refuerza lo local", "No map on contact: it helps people arrive and reinforces local signal")))
    if has_hours is not None:
        rows.append((L("Horario de atención", "Opening hours"), "OK" if has_hours else L("Falta", "Missing"), "ok" if has_hours else "med",
                     L("Horario publicado", "Hours published") if has_hours else L("Sin horario visible", "No visible hours")))
    if not rows:
        return ""
    return f"""
    <div class="keep">
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Presencia local y reputación', 'Local presence and reputation')}</div>
    <h2 class="sec">{L('Cómo te encuentran cerca de ti (y por qué las reseñas mandan)', 'How people find you nearby (and why reviews rule)')}</h2>
    <p class="sub">{L('Tu presencia local es lo que te hace salir en el mapa y en las búsquedas "cerca de mi", y de lo que más miran los clientes y la IA para elegir. En rojo lo que falta, en verde lo que ya tienes.', 'Your local presence is what puts you on the map and in "near me" searches, and one of the top things customers and AI look at to choose. In red what is missing, in green what you already have.')}</p>
    {_check_list(rows)}
    </div>"""


def _content_section(r: dict) -> str:
    """Contenido y relevancia (E-E-A-T): palabras clave, autor, 'sobre nosotros',
    frescura y duplicados. Es lo que hace que Google y la IA te tomen como referencia."""
    c = r.get("content_ai") or {}; ai = r.get("geo_ai") or {}
    oc = (r.get("onpage") or {}).get("content") or {}
    kws = c.get("keywords") or ai.get("keywords") or []
    has_author = oc.get("author") if oc else c.get("has_author")
    has_about = oc.get("about_page") if oc else c.get("has_about")
    # frescura: bien si buena parte de las páginas con fecha son recientes
    freshness = None
    if oc:
        dated = oc.get("dated_pages") or 0; fresh = oc.get("fresh_pages") or 0
        if dated:
            freshness = "fresh" if fresh >= max(1, round(dated * 0.4)) else "stale"
    elif c.get("freshness") is not None:
        freshness = c.get("freshness")
    dups = ((oc.get("duplicates") or {}).get("count", 0) > 0) if oc else bool(c.get("duplicates"))
    if not kws and has_author is None and has_about is None and freshness is None:
        return ""
    rows = []
    if has_about is not None:
        rows.append((L("Página 'Sobre nosotros'", "'About us' page"), "OK" if has_about else L("Falta", "Missing"), "ok" if has_about else "hi",
                     L("Genera confianza (E-E-A-T) para Google, la IA y el cliente", "Builds trust (E-E-A-T) for Google, AI and the customer")))
    if has_author is not None:
        rows.append((L("Autor en los contenidos", "Author on content"), "OK" if has_author else L("Falta", "Missing"), "ok" if has_author else "med",
                     L("Firmar da autoridad (E-E-A-T)", "Signing content gives authority (E-E-A-T)") if not has_author else L("Contenidos firmados", "Signed content")))
    if freshness is not None:
        fr_ok = str(freshness).lower() in ("fresh", "ok", "buena", "reciente") or freshness is True
        rows.append((L("Frescura del contenido", "Content freshness"), "OK" if fr_ok else L("Antiguo", "Stale"), "ok" if fr_ok else "med",
                     L("Contenido actualizado y con fecha", "Up-to-date, dated content") if fr_ok else L("Actualiza y fecha tus artículos clave", "Update and date your key articles")))
    if dups:
        rows.append((L("Contenido duplicado", "Duplicate content"), L("Revisar", "Review"), "med",
                     L("Páginas casi identicas reparten tu fuerza en Google", "Near-identical pages split your strength in Google")))
    kw_block = ""
    if kws:
        chips = "".join(f'<span class="kw">{_esc(k)}</span>' for k in kws[:6])
        kw_block = (f'<div class="sectic" style="margin-top:10px">{L("Palabras clave objetivo (según la IA)", "Target keywords (per AI)")}</div>'
                    f'<div class="kwrow">{chips}</div>'
                    f'<p class="note" style="margin-top:6px">{L("Refuerza una página por tema para dominarlas. El volumen exacto se confirma con una herramienta de keywords.", "Reinforce one page per topic to own them. Exact volume is confirmed with a keyword tool.")}</p>')
    checks = _check_list(rows) if rows else ""
    return f"""
    <div class="keep">
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Contenido y relevancia', 'Content and relevance')}</div>
    <h2 class="sec">{L('Si tu contenido es la referencia, Google y la IA te citan', 'If your content is the reference, Google and AI cite you')}</h2>
    <p class="sub">{L('Analizamos si tu contenido apunta a lo que buscan tus clientes y si transmite confianza (autor, "sobre nosotros", frescura). Es lo que separa "una web más" de "la referencia del sector".', 'We analyze whether your content targets what your customers search and whether it conveys trust (author, "about", freshness). It is what separates "just another site" from "the sector reference".')}</p>
    {checks}{kw_block}
    </div>"""


def _cwv_strip(psi: dict) -> str:
    """Tira llamativa de Core Web Vitals (LCP/CLS/INP) móvil vs escritorio, con
    valores grandes y legibles y un semaforo claro. Es lo que Google usa para rankear."""
    order = [("lcp", "LCP", L("Carga del contenido", "Content load"), "&lt;2,5 s"),
             ("cls", "CLS", L("Estabilidad visual", "Visual stability"), "&lt;0,1"),
             ("inp", "INP", L("Respuesta al tocar", "Tap response"), "&lt;200 ms")]
    st_col = {"ok": GREEN, "warn": AMBER, "bad": RED}
    st_lab = {"ok": L("Bien", "Good"), "warn": L("Justo", "Fair"), "bad": L("Malo", "Poor")}

    def dev_card(dev_lab, dev_col, d):
        cwv = (d or {}).get("cwv") or {}
        perf = d.get("performance") if d else None
        cells = ""
        for k, code, name, ideal in order:
            mv = cwv.get(k) or {}
            val = mv.get("v", "—"); stt = mv.get("state", "na")
            c = st_col.get(stt, "#c9d1da"); lab = st_lab.get(stt, "—")
            cells += (f'<td class="cwvc"><div class="cwvk">{code}</div>'
                      f'<div class="cwvv" style="color:{c}">{val}</div>'
                      f'<div class="cwvs" style="background:{c}">{lab}</div>'
                      f'<div class="cwvn">{name}<br><span>{L("ideal","ideal")} {ideal}</span></div></td>')
        pc = _color(perf if perf is not None else 0)
        return (f'<div class="cwvcard"><div class="cwvhd"><span class="cwvdev" style="background:{dev_col}">{dev_lab}</span>'
                f'<span class="cwvsc" style="color:{pc}">{perf if perf is not None else "—"}<i>/100</i></span></div>'
                f'<table class="cwvt"><tr>{cells}</tr></table></div>')

    m = psi.get("mobile"); d = psi.get("desktop")
    if not ((m and m.get("cwv")) or (d and d.get("cwv"))):
        return ""
    cards = ""
    if m and m.get("cwv"):
        cards += dev_card(L("MOVIL", "MOBILE"), RED if (m.get("performance") or 0) < 60 else GREEN, m)
    if d and d.get("cwv"):
        cards += dev_card(L("ESCRITORIO", "DESKTOP"), GREEN if (d.get("performance") or 0) >= 80 else AMBER, d)
    return f'<div class="block two" style="margin:2px 0 12px">{cards}</div>'


def _speed_section(r: dict) -> str:
    psi = r.get("psi_full")
    if not psi or (not psi.get("mobile") and not psi.get("desktop")):
        return ""

    def _num(v):
        try:
            return float(re.sub(r"[^0-9.,]", "", str(v)).replace(",", "."))
        except Exception:  # noqa: BLE001
            return None

    def _metric_bar(k, val):
        n = _num(val)
        # (bueno, malo) por métrica; en segundos salvo TBT (ms) y SI
        limits = {"fcp": (1.8, 3.0), "lcp": (2.5, 4.0), "tbt": (200, 600), "si": (3.4, 5.8)}
        good, bad = limits.get(k, (2.5, 4.0))
        if n is None:
            return "#c9d1da", 30
        if n <= good:
            return GREEN, min(35 + n / good * 25, 60)
        if n <= bad:
            return AMBER, 62 + (n - good) / (bad - good) * 20
        return RED, min(84 + (n - bad) / bad * 16, 100)

    def card(title, d, mark):
        if not d:
            return (f'<div class="mini"><h4>{title}</h4>'
                    f'<p style="font-size:9px;color:#7b8694">{L("Medicion no disponible en esta prueba.", "Measurement not available in this test.")}</p></div>')
        perf = d.get("performance")
        col = _color(perf if perf is not None else 0)
        rows = ""
        for lab, k in [(L("Primer contenido (FCP)", "First content (FCP)"), "fcp"), (L("Contenido principal (LCP)", "Main content (LCP)"), "lcp"),
                       (L("Bloqueo por código (TBT)", "Code blocking (TBT)"), "tbt"), (L("Indice de velocidad", "Speed index"), "si")]:
            val = d.get(k) or "-"
            bcol, w = _metric_bar(k, val)
            rows += (f'<div class="hbar" style="margin:5px 0"><div class="l" style="width:44%;font-size:8.5px">{lab}</div>'
                     f'<div class="tk" style="height:9px"><div class="fl" style="width:{w}%;background:{bcol}"></div></div>'
                     f'<div class="st" style="width:44px;color:{bcol}">{val}</div></div>')
        return f"""<div class="mini">
          <h4>{title} <span style="margin-left:auto;font-size:22px;font-weight:800;color:{col}">{perf if perf is not None else '-'}<span style="font-size:9px;color:#7b8694">/100</span></span></h4>
          <div class="hbars" style="margin-top:6px">{rows}</div></div>"""

    m = psi.get("mobile"); d = psi.get("desktop")
    mob_p = (m or {}).get("performance")
    speed_lead = (L("La velocidad es de los pocos factores que Google confirma como criterio de posicionamiento, y es "
                    "la primera impresión de tu cliente. La medimos con los Core Web Vitals (las métricas oficiales de "
                    "Google), en móvil y escritorio por separado: el móvil suele ir con peor conexión y un procesador "
                    "más lento, y además Google indexa primero la versión móvil.",
                    "Speed is one of the few factors Google confirms as a ranking criterion, and it is your customer's "
                    "first impression. We measure it with Core Web Vitals (Google's official metrics), on mobile and "
                    "desktop separately: mobile usually has a worse connection and slower CPU, and Google indexes the "
                    "mobile versión first."))

    # Lectura de negocio dinamica según la nota móvil
    if mob_p is not None and mob_p < 60:
        insight = L("La buena noticia: tu servidor responde rápido y la página no salta mientras carga. El problema esta "
                    "concentrado en el móvil, donde la primera vista tarda demasiado en pintarse. Es de los arreglos más "
                    "rentables: se resuelve optimizando la imagen principal y aligerando el código, sin rehacer la web.",
                    "Good news: your server responds fast and the page does not shift while loading. The problem is "
                    "concentrated on mobile, where the first view takes too long to paint. It is one of the most profitable "
                    "fixes: solved by optimizing the main image and lightening the code, without rebuilding the site.")
    else:
        insight = L("Tu web carga rápido y de forma estable en ambos dispositivos: una buena experiencia que juega a tu "
                    "favor en Google y con el visitante. Toca mantenerlo al anadir contenido y campanas.",
                    "Your site loads fast and stably on both devices: a good experience that works in your favor with "
                    "Google and the visitor. Keep it up as you add content and campaigns.")
    return f"""
    <div class="keep">
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Rendimiento · Core Web Vitals', 'Performance · Core Web Vitals')}</div>
    <h2 class="sec">{L('La velocidad de tu web', 'Your site speed')}</h2>
    <p class="sub">{speed_lead}</p>
    {_cwv_strip(psi)}
    </div>
    <div class="block callout o">{insight}</div>"""


def _google_section(r: dict) -> str:
    g = r.get("google")
    if not g:
        return ""
    bq = g.get("brand_query") or {}
    bpos = bq.get("position")
    brand_verdict = (f'{L("apareces el", "you appear at")} <b>nº{bpos}</b>' if bpos else L("<b>no apareces</b> ni al buscar tu propio nombre", "<b>you don't appear</b> even when searching your own name"))
    cat_rows = ""
    for c in g.get("category", []):
        pos = c.get("position")
        badge = (f'<span class="pill ok">nº{pos}</span>' if pos and pos <= 10 else f'<span class="pill hi">{L("Fuera top-10", "Outside top-10")}</span>')
        top = ", ".join(c.get("top", [])[:3]) or "-"
        cat_rows += f'<tr><td>{c.get("query","")}</td><td class="c">{badge}</td><td class="u">{top}</td></tr>'
    if not cat_rows:
        return ""
    ai = r.get("geo_ai") or {}
    pais = ai.get("country") or ""
    pais_txt = f'{L(" en ", " in ")}{pais}' if pais else ""
    return f"""
    <div class="keep">
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Como te ve Google', 'How Google sees you')}</div>
    <h2 class="sec">{L('En qué posición apareces, búsqueda a búsqueda', 'Where you rank, search by search')}</h2>
    <p class="sub">{L('Búsquedas reales de un cliente de tu sector', 'Real searches a customer in your sector would run')}{pais_txt} {L('(el mercado donde opera tu web). En tu propia marca', '(the market your site operates in). On your own brand')} {brand_verdict}.</p>
    <table class="t"><thead><tr><th>{L('Lo que busca un cliente (categoría)', 'What a customer searches (category)')}</th><th class="c">{L('¿Apareces?', 'Do you appear?')}</th><th>{L('Quien sale en tu lugar', 'Who shows up instead')}</th></tr></thead>
    <tbody>{cat_rows}</tbody></table>
    </div>"""


def _competitors_block(r: dict) -> str:
    ai = r.get("geo_ai") or {}
    comps = [c for c in (ai.get("competitors") or []) if isinstance(c, dict) and c.get("name")]
    if not comps:
        return ""
    trs = ""
    for c in comps[:6]:
        dom = c.get("domain") or "—"
        trs += (f'<tr><td><b>{c["name"]}</b></td>'
                f'<td class="u">{dom}</td>'
                f'<td>{L("La IA lo recomienda a un cliente que pide tu mismo servicio", "AI recommends it to a customer asking for your same service")}</td></tr>')
    return f"""
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Quien capta hoy tu demanda', 'Who captures your demand today')}</div>
    <h2 class="sec">{L('Tu competencia real, la que la IA sí recomienda', 'Your real competition, the one AI does recommend')}</h2>
    <p class="sub">{L('Negocios de tu mismo sector que la IA nombra cuando alguien pide tu servicio y tu marca no aparece. No tienen mejor producto: tienen mejor rastro digital, y por eso la IA los cita.', 'Businesses in your sector that AI names when someone asks for your service and your brand does not appear. They do not have a better product: they have a better digital footprint, and that is why AI cites them.')}</p>
    <table class="t"><thead><tr><th>{L('Competidor', 'Competitor')}</th><th>{L('Su web', 'Their site')}</th><th>{L('Por que aparece (y tu no)', 'Why it appears (and you do not)')}</th></tr></thead><tbody>{trs}</tbody></table>"""


def _estado_resumen(r: dict) -> str:
    """Resumen ejecutivo especifico de ESTE sitio: punto fuerte, punto debil y las
    carencias concretas detectadas (para que ningun informe se lea igual a otro)."""
    s = r.get("signals", {}); m = r.get("meta", {})
    # punto fuerte / débil a partir de las MISMAS 9 dimensiones de la pantalla
    dl = r.get("dims") or []
    if dl:
        named = {d["name"].lower(): d["score"] for d in dl}
    else:
        cats = r.get("categories", {})
        named = {L("la salud técnica", "technical health"): cats.get("tecnico", {}).get("score", 0),
                 L("el SEO on-page", "on-page SEO"): cats.get("onpage", {}).get("score", 0),
                 L("la preparación para la IA (GEO)", "AI readiness (GEO)"): cats.get("geo", {}).get("score", 0)}
    best = max(named, key=named.get); worst = min(named, key=named.get)
    weak = []
    if not s.get("https"):
        weak.append(L("no tiene HTTPS", "no HTTPS"))
    if (s.get("robots_info") or {}).get("blocks_all"):
        weak.append(L("el robots.txt bloquea todo el sitio", "robots.txt blocks the whole site"))
    if not m.get("schema_types"):
        weak.append(L("le faltan los datos estructurados (schema)", "missing structured data (schema)"))
    if not m.get("description"):
        weak.append(L("no tiene meta descripción", "no meta description"))
    if m.get("h1_count", 0) != 1:
        weak.append(f'{L("el H1 no esta bien definido", "the H1 is not well defined")} ({m.get("h1_count",0)})')
    if _broken(r)[0] > 0:
        weak.append(f'{_broken(r)[0]} {L("enlace(s) roto(s)", "broken link(s)")}')
    mob = (r.get("psi_full") or {}).get("mobile") or {}
    if mob.get("performance") is not None and mob["performance"] < 60:
        weak.append(f'{L("el móvil es lento", "mobile is slow")} ({mob["performance"]}/100)')
    an = s.get("analytics") or {}
    if not an.get("has_any"):
        weak.append(L("no detectamos analitica", "no analytics detected"))
    weak_txt = ("; ".join(weak[:4]) + ".") if weak else L("no encontramos fallos graves; toca pulir y consolidar.", "we found no serious issues; it's about polishing and consolidating.")
    return (f'<div class="block callout {"g" if named[worst] >= 60 else "o"}">'
            f'<b>{L("En concreto para", "Specifically for")} {r.get("domain", L("tu web","your site"))}:</b> {L("tu punto más fuerte es", "your strongest point is")} <b>{best}</b> '
            f'({named[best]}/100) {L("y donde más pierdes es", "and where you lose most is")} <b>{worst}</b> ({named[worst]}/100). '
            f'{L("Lo que hay que corregir:", "What needs fixing:")} {weak_txt}</div>')


def _fortalezas_block(r: dict) -> str:
    """Dos columnas: lo que YA tienes a favor + victorias rápidas. Positivo y util,
    para no dejar hueco en blanco tras el resumen (sin proyecciones ni marca propia)."""
    s = r.get("signals") or {}; m = r.get("meta") or {}; ai = r.get("geo_ai") or {}
    cats = r.get("categories") or {}
    forts, wins = [], []
    if s.get("https"):
        forts.append(L("Conexión segura (HTTPS) bien configurada.", "Secure connection (HTTPS) well configured."))
    if s.get("sitemap"):
        forts.append(L("Tienes mapa del sitio y Google te indexa.", "You have a sitemap and Google indexes you."))
    if s.get("links_broken", 0) == 0:
        forts.append(L("Sin enlaces rotos (404) en la muestra revisada.", "No broken links (404) in the sample checked."))
    if (cats.get("onpage") or {}).get("score", 0) >= 70:
        forts.append(L("El SEO on-page base esta resuelto (títulos, H1, descripciones).", "On-page SEO basics are covered (titles, H1, descriptions)."))
    if m.get("schema_types"):
        forts.append(L("Ya usas datos estructurados (schema) en tu web.", "You already use structured data (schema) on your site."))
    psi = r.get("psi_full") or {}
    if ((psi.get("desktop") or {}).get("performance") or 0) >= 80:
        forts.append(L("La velocidad en escritorio es rápida y estable.", "Desktop speed is fast and stable."))
    # Victorias rápidas (bajo esfuerzo, alto retorno) según lo que falte
    if not s.get("llms_txt"):
        wins.append(L("Publicar el llms.txt (guia para los buscadores con IA).", "Publish llms.txt (a guide for AI search engines)."))
    it = m.get("img_total", 0); ia = m.get("img_alt", 0)
    if it and ia / it < 0.7:
        wins.append(L("Anadir texto ALT a las imagenes que faltan.", "Add ALT text to the images that are missing it."))
    if not (m.get("og_title") and m.get("og_image")):
        wins.append(L("Completar la vista previa al compartir (Open Graph).", "Complete the share preview (Open Graph)."))
    rb = s.get("robots_info") or {}
    if not rb.get("has_sitemap"):
        wins.append(L("Declarar el sitemap dentro del robots.txt.", "Declare the sitemap inside robots.txt."))
    if ai.get("gbp") and (ai.get("gbp_reviews_n") is None or (isinstance(ai.get("gbp_reviews_n"), int) and ai["gbp_reviews_n"] < 15)):
        wins.append(L("Pedir reseñas en tu ficha de Google de forma sistemática.", "Ask for reviews on your Google listing systematically."))
    if not m.get("has_faq"):
        wins.append(L("Añadir una sección de preguntas frecuentes (FAQ).", "Add a Frequently Asked Questions (FAQ) section."))
    if not forts and not wins:
        return ""
    forts = forts[:4] or [L("Base digital operativa.", "Operational digital base.")]
    wins = wins[:4] or [L("Mantener y monitorizar.", "Maintain and monitor.")]
    fl = "".join(f'<li><span class="i" style="background:{GREEN}">&#10003;</span>{t}</li>' for t in forts)
    wl = "".join(f'<li><span class="i" style="background:{CY6}">+</span>{t}</li>' for t in wins)
    return f"""<div class="block two" style="margin-top:11px">
      <div class="mini"><h4><span class="d" style="background:{GREEN}"></span>{L('Lo que ya tienes a favor', 'What you already have going for you')}</h4><ul class="chk">{fl}</ul></div>
      <div class="mini"><h4><span class="d" style="background:{CY6}"></span>{L('Victorias rápidas (poco esfuerzo, mucho retorno)', 'Quick wins (low effort, high return)')}</h4><ul class="chk">{wl}</ul></div>
    </div>"""


def _gauge(score: int, label: str) -> str:
    dash = round(score / 100 * 258, 1)
    col = _color(score)
    return f"""<svg viewBox="0 0 200 128" width="160">
      <path d="M18,112 A82,82 0 0 1 182,112" fill="none" stroke="#eef1f4" stroke-width="18" stroke-linecap="round"/>
      <path d="M18,112 A82,82 0 0 1 182,112" fill="none" stroke="{col}" stroke-width="18" stroke-linecap="round" stroke-dasharray="{dash} 258"/>
      <text x="100" y="98" text-anchor="middle" font-family="Sora" font-weight="800" font-size="46" fill="#0e1319">{score}</text>
      <text x="100" y="118" text-anchor="middle" font-family="JetBrains Mono" font-size="11" fill="#7b8694">/ 100</text>
    </svg><div class="big">{label}</div>"""


def _growth_chart(score: int) -> str:
    """Gráfica de avance: de dónde partes (hoy) a dónde puedes llegar con el plan."""
    tgt = _target(score)
    y0 = round(82 - (score / 100) * 66, 1)
    y1 = round(82 - (tgt / 100) * 66, 1)
    ymid = round((y0 + y1) / 2, 1)
    line = f"M8,{y0} C90,{y0-1} 160,{ymid} 312,{y1}"
    area = f"{line} L312,88 L8,88 Z"
    return f"""<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;border:1px solid #dee3e9;border-radius:14px"><tr><td style="padding:15px 18px 11px">
      <div class="sectic" style="margin-bottom:9px">{L('Tu potencial: de dónde partes y a dónde puedes llegar', 'Your potential: where you start and where you can reach')}</div>
      <svg viewBox="0 0 320 92" width="100%" style="display:block">
        <defs><linearGradient id="grw" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{CY}" stop-opacity=".22"/><stop offset="1" stop-color="{CY}" stop-opacity="0"/></linearGradient></defs>
        <line x1="8" y1="88" x2="312" y2="88" stroke="#eef1f4" stroke-width="1"/>
        <path d="{area}" fill="url(#grw)"/>
        <path d="{line}" fill="none" stroke="{CY}" stroke-width="2.6" stroke-linecap="round"/>
        <circle cx="8" cy="{y0}" r="3.6" fill="#9aa4b0"/>
        <circle cx="312" cy="{y1}" r="4.2" fill="{CY}"/>
      </svg>
      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td class="mono" style="font-size:9px;color:#7b8694">{L('Hoy', 'Today')} &middot; <b style="color:{INK9}">{score}</b>/100</td>
        <td align="right" class="mono" style="font-size:9px;color:{CY6}">{L('Con Cupperlab', 'With Cupperlab')} &middot; <b>~{tgt}</b>/100</td>
      </tr></table>
    </td></tr></table>"""


def _findings_rows(r: dict, keys, title=None, exclude=None) -> str:
    """Hallazgos del analizador (por clave de área) renderizados con el ESTILO PROPIO
    del PDF (la lista de comprobaciones _check_list: punto de color + nombre + etiqueta +
    observación), NO con las tarjetas del analizador. `exclude` salta títulos que ya
    muestra la sección nativa (para no duplicar). Fuente única: findings.compute."""
    if isinstance(keys, str):
        keys = [keys]
    exc = [e.lower() for e in (exclude or [])]
    rows = []
    for g in (r.get("findings") or []):
        if g.get("key") not in keys:
            continue
        for it in (g.get("items") or []):
            t = it.get("t", "")
            if any(e in t.lower() for e in exc):
                continue
            crit = it.get("sev") == "critico"
            code = L("CRÍTICO", "CRITICAL") if crit else L("REVISAR", "REVIEW")
            rows.append((t, code, "crit" if crit else "med", it.get("d", "")))
    if not rows:
        return ""
    head = (f'<div class="sectic" style="margin-top:12px">{_esc(title)}</div>' if title else "")
    return head + _check_list(rows)


def build_report_html(r: dict, contact: dict, name: str = "") -> str:
    score = r.get("score", 0); grade = r.get("grade", "")
    cats = r.get("categories", {}); m = r.get("meta", {}); s = r.get("signals", {})
    ai = r.get("geo_ai") or {}
    plan = build_plan(r)
    today = datetime.now().strftime("%d/%m/%Y")
    dom = r.get("domain", "")
    client = name if (name and "@" not in name and name.lower() != dom) else dom.split(".")[0].capitalize()

    # Titular a dos tonos según nota — VARIADO por web (no siempre el mismo)
    _h1_pool = {
        "hi": [L('Buena base, <span class="o">con margen para ganar en Google y en la&#160;IA</span>', 'Solid base, <span class="o">with room to win on Google and AI</span>'),
               L('Vas muy bien, <span class="o">el reto ahora es mantener la ventaja</span>', 'You are in good shape, <span class="o">the challenge now is holding your edge</span>'),
               L('Base sólida, <span class="o">lista para adelantar a tu competencia</span>', 'Strong base, <span class="o">ready to pull ahead of your competition</span>')],
        "mid": [L('Tu web cumple, <span class="o">pero hay frentes importantes que te frenan</span>', 'Your site does the job, <span class="o">but real issues are holding you back</span>'),
                L('Base correcta, <span class="o">con varios puntos que atender cuanto antes</span>', 'A decent base, <span class="o">with several points to address soon</span>'),
                L('Vas por detrás de tu potencial, <span class="o">con margen claro de mejora</span>', 'You are below your potential, <span class="o">with clear room to improve</span>')],
        "low": [L('Tu web necesita atención: <span class="o">Google y la&#160;IA te dejan fuera</span>', 'Your site needs attention: <span class="o">Google and AI leave you out</span>'),
                L('Hay bastante que corregir <span class="o">para que te encuentren y te recomienden</span>', 'There is quite a bit to fix <span class="o">so they find and recommend you</span>'),
                L('Pierdes clientes cada día <span class="o">en Google y en la&#160;IA</span>', 'You lose customers every day <span class="o">on Google and AI</span>')],
        "bad": [L('Ahora mismo <span class="o">Google y la&#160;IA apenas te ven</span>', 'Right now <span class="o">Google and AI barely see you</span>'),
                L('Tu web es casi invisible <span class="o">para Google y para la&#160;IA</span>', 'Your site is almost invisible <span class="o">to Google and to AI</span>'),
                L('Partes de cero en visibilidad: <span class="o">es el momento de construirla</span>', 'You start from zero on visibility: <span class="o">now is the time to build it</span>')],
    }
    _band = "hi" if score >= 80 else "mid" if score >= 65 else "low" if score >= 45 else "bad"
    _pool = _h1_pool[_band]
    h1 = _pool[sum(ord(c) for c in (dom or "x")) % len(_pool)]

    # Facts de portada
    ai_fact = ""
    if ai.get("available") and not ai.get("error"):
        ai_fact = (f'<div class="f"><div class="n {"c" if ai.get("knows_brand") else "r"}">{L("Si","Yes") if ai.get("knows_brand") else "No"}</div>'
                   f'<div class="l">{L("la IA", "AI")} {L("reconoce","recognizes") if ai.get("knows_brand") else L("no reconoce","does not recognize")} {L("tu marca al preguntarle directamente", "your brand when asked directly")}</div></div>')
    else:
        gs = cats.get("geo", {}).get("score", 0)
        ai_fact = f'<div class="f"><div class="n {"c" if gs>=55 else "o"}">{gs}</div><div class="l">{L("preparación para la IA (GEO) sobre 100", "AI readiness (GEO) out of 100")}</div></div>'

    facts = f"""
      <div class="f"><div class="n {"c" if score>=70 else "o" if score>=40 else "r"}">{score}</div><div class="l">{L("salud digital global, verificada en vivo", "overall digital health, verified live")}</div></div>
      {ai_fact}
      <div class="f"><div class="n {"r" if _broken(r)[0]>0 else ""}">{_broken(r)[0]}</div><div class="l">{L("enlaces rotos (404) en el rastreo del sitio", "broken links (404) in the site crawl")}</div></div>
      <div class="f"><div class="n {"o" if s["home_time"]>=3 else "c"}">{s["home_time"]}s</div><div class="l">{L("tiempo de respuesta del servidor", "server response time")}</div></div>"""

    # Banda de veredicto
    if ai.get("available") and not ai.get("error"):
        band_b = L("Marca ", "Brand ") + (L("reconocida","recognized") if ai.get("knows_brand") else L("invisible","invisible")) + L(" para la IA.", " to AI.")
        band_p = (L("Le preguntamos directamente a la IA: ", "We asked AI directly: ") +
                  (L("te reconoce","it recognizes you") if ai.get("knows_brand") else L("no tiene información fiable de ti","it has no reliable information about you")) + L(" y ", " and ") +
                  (L("te recomienda en tu sector.","recommends you in your sector.") if ai.get("recommended") else L("recomienda a otras empresas de tu sector, no a la tuya.","recommends other companies in your sector, not yours.")) +
                  L(" En paralelo revisamos tu web técnica y on-page, página a página.", " In parallel we reviewed your site's technical and on-page health, page by page."))
    else:
        band_b = L("Tu visibilidad no se decide solo en Google: ahora también en la IA.", "Your visibility isn't decided only on Google anymore: now also in AI.")
        band_p = L("Revisamos en vivo tu salud técnica, tu on-page y tu preparación para los buscadores con IA. "
                   "Lo que sigue es el detalle, comprobado sin accesos, y el plan para mejorar donde más pesa.",
                   "We reviewed live your technical health, your on-page and your readiness for AI search engines. "
                   "What follows is the detail, checked without access, and the plan to improve where it matters most.")

    css = f"""
    {_font_faces()}
    *{{box-sizing:border-box;margin:0;padding:0}}
    html{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
    body{{font-family:"Plus Jakarta Sans",sans-serif;color:#283038;font-size:11px;line-height:1.55;background:#fff;-webkit-font-smoothing:antialiased}}
    @page{{size:A4;margin:16mm 15mm 14mm}}
    @page:first{{margin:0}}
    .disp{{font-family:"Sora",sans-serif}}
    .cover{{position:relative;height:297mm;background:#fff;overflow:hidden;padding:15mm 18mm}}
    .cover .accent{{position:absolute;top:0;left:0;right:0;height:7mm;background:linear-gradient(90deg,{CY},{CY6} 45%,{OR})}}
    .cover .brand{{display:flex;justify-content:space-between;align-items:center;margin-top:6mm}}
    .cover .brand img{{height:23px;width:auto}}
    .cover .brand .cl{{font-family:"JetBrains Mono",monospace;font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:#7b8694;text-align:right;line-height:1.5}}
    .cover .brand .cl b{{display:block;font-family:"Sora",sans-serif;font-size:17px;font-weight:800;letter-spacing:.02em;color:{NAVY}}}
    .cover .ey{{font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:.26em;text-transform:uppercase;color:{CY6};margin-top:16mm}}
    .cover h1{{font-family:"Sora",sans-serif;font-weight:800;font-size:36px;line-height:1.08;letter-spacing:-.02em;color:{INK9};margin-top:13px}}
    .cover h1 .o{{color:{OR}}}
    .cover h1 .dom{{display:block;font-size:26px;color:{CY6};margin-top:8px}}
    .cover .lede{{font-size:13px;line-height:1.6;color:#5a6675;max-width:170mm;margin-top:16px}}
    .cover .lede b{{color:{INK9}}}
    .cover .facts{{display:grid;grid-template-columns:repeat(4,1fr);gap:11px;margin-top:22px}}
    .cover .facts .f{{border:1px solid #dee3e9;border-radius:13px;padding:13px;background:linear-gradient(180deg,#fff,#f6f9fb)}}
    .cover .facts .f .n{{font-family:"Sora",sans-serif;font-weight:800;font-size:22px;line-height:1;color:{INK9}}}
    .cover .facts .f .n.r{{color:{RED}}}.cover .facts .f .n.o{{color:{OR7}}}.cover .facts .f .n.c{{color:{CY6}}}
    .cover .facts .f .l{{font-size:8.5px;color:#7b8694;margin-top:7px;line-height:1.35}}
    .cover .band{{margin-top:20px;background:{INK9};color:#fff;border-radius:15px;padding:17px 20px;position:relative;overflow:hidden}}
    .cover .band:after{{content:"";position:absolute;right:-30mm;top:-28mm;width:80mm;height:80mm;border-radius:50%;background:radial-gradient(circle,rgba(28,188,228,.20),transparent 70%)}}
    .cover .band b{{font-family:"Sora",sans-serif;font-weight:800;font-size:15.5px;color:#fff;position:relative;display:block;letter-spacing:-.01em}}
    .cover .band p{{font-size:10.5px;color:#c3cdd8;margin-top:6px;position:relative;line-height:1.5}}
    .cover .msec{{font-family:"JetBrains Mono",monospace;font-size:8.5px;letter-spacing:.16em;text-transform:uppercase;color:#7b8694;margin:20px 0 9px}}
    .method4{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
    .method4 .m{{border:1px solid #dee3e9;border-radius:12px;padding:12px}}
    .method4 .m .no{{font-family:"JetBrains Mono",monospace;font-weight:700;font-size:9px;color:{CY6}}}
    .method4 .m b{{display:block;font-family:"Sora",sans-serif;font-size:10px;color:{INK9};margin:4px 0 3px}}
    .method4 .m p{{font-size:8px;color:#5a6675;margin:0;line-height:1.35}}
    .cover .foot{{position:absolute;left:18mm;right:18mm;bottom:13mm;display:flex;justify-content:space-between;border-top:1px solid #dee3e9;padding-top:11px;font-family:"JetBrains Mono",monospace;font-size:9px;letter-spacing:.11em;text-transform:uppercase;color:#7b8694}}
    .cover .foot b{{color:{INK9}}}
    .pg{{padding:0}}
    .newpage{{break-before:page}}
    .eyebrow{{font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:{CY6};margin:0 0 8px;display:flex;align-items:center;gap:8px;break-after:avoid}}
    .eyebrow .bar{{width:26px;height:2px;background:{OR};display:inline-block}}
    h2.sec{{font-family:"Sora",sans-serif;font-weight:800;font-size:27px;letter-spacing:-.02em;color:{INK9};line-height:1.1;margin:0 0 5px;break-after:avoid}}
    .sub{{font-size:11.5px;color:#7b8694;margin-bottom:15px;break-after:avoid}}
    .eyebrow+h2.sec,h2.sec+.sub{{break-before:avoid}}
    .keep{{break-inside:avoid}}
    p{{margin:0 0 9px}}
    .block{{margin-bottom:11px;break-inside:avoid}}
    .two{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
    .sectic{{font-family:"JetBrains Mono",monospace;font-size:8px;letter-spacing:.05em;color:#7b8694;text-transform:uppercase;margin-bottom:8px}}
    .scorewrap{{display:grid;grid-template-columns:176px 1fr;gap:20px;align-items:center;border:1px solid #dee3e9;border-radius:16px;padding:16px 18px;background:#f6f9fb}}
    .gauge{{text-align:center}}.gauge .big{{font-family:"Sora",sans-serif;font-weight:800;font-size:12.5px;color:{INK9};margin-top:2px}}
    .levels{{display:flex;flex-direction:column;gap:7px}}
    .lv .top{{display:flex;justify-content:space-between;margin-bottom:3px}}
    .lv .nm{{font-size:9.5px;font-weight:700;color:#171d26}}.lv .vl{{font-family:"JetBrains Mono",monospace;font-size:9px;font-weight:700}}
    .lv .tr{{height:8px;background:#eef1f4;border-radius:5px;overflow:hidden}}.lv .fl{{height:100%;border-radius:5px}}
    .stats3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
    .stats3 .s{{border:1px solid #dee3e9;border-radius:13px;padding:13px 15px}}
    .stats3 .s .n{{font-family:"Sora",sans-serif;font-weight:800;font-size:20px;line-height:1;color:{INK9}}}
    .stats3 .s .n.r{{color:{RED}}}.stats3 .s .n.o{{color:{OR7}}}.stats3 .s .n.c{{color:{CY6}}}.stats3 .s .n.g{{color:{GREEN}}}
    .stats3 .s .l{{font-size:9px;color:#5a6675;margin-top:6px;line-height:1.4}}
    .critband{{background:{INK9};color:#fff;border-radius:13px;padding:15px 17px;display:flex;gap:14px;align-items:flex-start;break-inside:avoid}}
    .critband .badge{{font-family:"JetBrains Mono",monospace;font-size:7px;font-weight:700;background:{OR7};color:#fff;padding:4px 8px;border-radius:8px;white-space:nowrap;margin-top:2px}}
    .critband .tx{{font-size:10.5px;color:#dfe6ee;line-height:1.55}}.critband .tx b{{color:#fff}}.critband .tx .c{{color:{CY}}}
    table.t{{width:100%;border-collapse:collapse;font-size:9.5px;margin:2px 0}}
    table.t th{{background:{INK9};color:#fff;font-family:"JetBrains Mono",monospace;font-size:7px;letter-spacing:.04em;text-transform:uppercase;padding:8px 9px;text-align:left}}
    table.t td{{border-bottom:1px solid #eef1f4;padding:7px 9px;vertical-align:top}}
    table.t td.c,table.t th.c{{text-align:center}}
    table.t tbody tr:nth-child(odd) td{{background:#f6f9fb}}table.t td b{{color:{INK9}}}
    table.t tr{{break-inside:avoid}}
    .pill{{font-family:"JetBrains Mono",monospace;font-size:7px;font-weight:700;padding:2px 7px;border-radius:9px;white-space:nowrap;display:inline-block}}
    .pill.crit{{background:{RED}22;color:{RED}}}.pill.hi{{background:#fde6d8;color:{OR7}}}.pill.med{{background:#fbf1dc;color:#a9790a}}.pill.ok{{background:#e3f5ec;color:#177a52}}.pill.low{{background:#eef1f4;color:#5a6675}}
    .pill.tia{{background:rgba(28,188,228,.14);color:{CY6}}}.pill.tseo{{background:#e3f5ec;color:#177a52}}
    .yes{{color:#177a52;font-weight:700}}.no{{color:{RED};font-weight:700}}
    table.t td.u{{font-family:"JetBrains Mono",monospace;font-size:8.5px;color:#283038;word-break:break-all}}
    .mini h4{{display:flex;align-items:center}}
    .callout{{background:#f6f9fb;border-left:3px solid {CY};border-radius:0 9px 9px 0;padding:12px 16px;margin:10px 0;font-size:10.5px;color:#283038;line-height:1.55}}
    .callout.o{{border-left-color:{OR}}}.callout.g{{border-left-color:{GREEN}}}.callout.r{{border-left-color:{RED}}}
    .callout b{{color:{INK9}}}
    .mini{{border:1px solid #dee3e9;border-radius:12px;padding:13px 15px}}
    .mini h4{{font-family:"Sora",sans-serif;font-weight:700;font-size:11.5px;color:{INK9};margin-bottom:9px;display:flex;align-items:center;gap:7px}}
    .mini h4 .d{{width:9px;height:9px;border-radius:50%;flex:none}}
    .chk{{list-style:none}}.chk li{{font-size:9.5px;padding:6px 0 6px 22px;position:relative;line-height:1.45}}
    .chk li+li{{border-top:1px solid #eef1f4}}
    .chk li .i{{position:absolute;left:0;top:6px;width:15px;height:15px;border-radius:5px;font-size:9px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700}}
    .chk li b{{color:{INK9}}}
    .actcol{{list-style:none}}.actcol li{{font-size:9.5px;padding:6px 0 6px 25px;position:relative;line-height:1.4;border-top:1px solid #eef1f4}}
    .actcol li:first-child{{border-top:0}}
    .actcol li .k{{position:absolute;left:0;top:6px;width:16px;height:16px;border-radius:5px;background:{CY6};color:#fff;font-family:"JetBrains Mono",monospace;font-size:8px;font-weight:700;display:flex;align-items:center;justify-content:center}}
    .actcol li b{{color:{INK9}}}
    .card{{border:1px solid #dee3e9;border-radius:14px;padding:14px 16px}}
    .cktbl{{width:100%;border-collapse:collapse;margin-top:6px}}
    .cktbl td{{border-top:1px solid #eef1f4;padding:9px 6px;vertical-align:top}}
    .cktbl tr:first-child td{{border-top:0}}
    .cktbl .ckd{{width:22px}}
    .cktbl .ckdot{{display:inline-block;width:15px;height:15px;border-radius:50%;color:#fff;font-size:8.5px;font-weight:700;text-align:center;line-height:15px}}
    .cktbl .ckn{{width:34%;font-size:9.5px;color:{INK9};line-height:1.35}}
    .cktbl .ckn b{{font-family:"Sora",sans-serif;font-weight:700}}
    .cktbl .ckcode{{font-family:"JetBrains Mono",monospace;font-size:7.5px;font-weight:700;margin-left:5px;white-space:nowrap}}
    .cktbl .cko{{font-size:8.5px;color:#5a6675;line-height:1.45}}
    .hbars{{display:flex;flex-direction:column;gap:8px}}
    .hbar{{display:flex;align-items:center;gap:10px}}
    .hbar .l{{width:52%;font-size:9.5px;color:#283038}}.hbar .l b{{color:{INK9}}}
    .hbar .tk{{flex:1;height:14px;background:#eef1f4;border-radius:4px;overflow:hidden}}
    .hbar .fl{{height:100%;border-radius:4px}}
    .hbar .st{{font-family:"JetBrains Mono",monospace;font-size:8px;font-weight:700;white-space:nowrap;width:52px;text-align:right}}
    .cwvcard{{border:1px solid #dee3e9;border-radius:14px;padding:13px 14px 15px;background:#fff}}
    .cwvhd{{display:flex;align-items:center;gap:9px;margin-bottom:11px}}
    .cwvdev{{font-family:"JetBrains Mono",monospace;font-size:8px;font-weight:700;letter-spacing:.12em;color:#fff;padding:5px 9px;border-radius:7px}}
    .cwvsc{{margin-left:auto;font-family:"Sora",sans-serif;font-weight:800;font-size:23px;line-height:1}}
    .cwvsc i{{font-style:normal;font-size:9px;color:#7b8694;font-weight:600}}
    .cwvt{{width:100%;border-collapse:separate;border-spacing:7px 0;table-layout:fixed}}
    .cwvc{{background:#f6f9fb;border:1px solid #eef1f4;border-radius:11px;padding:9px 8px 10px;text-align:center;vertical-align:top}}
    .cwvk{{font-family:"JetBrains Mono",monospace;font-size:8px;font-weight:700;letter-spacing:.08em;color:#7b8694}}
    .cwvv{{font-family:"Sora",sans-serif;font-weight:800;font-size:19px;line-height:1.1;margin:4px 0 6px}}
    .cwvs{{display:inline-block;font-family:"JetBrains Mono",monospace;font-size:7px;font-weight:700;letter-spacing:.06em;color:#fff;padding:2px 8px;border-radius:8px}}
    .cwvn{{font-size:7.6px;color:#5a6675;line-height:1.3;margin-top:7px}}.cwvn span{{color:#9aa4b0}}
    .kwrow{{display:flex;flex-wrap:wrap;gap:7px;margin-top:4px}}
    .kw{{font-family:"JetBrains Mono",monospace;font-size:8.5px;font-weight:500;color:{CY6};background:rgba(28,188,228,.10);border:1px solid rgba(28,188,228,.28);border-radius:20px;padding:4px 11px}}
    .objbox{{border:1px solid #dee3e9;border-radius:14px;padding:15px 18px;background:#f6f9fb;text-align:center;margin-top:10px}}
    .objbox .r{{font-family:"Sora",sans-serif;font-weight:800;font-size:30px;color:{INK9};display:flex;align-items:center;justify-content:center;gap:14px}}
    .objbox .r .a{{color:{AMBER}}}.objbox .r .b{{color:{GREEN}}}.objbox .r .ar{{color:{CY};font-size:20px}}
    .objbox .l{{font-family:"JetBrains Mono",monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;color:#7b8694;margin-top:8px}}
    .segbar{{display:flex;height:30px;border-radius:9px;overflow:hidden;border:1px solid #dee3e9;margin-top:4px}}
    .segbar .sg{{display:flex;align-items:center;justify-content:center;font-family:"JetBrains Mono",monospace;font-size:8.5px;font-weight:700;color:#fff;white-space:nowrap}}
    .seglg{{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:9px;font-size:8.5px;color:#5a6675}}
    .seglg .i{{display:flex;align-items:center;gap:6px}}.seglg .sw{{width:10px;height:10px;border-radius:3px;flex:none}}
    .chk{{list-style:none}}.chk li{{font-size:9px;padding:6px 0 6px 22px;position:relative;line-height:1.45}}
    .chk li+li{{border-top:1px solid #eef1f4}}
    .chk li .i{{position:absolute;left:0;top:6px;width:15px;height:15px;border-radius:5px;font-size:9px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700}}
    .chk li b{{color:{INK9}}}
    .mini h3{{font-family:"Sora",sans-serif;font-weight:700;font-size:11.5px;color:{INK9};margin-bottom:7px}}
    .mini .actcol li{{padding-left:24px}}
    .aiq{{border:1px solid #dee3e9;border-radius:12px;overflow:hidden;margin-bottom:9px;break-inside:avoid}}
    .aiq .q{{display:flex;gap:11px;padding:12px 14px}}
    .aiq .ico{{width:24px;height:24px;border-radius:7px;background:{NAVY};color:#fff;font-family:"JetBrains Mono",monospace;font-size:8px;font-weight:700;display:flex;align-items:center;justify-content:center;flex:none}}
    .aiq .ask{{font-family:"Sora",sans-serif;font-weight:700;font-size:10.5px;color:{INK9};margin-bottom:4px}}
    .aiq .qt{{font-size:9.5px;color:#3d4855;font-style:italic;line-height:1.5}}
    .aiq .src{{background:#f6f9fb;border-top:1px solid #eef1f4;padding:7px 14px;font-family:"JetBrains Mono",monospace;font-size:7.5px;color:#7b8694;display:flex;justify-content:space-between;gap:10px}}
    .aiq .src .v.no{{color:{RED};font-weight:700}}.aiq .src .v.yes{{color:#177a52;font-weight:700}}
    .closeband{{background:linear-gradient(120deg,{INK9},#171d26);color:#fff;border-radius:15px;padding:17px 20px;display:flex;justify-content:space-between;align-items:center;gap:20px;break-inside:avoid;margin-top:12px}}
    .closeband .l b{{font-family:"Sora",sans-serif;font-size:14px;color:#fff}}.closeband .l p{{font-size:10px;color:#aeb9c6;margin:5px 0 0;max-width:120mm;line-height:1.5}}
    .closeband .r{{text-align:right;font-family:"JetBrains Mono",monospace;font-size:9px;color:#c3cdd8;line-height:1.8;white-space:nowrap}}.closeband .r b{{color:{CY}}}
    """

    plan_rows = "".join(f'<li><span class="k">{i+1}</span><b>{p["text"]}</b></li>'
                        for i, p in enumerate(plan[:5]))
    stats3 = f"""
      <div class="s"><div class="n {'g' if cats.get('tecnico',{}).get('score',0)>=70 else 'o'}">{cats.get('tecnico',{}).get('score',0)}/100</div><div class="l">{L("salud técnica: HTTPS, velocidad, robots, sitemap, 404", "technical health: HTTPS, speed, robots, sitemap, 404")}</div></div>
      <div class="s"><div class="n {'g' if cats.get('onpage',{}).get('score',0)>=70 else 'o'}">{cats.get('onpage',{}).get('score',0)}/100</div><div class="l">{L("SEO on-page: títulos, descripciones, H1, schema", "on-page SEO: titles, descriptions, H1, schema")}</div></div>
      <div class="s"><div class="n {'c' if cats.get('geo',{}).get('score',0)>=55 else 'o'}">{cats.get('geo',{}).get('score',0)}/100</div><div class="l">{L("preparación para la IA (GEO / LLMO)", "AI readiness (GEO / LLMO)")}</div></div>"""

    # Lede especifico con datos reales
    comps3 = _comp_names(ai, 3)
    ai_bit = ""
    if ai.get("available") and not ai.get("error") and ai.get("answered_names"):
        if ai.get("knows_brand") and ai.get("recommended") is False:
            ai_bit = f' {L("Le preguntamos a la <b>IA</b>: te describe, pero cuando alguien pide tu servicio nombra a", "We asked <b>AI</b>: it describes you, but when someone asks for your service it names")} {comps3}{L(", no a ti.", ", not you.")}'
        elif not ai.get("knows_brand"):
            ai_bit = f' {L("Le preguntamos a la <b>IA</b>: no sabe quien eres y recomienda a", "We asked <b>AI</b>: it does not know who you are and recommends")} {comps3}.'
        else:
            ai_bit = L(" Le preguntamos a la <b>IA</b>: te reconoce y te recomienda en tu sector.", " We asked <b>AI</b>: it recognizes you and recommends you in your sector.")
    psi = r.get("psi_full") or {}
    mob = (psi.get("mobile") or {})
    speed_bit = f' {L("Medimos la velocidad real en móvil y escritorio (móvil", "We measured real speed on mobile and desktop (mobile")} {mob.get("performance")}/100).' if mob.get("performance") is not None else ""
    n404, nchk, _ = _broken(r)
    tec_bit = (f' {L("Rastreamos", "We crawled")} {nchk} {L("direcciones una a una", "addresses one by one")}'
               + (f' {L("y encontramos", "and found")} {n404} {L("enlace(s) roto(s).", "broken link(s).")}' if n404 else L(" sin enlaces rotos.", " with no broken links.")))
    lede = (f'{L("Diagnóstico con <b>datos reales comprobados en vivo</b>: revisamos tu web por dentro, página a página.", "Diagnosis with <b>real data checked live</b>: we reviewed your site from the inside, page by page.")}{tec_bit}{ai_bit}{speed_bit} '
            f'{L("Aquí tienes lo que encontramos y el plan para que Google y la IA te encuentren y te recomienden.", "Here is what we found and the plan for Google and AI to find and recommend you.")}')

    # Análisis dinamico para "Cómo está tu web hoy" — desde las 9 dimensiones reales
    _dl = r.get("dims") or []
    if _dl:
        _areas = [(d["name"].lower(), d["score"]) for d in _dl]
    else:
        _areas = [(L("la base técnica", "the technical base"), cats.get("tecnico", {}).get("score", 0)),
                  (L("el SEO on-page", "on-page SEO"), cats.get("onpage", {}).get("score", 0)),
                  (L("la preparación para la IA (GEO)", "AI readiness (GEO)"), cats.get("geo", {}).get("score", 0))]
    _best = max(_areas, key=lambda a: a[1]); _worst = min(_areas, key=lambda a: a[1])
    _ai_estado = ""
    if ai.get("available") and not ai.get("error"):
        if not ai.get("knows_brand"):
            _ai_estado = L(" Y lo que más pesa: al preguntarle a la IA, no sabe quien eres.", " And what matters most: when asked, AI doesn't know who you are.")
        elif ai.get("recommended") is False:
            _ai_estado = L(" Y aunque la IA te reconoce, cuando piden tu servicio recomienda a otros.", " And although AI recognizes you, when people ask for your service it recommends others.")
    _idx = L(" Además tu web se esta bloqueando a si misma (noindex).", " On top of that, your site is blocking itself (noindex).") if m.get("robots_noindex") else ""
    _sev = L("sólida","solid") if score >= 80 else L("correcta pero con frentes importantes que atender","decent but with real issues to address") if score >= 65 else L("que necesita atención","that needs attention") if score >= 45 else L("muy débil","very weak")
    estado_análisis = (L(f'Tu web saca <b>{score}/100</b> en salud digital: una base <b>{_sev}</b>.', f'Your site scores <b>{score}/100</b> in digital health: a <b>{_sev}</b> base.')
                       + f'{L(" Tu punto más fuerte es", " Your strongest point is")} <b>{_best[0]}</b> ({_best[1]}/100) {L("y donde más visibilidad pierdes es", "and where you lose the most visibility is")} '
                       f'<b>{_worst[0]}</b> ({_worst[1]}/100).{_ai_estado}{_idx} '
                       f'{L("La nota combina pruebas técnicas en vivo, velocidad real, on-page, seguridad y una consulta real a la IA.", "The score combines live technical tests, real speed, on-page, security and a real query to AI.")}')

    return f"""<!doctype html><html lang="{L('es','en')}"><head><meta charset="utf-8"><style>{css}</style></head><body>

<section class="cover">
  <div class="accent"></div>
  <div class="brand"><img src="{_logo_uri()}" alt="Cupperlab"><span class="cl">{L('Diagnóstico SEO &amp; GEO', 'SEO &amp; GEO Diagnosis')}<b>{today}</b></span></div>
  <div class="ey">{L('Cómo te ven Google y la IA hoy', 'How Google and AI see you today')}</div>
  <h1>{h1}<span class="dom">{dom}</span></h1>
  <p class="lede">{lede}</p>
  <div class="facts">{facts}</div>
  <div class="band"><b>{band_b}</b><p>{band_p}</p></div>
  <div class="msec">{L('Que hemos analizado · en vivo', 'What we analyzed · live')}</div>
  <div class="method4">
    <div class="m"><div class="no">01</div><b>{L('Pruebas técnicas', 'Technical tests')}</b><p>{L('HTTPS, robots, sitemap, velocidad y enlaces rotos (404), uno a uno.', 'HTTPS, robots, sitemap, speed and broken links (404), one by one.')}</p></div>
    <div class="m"><div class="no">02</div><b>On-page</b><p>{L('Títulos, descripciones, H1, canonical y vista previa al compartir.', 'Titles, descriptions, H1, canonical and share preview.')}</p></div>
    <div class="m"><div class="no">03</div><b>{L('Preparación IA (GEO)', 'AI readiness (GEO)')}</b><p>{L('Datos estructurados, llms.txt, marca como entidad y estructura.', 'Structured data, llms.txt, brand as entity and structure.')}</p></div>
    <div class="m"><div class="no">04</div><b>{L('Consulta a la IA', 'AI query')}</b><p>{L('Le preguntamos a la IA si te conoce y si te recomienda.', 'We ask AI whether it knows you and whether it recommends you.')}</p></div>
  </div>
  <div class="foot"><span>{L('Preparado por', 'Prepared by')} <b>Cupperlab</b> · {today} · {L('Verificado en vivo', 'Verified live')}</span><span>{L('Confidencial', 'Confidential')}</span></div>
</section>

<section class="pg">
  <div class="keep">
  <div class="eyebrow"><span class="bar"></span>01 · {L('Estado general', 'Overall status')}</div>
  <h2 class="sec">{L('Cómo está tu web hoy', 'How your site stands today')}</h2>
  <p class="sub">{estado_análisis}</p>
  <div class="block scorewrap">
    <div class="gauge">{_gauge(score, L('Salud digital', 'Digital health'))}</div>
    <div class="levels">{_levels(r)}</div>
  </div>
  </div>
  {_estado_resumen(r)}
  {_fortalezas_block(r)}
</section>

<section class="pg">
  <div class="keep">
  <div class="eyebrow"><span class="bar"></span>02 · {L('Salud técnica de tu web', 'Your site technical health')}</div>
  <h2 class="sec">{L('Qué falla (y qué funciona) por dentro', 'What fails (and what works) under the hood')}</h2>
  <p class="sub">{L('Lo tecnico que Google mira para decidir si te muestra: seguridad, respuesta del servidor, robots, mapa del sitio y enlaces rotos. En rojo lo que falla, en verde lo que ya funciona.', 'The technical signals Google looks at to decide whether to show you: security, server response, robots, sitemap and broken links. In red what fails, in green what already works.')}</p>
  </div>
  {_tech_rows(r)}
  {_robots_block(r)}
  {_crawl_structure_block(r)}
  {_index_block(r)}
  {(''.join('<div class="block callout r"><b>' + L("Enlaces rotos.", "Broken links.") + '</b> ' + L("Ejemplos reales encontrados:", "Real examples found:") + ' ' + ', '.join(e["url"] for e in s["broken_examples"][:3]) + '.</div>' for _ in [0]) if s.get("broken_examples") else '')}
  {_findings_rows(r, ["tech", "schema"], L("Otros hallazgos técnicos y de datos estructurados", "Other technical & structured-data findings"), exclude=["enlaces rotos", "mapa del sitio", "robots", "móvil", "indexación", "cobertura", "conexión segura"])}
</section>

<section class="pg">
  <div class="keep">
  <div class="eyebrow"><span class="bar"></span>03 · {L('SEO on-page', 'On-page SEO')}</div>
  <h2 class="sec">{L('Qué le falta a tus páginas para posicionar', 'What your pages are missing to rank')}</h2>
  <p class="sub">{L('Analizamos TODAS las páginas de tu sitio, no solo la portada. Estas son las señales que deciden si Google te muestra y si la IA te cita, con ejemplos reales de páginas a corregir.', 'We analyze ALL pages of your site, not just the homepage. These are the signals that decide whether Google shows you and whether AI cites you, with real examples of pages to fix.')}</p>
  </div>
  {_onpage_multi(r) or _onpage_rows(r)}
  {_porque_como(r)}
</section>

<section class="pg">
  {_ai_section(r)}
</section>

<section class="pg">
  {_speed_section(r)}
  {_security_section(r)}
</section>

{(f'''<section class="pg">
  {_local_section(r)}
  {_content_section(r)}
</section>''') if (_local_section(r) or _content_section(r)) else ''}

{(f'''<section class="pg">
  {_google_section(r)}
</section>''') if _google_section(r) else ''}

<section class="pg">
  <div class="keep">
  <div class="eyebrow"><span class="bar"></span>06 · {L('Plan de acción', 'Action plan')}</div>
  <h2 class="sec">{L('Todo lo que hay que mejorar, del más crítico al medio', 'Everything to improve, from most critical to softest')}</h2>
  <p class="sub">{L('Una sola lista con todo lo que hay que hacer, ordenada por prioridad. Cada acción lleva su etiqueta: SEO (para Google) o IA (para los buscadores con inteligencia artificial).', 'One single list with everything to do, ordered by priority. Each action is tagged: SEO (for Google) or AI (for AI search engines).')}</p>
  </div>
  {_plan_unificado(r)}
  <div class="keep">
  {_que_esperamos(r)}
  <div class="closeband">
    <div class="l"><b>{L('¿Damos el siguiente paso?', 'Shall we take the next step?')}</b><p>{L('Ponemos en marcha este plan contigo: base técnica, on-page, contenido y las señales que hacen que la IA te recomiende. Primera revisión sin costo.', 'We put this plan into motion with you: technical base, on-page, content and the signals that get AI to recommend you. First review at no cost.')}</p></div>
    <div class="r">{L('Tel', 'Tel')} <b>{contact.get('phone','')}</b><br>{contact.get('email','')}<br>{L('Mejoramos tu rentabilidad.', 'We improve your profitability.')}</div>
  </div>
  </div>
</section>

</body></html>"""


def build_pdf(r: dict, contact: dict, name: str = "") -> bytes | None:
    try:
        html = build_report_html(r, contact, name)
        return html_to_pdf(html)
    except Exception as exc:  # noqa: BLE001
        print(f"[pdf:ERROR] {exc}")
        return None
