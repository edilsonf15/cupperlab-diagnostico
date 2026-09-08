"""
Informe premium en PDF: reutiliza el sistema de diseno de la plantilla oficial de
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
INK9 = "#0e1319"; GREEN = "#1f9d6b"; AMBER = "#dfa019"; RED = "#d64343"; NAVY = "#12324a"

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
    # Exigente: verde solo desde 80; 60-79 ambar (alerta), 45-59 naranja, <45 rojo.
    return GREEN if s >= 80 else AMBER if s >= 60 else OR7 if s >= 45 else RED


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
    # --- Seguridad (lo mas urgente si hay algo expuesto) ---
    if sec.get("exposed"):
        add(L("URGENTE (seguridad): bloquear los archivos sensibles accesibles (", "URGENT (security): block the accessible sensitive files (") +
            ", ".join(e["path"] for e in sec["exposed"][:3]) + L("): pueden filtrar codigo o credenciales.", "): they can leak code or credentials."),
            "Alto", "Bajo", 1)
    if sec.get("headers_missing"):
        add(L("Anadir las cabeceras de seguridad que faltan (", "Add the missing security headers (") + ", ".join(sec["headers_missing"][:3]) +
            L(") para proteger a tus visitantes de ataques comunes.", ") to protect your visitors from common attacks."), "Medio", "Bajo", 2)
    if sec.get("leaks"):
        add(L("Ocultar la version del servidor/CMS que hoy es publica, para dificultar ataques dirigidos.",
            "Hide the server/CMS version that is currently public, to make targeted attacks harder."),
            "Bajo", "Bajo", 2)
    # --- Base tecnica (que exista y sea rastreable) ---
    if not s.get("https"):
        add(L("Activar la conexion segura (HTTPS): sin candado Google penaliza y el navegador avisa de web no segura.",
            "Enable the secure connection (HTTPS): without the padlock Google penalizes you and the browser warns visitors the site is not secure."),
            "Alto", "Bajo", 1)
    if not m.get("viewport"):
        add(L("Adaptar la web a movil (viewport): la mayoria de tus clientes te abren desde el celular.", "Make the site mobile-friendly (viewport): most of your clients open it from their phone."), "Alto", "Medio", 1)
    if rb.get("blocks_all"):
        add(L("URGENTE: tu robots.txt bloquea TODO el sitio (Disallow: /). Google no puede rastrearte. Quitar ese bloqueo.",
            "URGENT: your robots.txt blocks the ENTIRE site (Disallow: /). Google cannot crawl you. Remove that block."),
            "Alto", "Bajo", 1)
    elif not rb.get("present"):
        add(L("Publicar un robots.txt que guie el rastreo de Google y declare el mapa del sitio.", "Publish a robots.txt that guides Google's crawling and declares the sitemap."), "Medio", "Bajo", 1)
    if rb.get("present") and rb.get("suggest_block"):
        add(L("Afinar el robots.txt: bloquear ", "Fine-tune the robots.txt: block ") + ", ".join(rb["suggest_block"]) +
            L(" para que Google no gaste rastreo en paginas sin valor y priorice las que venden.", " so Google does not waste its crawl budget on low-value pages and prioritizes the ones that sell."), "Medio", "Bajo", 2)
    if not s.get("sitemap"):
        add(L("Crear el mapa del sitio (sitemap.xml) para que Google y la IA descubran todas tus paginas.", "Create the sitemap (sitemap.xml) so Google and AI can discover all your pages."), "Alto", "Bajo", 1)
    elif not s.get("sitemap_in_robots"):
        add(L("Declarar el mapa del sitio dentro del robots.txt para que Google lo encuentre antes.", "Declare the sitemap inside the robots.txt so Google finds it sooner."), "Bajo", "Bajo", 2)
    if s.get("links_broken", 0) > 0:
        add(f"{L('Reparar los', 'Fix the')} {s['links_broken']} {L('enlace(s) roto(s) (404) y limpiar el mapa del sitio (quitar etiquetas y paginas vacias).', 'broken link(s) (404) and clean up the sitemap (remove tags and empty pages).')}",
            "Medio", "Medio", 2)
    # --- On-page (que Google entienda y muestre) ---
    if not m.get("title") or not (25 <= len(m.get("title", "")) <= 65):
        add(L("Escribir titulos unicos por pagina (55-60 caracteres) con el servicio y la ciudad.", "Write unique titles per page (55-60 characters) including the service and the city."), "Alto", "Bajo", 1)
    if not m.get("description"):
        add(L("Escribir una meta descripcion por pagina: es el resumen que Google muestra y que la IA cita.", "Write a meta description per page: it is the summary Google shows and that AI quotes."), "Alto", "Bajo", 1)
    if m.get("h1_count", 0) != 1:
        add(L("Marcar un titular principal (H1) claro y unico en cada pagina.", "Set one clear, unique main heading (H1) on each page."), "Medio", "Bajo", 1)
    if not m.get("canonical"):
        add(L("Anadir la URL canonica para que Google no vea paginas duplicadas.", "Add the canonical URL so Google does not see duplicate pages."), "Medio", "Bajo", 2)
    if not m.get("lang"):
        add(L("Declarar el idioma de la web (atributo lang) para paises e IA.", "Declare the site language (lang attribute) for countries and AI."), "Bajo", "Bajo", 2)
    if m.get("word_count", 0) < 300:
        add(L("Ampliar el contenido de las paginas clave: texto propio que responda lo que busca el cliente.", "Expand the content of your key pages: original text that answers what the client is looking for."), "Medio", "Medio", 2)
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
        add(L("Crear una seccion de Preguntas frecuentes con FAQ schema: la IA cita respuestas directas de ahi.",
            "Create a Frequently Asked Questions section with FAQ schema: AI quotes direct answers from there."),
            "Alto", "Bajo", 1)
    if not m.get("has_sameas"):
        add(L("Conectar tu marca como entidad (Organization + perfiles/sameAs) para que la IA sepa que eres una empresa real.",
            "Connect your brand as an entity (Organization + profiles/sameAs) so AI knows you are a real company."),
            "Medio", "Bajo", 2)
    if not m.get("has_contact"):
        add(L("Mostrar ficha de contacto clara (nombre, telefono, direccion) y marcarla con schema: la IA confia en negocios verificables.",
            "Show a clear contact block (name, phone, address) and mark it up with schema: AI trusts verifiable businesses."),
            "Medio", "Bajo", 2)
    if ai.get("gbp") is False:
        add(L("Crear y verificar tu ficha de Google Business (hoy no la encontramos): clave para el mapa, las busquedas locales y la IA local.",
            "Create and verify your Google Business listing (we could not find it today): key for the map, local searches and local AI."),
            "Alto", "Bajo", 1)
    elif ai.get("gbp") is not True and m.get("has_contact"):
        add(L("Verificar y optimizar tu ficha de Google Business (categoria, fotos, resenas): clave para mapas y para la IA local.",
            "Verify and optimize your Google Business listing (category, photos, reviews): key for maps and for local AI."),
            "Medio", "Bajo", 1)
    if m.get("word_count", 0) < 500:
        add(L("Ampliar el contenido con paginas por servicio y por pregunta del cliente: la IA necesita texto propio que citar.",
            "Expand the content with pages per service and per client question: AI needs original text to cite."),
            "Alto", "Medio", 2)
    if not s.get("llms_txt"):
        add(L("Publicar una guia para los buscadores con IA (llms.txt).", "Publish a guide for AI search engines (llms.txt)."), "Medio", "Bajo", 2)
    if ai.get("available") and not ai.get("error"):
        if not ai.get("knows_brand"):
            add(L("Hacer que la IA te reconozca: ficha de empresa clara, perfiles consistentes y rastro externo "
                "(directorios, prensa, resenas) que la IA pueda citar.", "Get AI to recognize you: a clear company profile, consistent listings and an external footprint "
                "(directories, press, reviews) that AI can cite."), "Alto", "Medio", 2)
        if ai.get("recommended") is not True:
            add(L("Entrar en las recomendaciones de la IA: una pagina por servicio con el vocabulario del cliente "
                "y senales de autoridad para que te mencione junto a tu competencia.", "Get into AI's recommendations: a page per service using the client's vocabulary "
                "and authority signals so it mentions you alongside your competitors."), "Alto", "Medio", 2)
    # --- Datos y velocidad ---
    if not an.get("has_any"):
        add(L("Instalar analitica (Google Analytics 4 + Tag Manager) para saber que paginas te traen clientes.",
            "Install analytics (Google Analytics 4 + Tag Manager) to know which pages bring you clients."),
            "Medio", "Bajo", 1)
    elif an.get("duplicated"):
        add(L("Corregir la analitica duplicada: dejar una sola medicion para que tus datos sean fiables.", "Fix the duplicated analytics: keep a single measurement so your data is reliable."), "Medio", "Bajo", 1)
    if mob is not None and mob < 60:
        add(f"{L('Acelerar el movil (hoy', 'Speed up mobile (today')} {mob}/100): {L('comprimir imagenes y aligerar la portada para bajar de 2,5 s de carga.', 'compress images and lighten the homepage to load in under 2.5s.')}",
            "Alto", "Medio", 1)
    add(L("Medir cada semana tu posicion en buscadores y si la IA ya te reconoce y te recomienda.", "Track your search rankings every week and whether AI now recognizes and recommends you."), "Medio", "Bajo", 2)
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
                "en el analisis rapido. Toca mantener y monitorizar.", "We did not detect critical problems "
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
      <div class="sectic">{L("Puntos debiles detectados · que corregir y por que", "Weak points detected · what to fix and why")}</div>
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
    return f"""<table class="t"><thead><tr><th>{L("Accion (en lenguaje de negocio)", "Action (in business terms)")}</th>
      <th class="c">{L("Impacto", "Impact")}</th></tr></thead>
      <tbody>{rows}</tbody></table>"""


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
        <div class="i"><span class="sw" style="background:{RED}"></span>{L("Criticos/Altos: lo que mas frena hoy tu captacion y tu visibilidad en la IA", "Critical/High: what most holds back your lead generation and AI visibility today")}</div>
        <div class="i"><span class="sw" style="background:{AMBER}"></span>{L("Medios: mejoras de indexacion y experiencia", "Medium: indexing and experience improvements")}</div>
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
        if not (ai.get("knows_brand") and ai.get("recommended")) else L("consolidar tu presencia en la IA y ganar la categoria", "consolidating your presence in AI and winning the category")
    return f"""<div class="two" style="margin-top:2px">
      {_objetivo_box(score)}
      <div class="card"><h3>{L("Que esperamos ver", "What we expect to see")}</h3>
      <p style="font-size:9.5px;color:#3d4855;line-height:1.55">{L("Primero: la web con textos propios por pagina, un titular claro y el movil mas rapido; tu marca ganando su propia busqueda. Despues:", "First: the site with original text per page, a clear headline and a faster mobile experience; your brand winning its own search. Then:")} {reconoce} {L("y primeras posiciones en busquedas de tu categoria. Pasar de", "and top positions in searches for your category. Moving from")} <b style="color:{INK9}">{score}</b> {L("a", "to")} <b style="color:{GREEN}">~{tgt}</b> {L("de salud SEO/GEO es trabajo de textos, senales y contenido: rapido de mover y medible desde el primer dia.", "in SEO/GEO health is a matter of text, signals and content: fast to move and measurable from day one.")}</p></div>
    </div>"""


def _porque_como(r: dict) -> str:
    """Por que importa cada carencia (el 'como se corrige' va en el Plan de accion)."""
    m = r.get("meta", {}); s = r.get("signals", {})
    an = s.get("analytics") or {}
    por = []
    if not m.get("title") or not (25 <= len(m.get("title", "")) <= 65):
        por.append(("no", L("Con el <b>titulo</b> mal dimensionado, Google recorta o ignora como te presenta en los resultados.", "With a poorly sized <b>title</b>, Google trims or ignores how it presents you in the results.")))
    if not m.get("description"):
        por.append(("no", L("Sin <b>meta descripcion</b>, Google inventa el resumen y la IA no tiene una frase clara con que citarte.", "Without a <b>meta description</b>, Google makes up the summary and AI has no clear sentence to quote you with.")))
    if m.get("h1_count", 0) != 1:
        por.append(("mid", L("El <b>H1</b> le dice a Google de que va la pagina; si falta o hay varios, se diluye el mensaje.", "The <b>H1</b> tells Google what the page is about; if it is missing or there are several, the message gets diluted.")))
    if not m.get("schema_types"):
        por.append(("no", L("Sin <b>datos estructurados</b>, la IA tiene cero etiquetas con que entender tu negocio y a quien sirves.", "Without <b>structured data</b>, AI has zero labels to understand your business and who you serve.")))
    if not m.get("has_sameas"):
        por.append(("mid", L("Tu <b>marca no esta conectada como entidad</b>: la IA no sabe si eres una empresa real y verificable.", "Your <b>brand is not connected as an entity</b>: AI cannot tell whether you are a real, verifiable company.")))
    if (m.get("img_total", 0) and m.get("img_alt", 0) / max(m["img_total"], 1) < 0.7):
        por.append(("mid", L("Imagenes <b>sin texto ALT</b>: menos resultados enriquecidos en Google y menos accesibilidad.", "Images <b>without ALT text</b>: fewer rich results in Google and less accessibility.")))
    if not s.get("llms_txt"):
        por.append(("mid", L("Sin <b>guia para IA (llms.txt)</b>, los buscadores con IA no saben que priorizar de tu sitio.", "Without an <b>AI guide (llms.txt)</b>, AI search engines do not know what to prioritize from your site.")))
    if not an.get("has_any"):
        por.append(("mid", L("Sin <b>analitica</b> no sabes que paginas convierten, asi que no puedes mejorar con datos.", "Without <b>analytics</b> you do not know which pages convert, so you cannot improve with data.")))
    por = por[:5] or [("ok", L("La base on-page esta bien; quedan ajustes finos que refuerzan lo que ya funciona.", "The on-page basics are fine; only fine-tuning remains to reinforce what already works."))]

    def lis(items):
        out = ""
        for kind, txt in items:
            col = {"no": RED, "mid": AMBER, "ok": GREEN}[kind]
            ico = {"no": "&#10005;", "mid": "!", "ok": "&#10003;"}[kind]
            out += f'<li><span class="i" style="background:{col}">{ico}</span>{txt}</li>'
        return out
    return f"""<div class="block card">
      <div class="sectic" style="margin-bottom:8px">{L("Por que importa cada carencia (como se corrige, en el plan de accion)", "Why each gap matters (how to fix it is in the action plan)")}</div>
      <ul class="chk">{lis(por)}</ul></div>"""


_STAT = {"crit": (RED, "&#10005;"), "hi": (OR7, "!"), "med": (AMBER, "!"), "ok": (GREEN, "&#10003;")}
_ORD = {"crit": 0, "hi": 1, "med": 2, "ok": 3}


def _check_list(items: list) -> str:
    """Checklist en tarjetas (2 columnas), fallos primero."""
    items = sorted(items, key=lambda x: _ORD.get(x[2], 2))
    out = ""
    for name, code, st, obs in items:
        col, ico = _STAT.get(st, (AMBER, "!"))
        out += (f'<div class="ck" style="border-left:3px solid {col}"><span class="cki" style="background:{col}">{ico}</span>'
                f'<div class="ckb"><div class="ckt">{name} <span class="ckc" style="color:{col}">{code}</span></div>'
                f'<div class="cko">{obs}</div></div></div>')
    return f'<div class="cklist">{out}</div>'


def _tech_rows(r: dict) -> str:
    s = r["signals"]; m = r["meta"]
    rb = s.get("robots_info") or {}
    if rb.get("blocks_all"):
        robots_st, robots_code, robots_obs = "crit", L("Bloquea todo", "Blocks everything"), L("Disallow: / — Google no puede rastrear el sitio", "Disallow: / — Google cannot crawl the site")
    elif not s["robots"]:
        robots_st, robots_code, robots_obs = "hi", L("Falta", "Missing"), L("Sin robots.txt: no guias el rastreo de Google", "No robots.txt: you are not guiding Google's crawling")
    else:
        extra = f'{rb.get("disallow_count",0)} {L("reglas", "rules")}' + (L(", declara sitemap", ", declares sitemap") if rb.get("has_sitemap") else L(", no declara el sitemap", ", does not declare the sitemap"))
        robots_st = "ok" if rb.get("has_sitemap") else "med"
        robots_code, robots_obs = "OK", extra
    rows = [
        (L("Conexion segura (HTTPS)", "Secure connection (HTTPS)"), "OK" if s["https"] else L("Falla", "Fails"), "ok" if s["https"] else "crit",
         L("Certificado valido", "Valid certificate") if s["https"] else L("Sin candado de seguridad", "No security padlock")),
        (L("Respuesta del servidor", "Server response"), f'{s["home_status"]}', "ok" if s["home_status"] < 300 else "hi",
         f'{L("Responde en", "Responds in")} {s["home_time"]}s'),
        ("robots.txt", robots_code, robots_st, robots_obs),
        (L("Mapa del sitio (sitemap)", "Sitemap"), "OK" if s["sitemap"] else L("Falta", "Missing"), "ok" if s["sitemap"] else "hi",
         f'{s["sitemap_total"]} {L("URLs listadas", "URLs listed")}' if s["sitemap"] else L("No encontrado", "Not found")),
        (L("llms.txt (guia para IA)", "llms.txt (guide for AI)"), "OK" if s["llms_txt"] else "404", "ok" if s["llms_txt"] else "hi",
         L("Presente", "Present") if s["llms_txt"] else L("No existe: sin guia para los buscadores con IA", "Does not exist: no guide for AI search engines")),
        (L("Enlaces rotos (404)", "Broken links (404)"), f'{s["links_broken"]}/{s["links_checked"]}',
         "ok" if s["links_broken"] == 0 else ("crit" if s["broken_ratio"] > 0.2 else "med"),
         L("Sin enlaces rotos en la muestra", "No broken links in the sample") if s["links_broken"] == 0 else L("Paginas que ya no existen", "Pages that no longer exist")),
        (L("Preparada para movil", "Mobile-ready"), "OK" if m["viewport"] else L("Falta", "Missing"), "ok" if m["viewport"] else "med",
         L("Etiqueta viewport presente", "Viewport tag present") if m["viewport"] else L("Sin viewport movil", "No mobile viewport")),
    ]
    return _check_list(rows)


def _robots_block(r: dict) -> str:
    """Recuadro con el analisis del robots.txt: que bloquea y que conviene bloquear."""
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
    if rb.get("good_blocks"):
        parts.append(L("Ya bloqueas bien ", "You already block well ") + ", ".join(rb["good_blocks"]) +
                     L(" (evita que Google gaste rastreo en paginas sin valor).", " (it keeps Google from spending its crawl budget on low-value pages)."))
    elif rb.get("disallow_sample"):
        parts.append(L("Hoy bloquea: <b>", "Currently blocks: <b>") + ", ".join(rb["disallow_sample"][:5]) + "</b>.")
    if rb.get("suggest_block"):
        parts.append(L("Conviene bloquear tambien ", "It is worth also blocking ") + ", ".join(rb["suggest_block"]) + ".")
    if not rb.get("has_sitemap"):
        parts.append(L("No declara el <b>sitemap</b> dentro del robots: anadirlo ayuda a que Google lo descubra antes.", "It does not declare the <b>sitemap</b> inside robots: adding it helps Google discover it sooner."))
    if not parts:
        parts.append(L("Bien configurado: guia el rastreo y declara el sitemap.", "Well configured: it guides crawling and declares the sitemap."))
    return f'<div class="block callout {tone}"><b>{L("Analisis del robots.txt.", "robots.txt analysis.")}</b> ' + " ".join(parts) + "</div>"


def _onpage_rows(r: dict) -> str:
    m = r["meta"]
    tl = len(m["title"]); dl = len(m["description"])
    rows = [
        (L("Titulo de la pagina", "Page title"), "OK" if 25 <= tl <= 65 else (L("Largo", "Long") if tl > 65 else (L("Corto", "Short") if tl else L("Falta", "Missing"))),
         "ok" if 25 <= tl <= 65 else ("med" if tl else "crit"), f'{tl} {L("caracteres", "characters")}'),
        (L("Titular principal (H1)", "Main heading (H1)"), "OK" if m["h1_count"] == 1 else (L("Varios", "Several") if m["h1_count"] > 1 else L("Falta", "Missing")),
         "ok" if m["h1_count"] == 1 else ("med" if m["h1_count"] > 1 else "crit"), f'{m["h1_count"]} {L("en la home", "on the home page")}'),
        (L("Meta descripcion", "Meta description"), "OK" if 70 <= dl <= 165 else (L("Corta", "Short") if dl else L("Falta", "Missing")),
         "ok" if 70 <= dl <= 165 else ("med" if dl else "crit"), f'{dl} {L("caracteres", "characters")}'),
        (L("URL canonica", "Canonical URL"), "OK" if m["canonical"] else L("Falta", "Missing"), "ok" if m["canonical"] else "med",
         L("Presente", "Present") if m["canonical"] else L("Sin canonical", "No canonical")),
        (L("Vista previa (Open Graph)", "Share preview (Open Graph)"), "OK" if (m["og_title"] and m["og_image"]) else L("Incompleta", "Incomplete"),
         "ok" if (m["og_title"] and m["og_image"]) else "hi",
         L("Titulo e imagen", "Title and image") if (m["og_title"] and m["og_image"]) else L("Se comparte sin tarjeta", "Shared without a card")),
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
    labels = [("paginas", L("Paginas reales", "Real pages")), ("entradas", L("Noticias / blog", "News / blog")),
              ("etiquetas", L("Etiquetas / categorias", "Tags / categories")), ("fichas", L("Fichas / descargas", "Listings / downloads")),
              ("otras", L("Otras (feeds, adjuntos)", "Other (feeds, attachments)"))]
    trs = ""
    for k, lab in labels:
        n = comp.get(k, 0)
        if n:
            verdict = "ok" if k in ("paginas", "entradas") else "med"
            trs += f'<tr><td>{lab}</td><td class="c"><b>{n}</b></td><td class="c">{_pill(verdict, L("util", "useful") if verdict=="ok" else L("revisar", "review"))}</td></tr>'
    return f"""
      <div class="sectic" style="margin-top:12px">{L("Composicion del mapa del sitio", "Sitemap composition")} · {comp['total']} URLs</div>
      <table class="t"><thead><tr><th>{L("Tipo de URL", "URL type")}</th><th class="c">{L("Cuantas", "How many")}</th><th class="c">{L("Veredicto", "Verdict")}</th></tr></thead>
      <tbody>{trs}</tbody></table>"""


def _levels(r: dict) -> str:
    cats = r["categories"]; s = r["signals"]
    tec = cats.get("tecnico", {}); onp = cats.get("onpage", {}); geo = cats.get("geo", {})
    ai = r.get("geo_ai") or {}
    lv = [
        (L("Fundamentos tecnicos (HTTPS, respuesta, robots)", "Technical fundamentals (HTTPS, response, robots)"), tec.get("score", 0)),
        (L("On-page (titulos, descripciones, H1)", "On-page (titles, descriptions, H1)"), onp.get("score", 0)),
        (L("Preparacion para la IA (GEO / LLMO)", "AI readiness (GEO / LLMO)"), geo.get("score", 0)),
        (L("Enlaces y rastreo (404, sitemap)", "Links and crawling (404, sitemap)"), round((100 * (1 - s["broken_ratio"]) + (100 if s["sitemap"] else 0)) / 2)),
        (L("Datos estructurados (schema)", "Structured data (schema)"), _chk_pct(geo, "schema")),
        (L("Respuesta del servidor", "Server response"), 100 if s["home_time"] < 1.5 else 60 if s["home_time"] < 3 else 30),
    ]
    if ai.get("available") and not ai.get("error"):
        lv.append((L("Reconocimiento real por la IA", "Real recognition by AI"), ai.get("ai_score", 0)))
    out = ""
    for nm, val in lv:
        col = _lvl_color(val)
        out += (f'<div class="lv"><div class="top"><span class="nm">{nm}</span>'
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
    guiado por las senales reales del sitio."""
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
        tips.append(L("<b>Marca coherente en todas partes</b>: el MISMO nombre, direccion y telefono (NAP) en tu web, "
                    "Google, redes y directorios. Las contradicciones hacen que la IA dude de quien eres.", "<b>Consistent brand everywhere</b>: the SAME name, address and phone (NAP) on your site, "
                    "Google, social media and directories. Inconsistencies make AI doubt who you are."))
    if ai.get("knows_brand") is False:
        tips.append(L("<b>Definir tu marca como entidad</b>: pagina 'Quienes somos' clara, sameAs a tus perfiles "
                    "oficiales y, si aplica, ficha en Wikidata/Wikipedia, para que la IA sepa quien eres sin darle tu web.", "<b>Define your brand as an entity</b>: a clear 'About us' page, sameAs to your official "
                    "profiles and, if applicable, a Wikidata/Wikipedia entry, so AI knows who you are without being given your site."))
    if not any(x in st for x in ("organization", "localbusiness", "professionalservice")) or not m.get("has_sameas"):
        tips.append(L("Marcar tu <b>ficha de empresa (Organization/LocalBusiness + sameAs)</b>: nombre, direccion, telefono, zona y perfiles oficiales.", "Mark up your <b>company profile (Organization/LocalBusiness + sameAs)</b>: name, address, phone, area and official profiles."))
    if "faqpage" not in st and not m.get("has_faq"):
        tips.append(L("Anadir <b>Preguntas frecuentes con datos estructurados (FAQ schema)</b>: la IA cita respuestas directas de ahi.", "Add <b>Frequently Asked Questions with structured data (FAQ schema)</b>: AI quotes direct answers from there."))
    if not st or not (set(st) & {"faqpage", "organization", "localbusiness", "product", "article", "service"}):
        tips.append(L("Poner <b>datos estructurados utiles (schema)</b> de tus servicios/productos para que la IA entienda tu oferta.", "Add <b>useful structured data (schema)</b> for your services/products so AI understands your offering."))
    if m.get("word_count", 0) < 500:
        tips.append(L("Crear <b>una pagina por servicio</b> con contenido propio que responda las preguntas reales del cliente (la IA necesita texto que citar).", "Create <b>a page per service</b> with original content that answers the client's real questions (AI needs text to cite)."))
    if not (m.get("h1_count") == 1 and m.get("h2_count", 0) >= 3):
        tips.append(L("Ordenar la <b>estructura de titulares</b> (un H1 claro y varios H2 por tema) para que la IA extraiga tus respuestas.", "Organize your <b>heading structure</b> (one clear H1 and several H2 by topic) so AI can extract your answers."))
    if not m.get("has_contact"):
        tips.append(L("Mostrar una <b>ficha de contacto clara</b> (nombre, telefono, direccion): la IA prioriza negocios verificables.", "Show a <b>clear contact block</b> (name, phone, address): AI prioritizes verifiable businesses."))
    dlen = len(m.get("description") or "")
    if not (70 <= dlen <= 165):
        tips.append(L("Escribir un <b>resumen citable (meta descripcion)</b> de 70-160 caracteres por pagina: es lo que la IA usa para citarte.", "Write a <b>quotable summary (meta description)</b> of 70-160 characters per page: it is what AI uses to cite you."))
    if not s.get("llms_txt"):
        tips.append(L("Publicar <b>llms.txt</b> como guia para los buscadores con IA.", "Publish <b>llms.txt</b> as a guide for AI search engines."))
    # Reseñas: senal clave para que la IA recomiende. Adaptado a tu ficha real.
    gbp = ai.get("gbp"); gn = ai.get("gbp_reviews_n")
    if gbp is False:
        tips.append(L("Crear tu <b>ficha de Google Business y conseguir reseñas</b>: la IA recomienda a negocios con opiniones reales y buena valoracion.", "Create your <b>Google Business listing and gather reviews</b>: AI recommends businesses with real opinions and a good rating."))
    elif gbp and (gn is None or (isinstance(gn, int) and gn < 15)):
        tips.append(L("<b>Conseguir mas reseñas en tu ficha de Google</b>: tienes ficha pero pocas valoraciones, y la IA prioriza a los negocios mejor valorados. Pide reseñas a tus clientes de forma sistematica.", "<b>Gather more reviews on your Google listing</b>: you have a listing but few ratings, and AI prioritizes the best-rated businesses. Ask your clients for reviews systematically."))
    else:
        tips.append(L("<b>Sumar reseñas y casos de exito verificables</b> (Google, directorios, prensa): la IA cita fuentes con reputacion.", "<b>Add verifiable reviews and success stories</b> (Google, directories, press): AI cites reputable sources."))
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
    if not (ai.get("available") and not ai.get("error") and answered):
        geo = r["categories"].get("geo", {})
        return f"""
        <div class="eyebrow"><span class="bar"></span>04 · {L("Como te ve la inteligencia artificial", "How artificial intelligence sees you")}</div>
        <h2 class="sec">{L("Como te ve la IA", "How AI sees you")}</h2>
        <p class="sub">{L("Medimos las senales que la IA usa para entenderte y citarte, y le preguntamos por ti en vivo.", "We measure the signals AI uses to understand and cite you, and we ask it about you live.")}</p>
        <div class="block scorewrap"><div class="gauge">{_gauge(geo.get('score',0), L('Preparacion IA', 'AI readiness'))}</div>
        <div class="levels">{_levels(r)}</div></div>"""

    brand = ai.get("brand", r["domain"])
    country = ai.get("country") or ""
    zona = ai.get("zona") or country
    knows = ai.get("knows_brand"); reco = ai.get("recommended")
    recg = ai.get("recognition") or ("strong" if knows else ("weak" if ai.get("knows_with_web") else "none"))
    mentions = (ai.get("mentions") or ai.get("web_description") or ai.get("brand_description") or "").strip()
    comps = _comp_names(ai) or L("otras firmas de tu sector", "other firms in your industry")

    # Tarjeta 1: lo que la IA MENCIONA de ti (verde/ambar/rojo segun reconocimiento)
    rec_tag = {"strong": L("TE RECONOCE", "RECOGNIZES YOU"), "weak": L("SOLO CON TU WEB", "ONLY WITH YOUR SITE"), "none": L("NO TE RECONOCE", "DOES NOT RECOGNIZE YOU")}[recg]
    rec_v = {"strong": "yes", "weak": "", "none": "no"}[recg]
    if recg == "strong":
        m_line = f'{L("Esto es lo que la IA sabe de ti:", "This is what AI knows about you:")} "{_esc(mentions[:220])}"'
        m_src = L("La IA te reconoce por su cuenta: vas por delante de la mayoria.", "AI recognizes you on its own: you are ahead of most.")
    elif recg == "weak":
        m_line = (f'{L("Solo cuando le das tu web, la IA te describe asi:", "Only when you give it your site does AI describe you like this:")} "{_esc(mentions[:200])}". '
                  f'{L("Por su cuenta, no te reconoce.", "On its own, it does not recognize you.")}')
        m_src = L("Solo te reconoce si le pasas tu dominio; el objetivo es que te conozca sin darselo.", "It only recognizes you if you give it your domain; the goal is for it to know you without being told.")
    else:
        m_line = L("Ni dandole tu web la IA encuentra informacion fiable de tu marca.", "Even when given your site, AI finds no reliable information about your brand.")
        m_src = L("La IA no te encuentra: hoy no existes para quien pregunta a la IA antes de comprar.", "AI cannot find you: today you do not exist for those who ask AI before buying.")
    card1 = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L("Le preguntamos a la IA por tu marca", "We asked AI about your brand")} "{_esc(brand)}" ({_esc(r.get('domain',''))}):</div>
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
        <div class="qt">{L("Encontramos tu ficha activa", "We found your active listing")}{(' (' + gbp_rev + ')') if gbp_rev else ''}. {L("Buenas reseñas: son una de las fuentes que la IA cita para recomendarte. Mantenlas y sigue pidiendo mas.", "Good reviews: they are one of the sources AI cites to recommend you. Keep them up and keep asking for more.")}</div></div></div>
      <div class="src"><span>{L("Buscada por nombre en", "Searched by name in")} {_esc(zona)} {L("y por tu dominio", "and by your domain")}</span><span class="v yes">{L("TIENES FICHA", "YOU HAVE A LISTING")}</span></div>
    </div>"""
        elif gbp and few_reviews:
            gbp_card = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L("Tu ficha de Google Business / Maps", "Your Google Business / Maps listing")}</div>
        <div class="qt">{L("Tienes ficha", "You have a listing")}{(' (' + gbp_rev + ')') if gbp_rev else ''}, {L("pero <b>te faltan reseñas y valoraciones</b>. Las opiniones buenas son una de las señales que la IA usa para recomendarte: sin ellas, apareces por detras de la competencia mejor valorada. Hay que pedir reseñas a tus clientes de forma sistematica.", "but <b>you are short on reviews and ratings</b>. Good opinions are one of the signals AI uses to recommend you: without them, you appear behind better-rated competitors. You need to ask your clients for reviews systematically.")}</div></div></div>
      <div class="src"><span>{L("Buscada por nombre en", "Searched by name in")} {_esc(zona)} {L("y por tu dominio", "and by your domain")}</span><span class="v">{L("FALTAN RESEÑAS", "REVIEWS MISSING")}</span></div>
    </div>"""
        else:
            gbp_card = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L("Tu ficha de Google Business / Maps", "Your Google Business / Maps listing")}</div>
        <div class="qt">{L("No encontramos una ficha activa. Es clave para el mapa, las busquedas locales y para que la IA te cite con resenas reales.", "We could not find an active listing. It is key for the map, local searches and for AI to cite you with real reviews.")}</div></div></div>
      <div class="src"><span>{L("Buscada por nombre en", "Searched by name in")} {_esc(zona)} {L("y por tu dominio", "and by your domain")}</span><span class="v no">{L("SIN FICHA", "NO LISTING")}</span></div>
    </div>"""
    card2 = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">{L('Le preguntamos a la IA: "¿Que empresas recomiendas para este servicio?"', 'We asked AI: "Which companies do you recommend for this service?"')}</div>
        <div class="qt">{L("Menciono a:", "It mentioned:")} {comps}.</div></div></div>
      <div class="src"><span>{L("Consulta a la IA en vivo · sobre tu categoria (sin nombrarte)", "Live AI query · about your category (without naming you)")}</span>
        <span class="v {'yes' if reco else 'no'}">{L('TE RECOMIENDA', 'RECOMMENDS YOU') if reco else (L('NO TE RECOMIENDA', 'DOES NOT RECOMMEND YOU') if reco is False else L('SIN DETERMINAR', 'UNDETERMINED'))}</span></div>
    </div>"""

    # Las 3 busquedas reales de un cliente (el corazon de la prueba GEO), como tarjetas
    questions = ai.get("questions") or []
    q_cards = ""
    for q in questions[:3]:
        ap = q.get("appears")
        named = ", ".join(q.get("named", [])[:4]) or "—"
        vt = L("APARECES", "YOU APPEAR") if ap is True else (L("NO APARECES", "YOU DON'T APPEAR") if ap is False else L("SIN DATO", "NO DATA"))
        vc = "yes" if ap is True else ("no" if ap is False else "")
        q_cards += f"""
    <div class="aiq">
      <div class="q"><div class="ico">{L('IA','AI')}</div><div>
        <div class="ask">{L('Un cliente busca:', 'A customer searches:')} "{_esc(q.get('q',''))}"</div>
        <div class="qt">{L('La IA recomienda a:', 'AI recommends:')} {_esc(named)}.</div></div></div>
      <div class="src"><span>{L('Busqueda de categoria', 'Category search')}{(' · ' + _esc(country)) if country else ''}</span>
        <span class="v {vc}">{vt}</span></div>
    </div>"""
    q_block = (f'<div class="sectic" style="margin-top:12px">{L("Las 3 busquedas reales de un cliente", "The 3 real customer searches")}'
               f'{(L(" en ", " in ") + _esc(country)) if country else ""} · {L("¿sales tu?", "do you show up?")}</div>{q_cards}') if q_cards else ""

    # Veredicto segun reconocimiento + recomendacion (verde/ambar/rojo)
    if recg == "strong" and reco:
        topnote = L("<b>Buena senal:</b> la IA te reconoce y te incluye cuando piden tu servicio. Toca mantener la ventaja.",
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
        verdict = f'{L("La IA no sabe quien eres y recomienda a", "AI does not know who you are and recommends")} {comps}: {L("hoy no apareces cuando preguntan por tu servicio.", "today you do not show up when people ask for your service.")}'
    else:
        topnote = L("<b>Te reconoce por encima.</b> La IA te describe cuando le pasan tu web, pero cuando alguien "
                    "busca tu servicio sin conocerte, nombra antes a la competencia: ahi es donde pierdes clientes.",
                    "<b>It half-recognizes you.</b> AI describes you when given your site, but when someone "
                    "searches for your service without knowing you, it names competitors first: that's where you lose customers.")
        vcol = "o"
        verdict = f'{L("La IA te reconoce a medias y cuando piden tu servicio nombra a", "AI half-recognizes you and when people ask for your service it names")} {comps}: {L("trabajemos para que te cite a ti primero.", "let us work so it cites you first.")}'

    cards = card1 + gbp_card + card2
    return f"""
    <div class="eyebrow"><span class="bar"></span>04 · {L('Como te ve la inteligencia artificial', 'How artificial intelligence sees you')}</div>
    <h2 class="sec">{L('Como te ve la IA cuando preguntan por ti', 'How AI sees you when people ask about you')}</h2>
    <p class="sub">{L('Le preguntamos a la IA, en vivo: por tu marca, por tu servicio y con busquedas reales de cliente en', 'We asked AI, live: about your brand, your service and with real customer searches in')} {_esc(zona or L('tu zona','your area'))}. {L('Cada vez mas gente busca asi antes de decidir.', 'More and more people search this way before deciding.')}</p>
    <div class="block callout {vcol}">{topnote}</div>
    {cards}{q_block}
    <div class="block callout {vcol}"><b>{L('Veredicto IA.', 'AI verdict.')}</b> {verdict}</div>"""


def _crawl_structure_block(r: dict) -> str:
    """Estructura del sitio medida por NUESTRO rastreo (fiable, no depende de
    buscadores): cuantas paginas tiene y cuantas revisamos una a una."""
    s = r.get("signals") or {}
    pf = s.get("pages_found", 0)
    checked = s.get("links_checked", 0)
    broken = s.get("links_broken", 0)
    if not pf and not checked:
        return ""
    sm = s.get("sitemap_total", 0)
    fuente = (f'{L("segun tu mapa del sitio", "according to your sitemap")} ({sm} URLs)' if sm else L("por los enlaces internos de tu web", "from your site's internal links"))
    rota = (f' {L("De ellas,", "Of those,")} <b>{broken}</b> {L("daban error 404.", "returned a 404 error.")}' if broken else L(" No encontramos enlaces rotos en la muestra.", " We found no broken links in the sample."))
    return (f'<div class="block callout o"><b>{L("Estructura de tu sitio (rastreo pagina por pagina).", "Your site structure (page-by-page crawl).")}</b> '
            f'{L("Tu web tiene del orden de", "Your site has around")} <b>{pf}</b> {L("paginas", "pages")} {fuente}. {L("Revisamos", "We checked")} {checked} {L("una a una.", "one by one.")}{rota} '
            f'{L("El numero exacto que Google tiene indexado se confirma con Search Console (lo activamos al empezar).", "The exact number Google has indexed is confirmed with Search Console (we enable it at the start).")}</div>')


def _index_block(r: dict) -> str:
    ix = r.get("indexation")
    if not ix:
        return ""
    n = ix.get("sample_count", 0)
    tot = ix.get("sitemap_total", 0)
    prov = ix.get("provider", L("el buscador", "the search engine"))
    if not ix.get("indexed"):
        return ('<div class="block callout r"><b>' + L("Indexacion.", "Indexing.") + '</b> ' + L("No encontramos tu sitio indexado en la muestra de", "We didn't find your site indexed in the sample from")
                + ' ' + _esc(prov) + '. ' + L("Hay que revisar que Google pueda rastrearte e indexarte.", "We need to check that Google can crawl and index you.") + '</div>')
    est = ix.get("indexed_estimate")
    concl = ix.get("conclusion") or ""
    extra = f' {L("Tu mapa del sitio lista", "Your sitemap lists")} {tot} URLs.' if tot else ""
    est_txt = f' {L("El buscador indexa del orden de", "The search engine indexes around")} <b>{est}</b> {L("paginas.", "pages.")}' if isinstance(est, int) else ""
    bi = ix.get("broken_indexed") or []
    base = ('<div class="block callout o"><b>' + L("Indexacion (comprobada con navegador propio via site:).", "Indexing (checked with our own browser via site:).") + '</b> '
            + f'{L("Rastreamos", "We crawled")} {_esc(prov)} {L("pagina por pagina.", "page by page.")}{est_txt}{extra}'
            + (f' {_esc(concl)}' if concl else ' ' + L("El numero exacto se confirma con Search Console.", "The exact number is confirmed with Search Console."))
            + '</div>')
    if bi:
        trs = "".join(f'<tr><td class="u">{_esc(b["url"])}</td><td class="c">{b["status"]}</td></tr>' for b in bi[:6])
        base += ('<div class="block callout r"><b>' + L("Paginas indexadas que dan error (404).", "Indexed pages returning an error (404).") + '</b> ' + L("Google las tiene "
                 "indexadas pero ya no existen: hay que redirigirlas o recuperarlas.", "Google has them "
                 "indexed but they no longer exist: they must be redirected or restored.") + '</div>'
                 f'<table class="t"><thead><tr><th>{L("Pagina indexada", "Indexed page")}</th><th class="c">{L("Estado", "Status")}</th></tr></thead><tbody>{trs}</tbody></table>')
    return base


_SEC_EXPLAIN = [
    ("hsts", ("Obliga al navegador a usar siempre HTTPS: evita que intercepten la conexion.",
              "Forces the browser to always use HTTPS: prevents the connection from being intercepted.")),
    ("content-security", ("Controla que scripts y recursos puede cargar tu web: frena inyecciones y robo de datos.",
                          "Controls which scripts and resources your site can load: stops injections and data theft.")),
    ("x-frame", ("Impide que tu web se incruste en otra para enganar al usuario (clickjacking).",
                 "Prevents your site from being embedded in another to trick the user (clickjacking).")),
    ("x-content-type", ("Evita que el navegador interprete archivos como algo que no son (sniffing).",
                        "Stops the browser from interpreting files as something they are not (sniffing).")),
    ("referrer", ("Controla que informacion se envia al salir de tu web (privacidad del usuario).",
                  "Controls what information is sent when leaving your site (user privacy).")),
    ("permissions", ("Limita el acceso a camara, microfono o ubicacion: reduce la superficie de ataque.",
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
    # Cada cabecera EXPLICADA (que hace y por que importa)
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
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Seguridad y tecnologia', 'Security and technology')}</div>
    <h2 class="sec">{L('Que tan segura y protegida esta tu web', 'How safe and protected your site is')}</h2>
    <p class="sub">{L('Revisamos cabeceras de seguridad, fugas de version del servidor y archivos sensibles accesibles.', 'We check security headers, server version leaks and accessible sensitive files.')}
    {L('Una web insegura pierde confianza de clientes y de Google. Nota de seguridad:', 'An insecure site loses the trust of customers and of Google. Security score:')} <b style="color:{_color(score)}">{score}/100</b>.</p>
    {exposed_html}
    <div class="sectic" style="margin-top:8px">{L('Cabeceras de seguridad', 'Security headers')}</div>
    {checks}
    {tech_html}"""


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
        # (bueno, malo) por metrica; en segundos salvo TBT (ms) y SI
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
                       (L("Bloqueo por codigo (TBT)", "Code blocking (TBT)"), "tbt"), (L("Indice de velocidad", "Speed index"), "si")]:
            val = d.get(k) or "-"
            bcol, w = _metric_bar(k, val)
            rows += (f'<div class="hbar" style="margin:5px 0"><div class="l" style="width:44%;font-size:8.5px">{lab}</div>'
                     f'<div class="tk" style="height:9px"><div class="fl" style="width:{w}%;background:{bcol}"></div></div>'
                     f'<div class="st" style="width:44px;color:{bcol}">{val}</div></div>')
        return f"""<div class="mini">
          <h4>{title} <span style="margin-left:auto;font-size:22px;font-weight:800;color:{col}">{perf if perf is not None else '-'}<span style="font-size:9px;color:#7b8694">/100</span></span></h4>
          <div class="hbars" style="margin-top:6px">{rows}</div></div>"""

    m = psi.get("mobile"); d = psi.get("desktop")

    return f"""
    <div style="break-inside:avoid">
      <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Rendimiento · movil frente a escritorio', 'Performance · mobile vs desktop')}</div>
      <h2 class="sec">{L('Velocidad de tu web', 'Your site speed')}</h2>
      <div class="block two" style="margin-top:8px">{card(L('Movil', 'Mobile'), m, True)}{card(L('Escritorio', 'Desktop'), d, False)}</div>
    </div>"""


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
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>{L('Como te ve Google', 'How Google sees you')}</div>
    <h2 class="sec">{L('En que posicion apareces, busqueda a busqueda', 'Where you rank, search by search')}</h2>
    <p class="sub">{L('Busquedas reales de un cliente de tu sector', 'Real searches a customer in your sector would run')}{pais_txt} {L('(el mercado donde opera tu web). En tu propia marca', '(the market your site operates in). On your own brand')} {brand_verdict}.</p>
    <table class="t"><thead><tr><th>{L('Lo que busca un cliente (categoria)', 'What a customer searches (category)')}</th><th class="c">{L('¿Apareces?', 'Do you appear?')}</th><th>{L('Quien sale en tu lugar', 'Who shows up instead')}</th></tr></thead>
    <tbody>{cat_rows}</tbody></table>"""


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
    cats = r.get("categories", {}); s = r.get("signals", {}); m = r.get("meta", {})
    named = {L("la salud tecnica", "technical health"): cats.get("tecnico", {}).get("score", 0),
             L("el SEO on-page", "on-page SEO"): cats.get("onpage", {}).get("score", 0),
             L("la preparacion para la IA (GEO)", "AI readiness (GEO)"): cats.get("geo", {}).get("score", 0)}
    best = max(named, key=named.get); worst = min(named, key=named.get)
    weak = []
    if not s.get("https"):
        weak.append(L("no tiene HTTPS", "no HTTPS"))
    if (s.get("robots_info") or {}).get("blocks_all"):
        weak.append(L("el robots.txt bloquea todo el sitio", "robots.txt blocks the whole site"))
    if not m.get("schema_types"):
        weak.append(L("le faltan los datos estructurados (schema)", "missing structured data (schema)"))
    if not m.get("description"):
        weak.append(L("no tiene meta descripcion", "no meta description"))
    if m.get("h1_count", 0) != 1:
        weak.append(f'{L("el H1 no esta bien definido", "the H1 is not well defined")} ({m.get("h1_count",0)})')
    if s.get("links_broken", 0) > 0:
        weak.append(f'{s["links_broken"]} {L("enlace(s) roto(s)", "broken link(s)")}')
    mob = (r.get("psi_full") or {}).get("mobile") or {}
    if mob.get("performance") is not None and mob["performance"] < 60:
        weak.append(f'{L("el movil es lento", "mobile is slow")} ({mob["performance"]}/100)')
    an = s.get("analytics") or {}
    if not an.get("has_any"):
        weak.append(L("no detectamos analitica", "no analytics detected"))
    weak_txt = ("; ".join(weak[:4]) + ".") if weak else L("no encontramos fallos graves; toca pulir y consolidar.", "we found no serious issues; it's about polishing and consolidating.")
    return (f'<div class="block callout {"g" if named[worst] >= 60 else "o"}">'
            f'<b>{L("En concreto para", "Specifically for")} {r.get("domain", L("tu web","your site"))}:</b> {L("tu punto mas fuerte es", "your strongest point is")} <b>{best}</b> '
            f'({named[best]}/100) {L("y donde mas pierdes es", "and where you lose most is")} <b>{worst}</b> ({named[worst]}/100). '
            f'{L("Lo que hay que corregir:", "What needs fixing:")} {weak_txt}</div>')


def _gauge(score: int, label: str) -> str:
    dash = round(score / 100 * 258, 1)
    col = _color(score)
    return f"""<svg viewBox="0 0 200 128" width="160">
      <path d="M18,112 A82,82 0 0 1 182,112" fill="none" stroke="#eef1f4" stroke-width="18" stroke-linecap="round"/>
      <path d="M18,112 A82,82 0 0 1 182,112" fill="none" stroke="{col}" stroke-width="18" stroke-linecap="round" stroke-dasharray="{dash} 258"/>
      <text x="100" y="98" text-anchor="middle" font-family="Sora" font-weight="800" font-size="46" fill="#0e1319">{score}</text>
      <text x="100" y="118" text-anchor="middle" font-family="JetBrains Mono" font-size="11" fill="#7b8694">/ 100</text>
    </svg><div class="big">{label}</div>"""


def build_report_html(r: dict, contact: dict, name: str = "") -> str:
    score = r.get("score", 0); grade = r.get("grade", "")
    cats = r.get("categories", {}); m = r.get("meta", {}); s = r.get("signals", {})
    ai = r.get("geo_ai") or {}
    plan = build_plan(r)
    today = datetime.now().strftime("%d/%m/%Y")
    dom = r.get("domain", "")
    client = name if (name and "@" not in name and name.lower() != dom) else dom.split(".")[0].capitalize()

    # Titular a dos tonos segun nota — VARIADO por web (no siempre el mismo)
    _h1_pool = {
        "hi": [L('Buena base, <span class="o">con margen para ganar en Google y en la IA</span>', 'Solid base, <span class="o">with room to win on Google and AI</span>'),
               L('Tienes lo tecnico resuelto, <span class="o">pero la IA aun no te nombra</span>', 'Your tech is sorted, <span class="o">but AI still does not name you</span>'),
               L('Vas bien en Google, <span class="o">el reto ahora es que la IA te recomiende</span>', 'You do well on Google, <span class="o">the challenge now is getting AI to recommend you</span>')],
        "mid": [L('Vas por buen camino, <span class="o">pero la IA todavia no te prioriza</span>', 'You are on the right track, <span class="o">but AI does not prioritize you yet</span>'),
                L('Tu web cumple, <span class="o">aunque pierdes visibilidad donde mas se decide</span>', 'Your site does the job, <span class="o">but you lose visibility where it matters most</span>'),
                L('Base aceptable, <span class="o">con puntos claros que te estan frenando</span>', 'Acceptable base, <span class="o">with clear points holding you back</span>')],
        "low": [L('Tu web funciona, <span class="o">pero Google y la IA te dejan fuera</span>', 'Your site works, <span class="o">but Google and AI leave you out</span>'),
                L('Tienes carencias que te cuestan clientes <span class="o">en Google y en la IA</span>', 'You have gaps costing you customers <span class="o">on Google and AI</span>'),
                L('Hay trabajo por hacer <span class="o">para que Google y la IA te muestren</span>', 'There is work to do <span class="o">so Google and AI show you</span>')],
        "bad": [L('Ahora mismo <span class="o">Google y la IA apenas te ven</span>', 'Right now <span class="o">Google and AI barely see you</span>'),
                L('Tu web es casi invisible <span class="o">para Google y para la IA</span>', 'Your site is almost invisible <span class="o">to Google and to AI</span>'),
                L('Partes de cero en visibilidad: <span class="o">Google y la IA no te encuentran</span>', 'You start from zero on visibility: <span class="o">Google and AI cannot find you</span>')],
    }
    _band = "hi" if score >= 70 else "mid" if score >= 55 else "low" if score >= 40 else "bad"
    _pool = _h1_pool[_band]
    h1 = _pool[sum(ord(c) for c in (dom or "x")) % len(_pool)]

    # Facts de portada
    ai_fact = ""
    if ai.get("available") and not ai.get("error"):
        ai_fact = (f'<div class="f"><div class="n {"c" if ai.get("knows_brand") else "r"}">{L("Si","Yes") if ai.get("knows_brand") else "No"}</div>'
                   f'<div class="l">{L("la IA", "AI")} {L("reconoce","recognizes") if ai.get("knows_brand") else L("no reconoce","does not recognize")} {L("tu marca al preguntarle directamente", "your brand when asked directly")}</div></div>')
    else:
        gs = cats.get("geo", {}).get("score", 0)
        ai_fact = f'<div class="f"><div class="n {"c" if gs>=55 else "o"}">{gs}</div><div class="l">{L("preparacion para la IA (GEO) sobre 100", "AI readiness (GEO) out of 100")}</div></div>'

    facts = f"""
      <div class="f"><div class="n {"c" if score>=70 else "o" if score>=40 else "r"}">{score}</div><div class="l">{L("salud digital global, verificada en vivo", "overall digital health, verified live")}</div></div>
      {ai_fact}
      <div class="f"><div class="n {"r" if s["links_broken"]>0 else ""}">{s["links_broken"]}</div><div class="l">{L("enlaces rotos (404) en la muestra revisada", "broken links (404) in the sample checked")}</div></div>
      <div class="f"><div class="n {"o" if s["home_time"]>=3 else "c"}">{s["home_time"]}s</div><div class="l">{L("tiempo de respuesta del servidor", "server response time")}</div></div>"""

    # Banda de veredicto
    if ai.get("available") and not ai.get("error"):
        band_b = L("Marca ", "Brand ") + (L("reconocida","recognized") if ai.get("knows_brand") else L("invisible","invisible")) + L(" para la IA.", " to AI.")
        band_p = (L("Le preguntamos directamente a la IA: ", "We asked AI directly: ") +
                  (L("te reconoce","it recognizes you") if ai.get("knows_brand") else L("no tiene informacion fiable de ti","it has no reliable information about you")) + L(" y ", " and ") +
                  (L("te recomienda en tu sector.","recommends you in your sector.") if ai.get("recommended") else L("recomienda a otras empresas de tu sector, no a la tuya.","recommends other companies in your sector, not yours.")) +
                  L(" En paralelo revisamos tu web tecnica y on-page, pagina a pagina.", " In parallel we reviewed your site's technical and on-page health, page by page."))
    else:
        band_b = L("Tu visibilidad no se decide solo en Google: ahora tambien en la IA.", "Your visibility isn't decided only on Google anymore: now also in AI.")
        band_p = L("Revisamos en vivo tu salud tecnica, tu on-page y tu preparacion para los buscadores con IA. "
                   "Lo que sigue es el detalle, comprobado sin accesos, y el plan para mejorar donde mas pesa.",
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
    .cklist{{display:grid;grid-template-columns:1fr 1fr;gap:9px 16px;margin-top:8px}}
    .ck{{display:flex;gap:10px;align-items:flex-start;padding:11px 13px;border:1px solid #e6ebf0;border-radius:11px;background:#fff;box-shadow:0 1px 3px rgba(10,16,20,0.03);break-inside:avoid}}
    .ck .cki{{width:16px;height:16px;border-radius:5px;color:#fff;font-size:9px;font-weight:700;display:flex;align-items:center;justify-content:center;flex:none;margin-top:1px}}
    .ck .ckb{{flex:1;min-width:0}}
    .ck .ckt{{font-family:"Sora",sans-serif;font-weight:700;font-size:9.5px;color:{INK9}}}
    .ck .ckc{{font-family:"JetBrains Mono",monospace;font-size:7.5px;font-weight:700;margin-left:4px}}
    .ck .cko{{font-size:8.5px;color:#5a6675;margin-top:2px;line-height:1.4}}
    .hbars{{display:flex;flex-direction:column;gap:8px}}
    .hbar{{display:flex;align-items:center;gap:10px}}
    .hbar .l{{width:52%;font-size:9.5px;color:#283038}}.hbar .l b{{color:{INK9}}}
    .hbar .tk{{flex:1;height:14px;background:#eef1f4;border-radius:4px;overflow:hidden}}
    .hbar .fl{{height:100%;border-radius:4px}}
    .hbar .st{{font-family:"JetBrains Mono",monospace;font-size:8px;font-weight:700;white-space:nowrap;width:52px;text-align:right}}
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
      <div class="s"><div class="n {'g' if cats.get('tecnico',{}).get('score',0)>=70 else 'o'}">{cats.get('tecnico',{}).get('score',0)}/100</div><div class="l">{L("salud tecnica: HTTPS, velocidad, robots, sitemap, 404", "technical health: HTTPS, speed, robots, sitemap, 404")}</div></div>
      <div class="s"><div class="n {'g' if cats.get('onpage',{}).get('score',0)>=70 else 'o'}">{cats.get('onpage',{}).get('score',0)}/100</div><div class="l">{L("SEO on-page: titulos, descripciones, H1, schema", "on-page SEO: titles, descriptions, H1, schema")}</div></div>
      <div class="s"><div class="n {'c' if cats.get('geo',{}).get('score',0)>=55 else 'o'}">{cats.get('geo',{}).get('score',0)}/100</div><div class="l">{L("preparacion para la IA (GEO / LLMO)", "AI readiness (GEO / LLMO)")}</div></div>"""

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
    speed_bit = f' {L("Medimos la velocidad real en movil y escritorio (movil", "We measured real speed on mobile and desktop (mobile")} {mob.get("performance")}/100).' if mob.get("performance") is not None else ""
    n404 = s.get("links_broken", 0)
    tec_bit = (f' {L("Rastreamos", "We crawled")} {s.get("links_checked",0)} {L("direcciones una a una", "addresses one by one")}'
               + (f' {L("y encontramos", "and found")} {n404} {L("enlace(s) roto(s).", "broken link(s).")}' if n404 else L(" sin enlaces rotos.", " with no broken links.")))
    lede = (f'{L("Diagnostico con <b>datos reales comprobados en vivo</b>: revisamos tu web por dentro, pagina a pagina.", "Diagnosis with <b>real data checked live</b>: we reviewed your site from the inside, page by page.")}{tec_bit}{ai_bit}{speed_bit} '
            f'{L("Aqui tienes lo que encontramos y el plan para que Google y la IA te encuentren y te recomienden.", "Here is what we found and the plan for Google and AI to find and recommend you.")}')

    # Analisis dinamico para "Como esta tu web hoy" (en vez de explicar la formula)
    _areas = [(L("la base tecnica", "the technical base"), cats.get("tecnico", {}).get("score", 0)),
              (L("el SEO on-page", "on-page SEO"), cats.get("onpage", {}).get("score", 0)),
              (L("la preparacion para la IA (GEO)", "AI readiness (GEO)"), cats.get("geo", {}).get("score", 0))]
    _best = max(_areas, key=lambda a: a[1]); _worst = min(_areas, key=lambda a: a[1])
    _ai_estado = ""
    if ai.get("available") and not ai.get("error"):
        if not ai.get("knows_brand"):
            _ai_estado = L(" Y lo que mas pesa: al preguntarle a la IA, no sabe quien eres.", " And what matters most: when asked, AI doesn't know who you are.")
        elif ai.get("recommended") is False:
            _ai_estado = L(" Y aunque la IA te reconoce, cuando piden tu servicio recomienda a otros.", " And although AI recognizes you, when people ask for your service it recommends others.")
    _idx = L(" Ademas tu web se esta bloqueando a si misma (noindex).", " On top of that, your site is blocking itself (noindex).") if m.get("robots_noindex") else ""
    _sev = L("solida","solid") if score >= 80 else L("aceptable pero mejorable","acceptable but improvable") if score >= 60 else L("con carencias importantes","with significant gaps") if score >= 45 else L("muy debil","very weak")
    estado_analisis = (L(f'Tu web saca <b>{score}/100</b> en salud digital: una base <b>{_sev}</b>.', f'Your site scores <b>{score}/100</b> in digital health: a <b>{_sev}</b> base.')
                       + f'{L(" Tu punto mas fuerte es", " Your strongest point is")} <b>{_best[0]}</b> ({_best[1]}/100) {L("y donde mas visibilidad pierdes es", "and where you lose the most visibility is")} '
                       f'<b>{_worst[0]}</b> ({_worst[1]}/100).{_ai_estado}{_idx} '
                       f'{L("La nota combina pruebas tecnicas en vivo, velocidad real, on-page, seguridad y una consulta real a la IA.", "The score combines live technical tests, real speed, on-page, security and a real query to AI.")}')

    return f"""<!doctype html><html lang="{L('es','en')}"><head><meta charset="utf-8"><style>{css}</style></head><body>

<section class="cover">
  <div class="accent"></div>
  <div class="brand"><img src="{_logo_uri()}" alt="Cupperlab"><span class="cl">{L('Diagnostico SEO &amp; GEO', 'SEO &amp; GEO Diagnosis')}<b>{today}</b></span></div>
  <div class="ey">{L('Como te ven Google y la IA hoy', 'How Google and AI see you today')}</div>
  <h1>{h1}<span class="dom">{dom}</span></h1>
  <p class="lede">{lede}</p>
  <div class="facts">{facts}</div>
  <div class="band"><b>{band_b}</b><p>{band_p}</p></div>
  <div class="msec">{L('Que hemos analizado · en vivo', 'What we analyzed · live')}</div>
  <div class="method4">
    <div class="m"><div class="no">01</div><b>{L('Pruebas tecnicas', 'Technical tests')}</b><p>{L('HTTPS, robots, sitemap, velocidad y enlaces rotos (404), uno a uno.', 'HTTPS, robots, sitemap, speed and broken links (404), one by one.')}</p></div>
    <div class="m"><div class="no">02</div><b>On-page</b><p>{L('Titulos, descripciones, H1, canonical y vista previa al compartir.', 'Titles, descriptions, H1, canonical and share preview.')}</p></div>
    <div class="m"><div class="no">03</div><b>{L('Preparacion IA (GEO)', 'AI readiness (GEO)')}</b><p>{L('Datos estructurados, llms.txt, marca como entidad y estructura.', 'Structured data, llms.txt, brand as entity and structure.')}</p></div>
    <div class="m"><div class="no">04</div><b>{L('Consulta a la IA', 'AI query')}</b><p>{L('Le preguntamos a la IA si te conoce y si te recomienda.', 'We ask AI whether it knows you and whether it recommends you.')}</p></div>
  </div>
  <div class="foot"><span>{L('Preparado por', 'Prepared by')} <b>Cupperlab</b> · {today} · {L('Verificado en vivo', 'Verified live')}</span><span>{L('Confidencial', 'Confidential')}</span></div>
</section>

<section class="pg">
  <div class="eyebrow"><span class="bar"></span>01 · {L('Estado general', 'Overall status')}</div>
  <h2 class="sec">{L('Como esta tu web hoy', 'How your site stands today')}</h2>
  <p class="sub">{estado_analisis}</p>
  <div class="block scorewrap">
    <div class="gauge">{_gauge(score, L('Salud digital', 'Digital health'))}</div>
    <div class="levels">{_levels(r)}</div>
  </div>
  {_estado_resumen(r)}
</section>

<section class="pg">
  <div class="eyebrow"><span class="bar"></span>02 · {L('Salud tecnica de tu web', 'Your site technical health')}</div>
  <h2 class="sec">{L('Que falla (y que funciona) por dentro', 'What fails (and what works) under the hood')}</h2>
  <p class="sub">{L('Lo tecnico que Google mira para decidir si te muestra: seguridad, respuesta del servidor, robots, mapa del sitio y enlaces rotos. En rojo lo que falla, en verde lo que ya funciona.', 'The technical signals Google looks at to decide whether to show you: security, server response, robots, sitemap and broken links. In red what fails, in green what already works.')}</p>
  {_tech_rows(r)}
  {_robots_block(r)}
  {_crawl_structure_block(r)}
  {_index_block(r)}
  {(''.join('<div class="block callout r"><b>' + L("Enlaces rotos.", "Broken links.") + '</b> ' + L("Ejemplos reales encontrados:", "Real examples found:") + ' ' + ', '.join(e["url"] for e in s["broken_examples"][:3]) + '.</div>' for _ in [0]) if s.get("broken_examples") else '')}
</section>

<section class="pg">
  <div class="eyebrow"><span class="bar"></span>03 · {L('SEO on-page', 'On-page SEO')}</div>
  <h2 class="sec">{L('Que le falta a tus paginas para posicionar', 'What your pages are missing to rank')}</h2>
  <p class="sub">{L('Primero lo que falla y hay que corregir; despues lo que ya esta bien. Son las senales que deciden si Google te muestra y si la IA te cita.', 'First what fails and needs fixing; then what is already fine. These are the signals that decide whether Google shows you and whether AI cites you.')}</p>
  {_onpage_rows(r)}
  {_porque_como(r)}
</section>

<section class="pg">
  {_ai_section(r)}
  {_speed_section(r)}
  {_security_section(r)}
</section>

{(f'''<section class="pg">
  {_google_section(r)}
</section>''') if _google_section(r) else ''}

<section class="pg">
  <div class="eyebrow"><span class="bar"></span>06 · {L('Plan de accion', 'Action plan')}</div>
  <h2 class="sec">{L('Todo lo que hay que mejorar, por orden de impacto', 'Everything to improve, in order of impact')}</h2>
  <p class="sub">{L('La lista completa de lo que corregir para posicionar en Google y en la IA: base tecnica y robots primero (que puedan leerte), luego on-page y contenido, y por ultimo las senales para que la IA te reconozca y te recomiende.', 'The full list of what to fix to rank on Google and AI: technical base and robots first (so they can read you), then on-page and content, and finally the signals that get AI to recognize and recommend you.')}</p>
  {_priority_table(r)}
  {_geo_plan_block(r)}
  {_que_esperamos(r)}
  <div class="closeband">
    <div class="l"><b>{L('¿Damos el siguiente paso?', 'Shall we take the next step?')}</b><p>{L('Ponemos en marcha este plan contigo: base tecnica, on-page, contenido y las senales que hacen que la IA te recomiende. Primera revision sin costo.', 'We put this plan into motion with you: technical base, on-page, content and the signals that get AI to recommend you. First review at no cost.')}</p></div>
    <div class="r">{L('Tel', 'Tel')} <b>{contact.get('phone','')}</b><br>{contact.get('email','')}<br>{L('Mejoramos tu rentabilidad.', 'We improve your profitability.')}</div>
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
