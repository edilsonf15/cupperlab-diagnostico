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
    return GREEN if s >= 70 else AMBER if s >= 55 else OR7 if s >= 40 else RED


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
    # --- Base tecnica (que exista y sea rastreable) ---
    if not s.get("https"):
        add("Activar la conexion segura (HTTPS): sin candado Google penaliza y el navegador avisa de web no segura.",
            "Alto", "Bajo", 1)
    if not m.get("viewport"):
        add("Adaptar la web a movil (viewport): la mayoria de tus clientes te abren desde el celular.", "Alto", "Medio", 1)
    if rb.get("blocks_all"):
        add("URGENTE: tu robots.txt bloquea TODO el sitio (Disallow: /). Google no puede rastrearte. Quitar ese bloqueo.",
            "Alto", "Bajo", 1)
    elif not rb.get("present"):
        add("Publicar un robots.txt que guie el rastreo de Google y declare el mapa del sitio.", "Medio", "Bajo", 1)
    if rb.get("present") and rb.get("suggest_block"):
        add("Afinar el robots.txt: bloquear " + ", ".join(rb["suggest_block"]) +
            " para que Google no gaste rastreo en paginas sin valor y priorice las que venden.", "Medio", "Bajo", 2)
    if not s.get("sitemap"):
        add("Crear el mapa del sitio (sitemap.xml) para que Google y la IA descubran todas tus paginas.", "Alto", "Bajo", 1)
    elif not s.get("sitemap_in_robots"):
        add("Declarar el mapa del sitio dentro del robots.txt para que Google lo encuentre antes.", "Bajo", "Bajo", 2)
    if s.get("links_broken", 0) > 0:
        add(f"Reparar los {s['links_broken']} enlace(s) roto(s) (404) y limpiar el mapa del sitio (quitar etiquetas y paginas vacias).",
            "Medio", "Medio", 2)
    # --- On-page (que Google entienda y muestre) ---
    if not m.get("title") or not (25 <= len(m.get("title", "")) <= 65):
        add("Escribir titulos unicos por pagina (55-60 caracteres) con el servicio y la ciudad.", "Alto", "Bajo", 1)
    if not m.get("description"):
        add("Escribir una meta descripcion por pagina: es el resumen que Google muestra y que la IA cita.", "Alto", "Bajo", 1)
    if m.get("h1_count", 0) != 1:
        add("Marcar un titular principal (H1) claro y unico en cada pagina.", "Medio", "Bajo", 1)
    if not m.get("canonical"):
        add("Anadir la URL canonica para que Google no vea paginas duplicadas.", "Medio", "Bajo", 2)
    if not m.get("lang"):
        add("Declarar el idioma de la web (atributo lang) para paises e IA.", "Bajo", "Bajo", 2)
    if m.get("word_count", 0) < 300:
        add("Ampliar el contenido de las paginas clave: texto propio que responda lo que busca el cliente.", "Medio", "Medio", 2)
    if not (m.get("og_title") and m.get("og_image")):
        add("Poner la vista previa al compartir (Open Graph) para ganar clics al enlazarte en redes y chats.", "Medio", "Bajo", 1)
    it = m.get("img_total", 0); ia = m.get("img_alt", 0)
    if it and ia / it < 0.7:
        add(f"Describir las imagenes (texto ALT): hoy {ia} de {it} lo tienen. Ayuda al SEO y a la accesibilidad.",
            "Medio", "Bajo", 2)
    # --- GEO / IA ---
    if not m.get("schema_types"):
        add("Anadir los datos estructurados (schema: organizacion, servicios, preguntas frecuentes) para que "
            "Google y la IA entiendan tu negocio.", "Alto", "Bajo", 1)
    if not m.get("has_sameas"):
        add("Conectar tu marca como entidad (perfiles y sameAs) para que la IA sepa que eres una empresa real.",
            "Medio", "Bajo", 2)
    if not s.get("llms_txt"):
        add("Publicar una guia para los buscadores con IA (llms.txt).", "Medio", "Bajo", 2)
    if ai.get("available") and not ai.get("error"):
        if not ai.get("knows_brand"):
            add("Hacer que la IA te reconozca: ficha de empresa clara, perfiles consistentes y rastro externo "
                "(directorios, prensa, resenas) que la IA pueda citar.", "Alto", "Medio", 2)
        if ai.get("recommended") is not True:
            add("Entrar en las recomendaciones de la IA: una pagina por servicio con el vocabulario del cliente "
                "y senales de autoridad para que te mencione junto a tu competencia.", "Alto", "Medio", 2)
    # --- Datos y velocidad ---
    if not an.get("has_any"):
        add("Instalar analitica (Google Analytics 4 + Tag Manager) para saber que paginas te traen clientes.",
            "Medio", "Bajo", 1)
    elif an.get("duplicated"):
        add("Corregir la analitica duplicada: dejar una sola medicion para que tus datos sean fiables.", "Medio", "Bajo", 1)
    if mob is not None and mob < 60:
        add(f"Acelerar el movil (hoy {mob}/100): comprimir imagenes y aligerar la portada para bajar de 2,5 s de carga.",
            "Alto", "Medio", 1)
    add("Medir cada semana tu posicion en buscadores y si la IA ya te reconoce y te recomienda.", "Medio", "Bajo", 2)
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
        return ('<div class="block callout g"><b>Sin fallos graves.</b> No detectamos problemas criticos '
                'en el analisis rapido. Toca mantener y monitorizar.</div>')
    order = {"alto": 0, "medio": 1, "bajo": 2}
    items = sorted(items, key=lambda f: order.get(f.get("severity", "medio"), 1))[:6]
    pill = {"alto": ("crit", "Alto"), "medio": ("med", "Medio"), "bajo": ("low", "Bajo")}
    trs = ""
    for f in items:
        sev = f.get("severity", "medio")
        cls, lab = pill.get(sev, ("med", "Medio"))
        trs += (f'<tr><td><b>{f.get("title","")}</b></td>'
                f'<td class="c"><span class="pill {cls}">{lab}</span></td>'
                f'<td>{f.get("detail","")}</td></tr>')
    return f"""<div class="block">
      <div class="sectic">Puntos debiles detectados · que corregir y por que</div>
      <table class="t"><thead><tr><th>Punto debil</th><th class="c">Gravedad</th><th>Que te cuesta hoy</th></tr></thead>
      <tbody>{trs}</tbody></table>
      <p style="font-size:8.5px;color:#7b8694;margin:7px 0 0;font-style:italic">Ordenado por gravedad (impacto en captacion, posicionamiento y en que la IA te recomiende). Todo verificado en vivo.</p>
    </div>"""


def _priority_table(r: dict) -> str:
    plan = build_plan(r)
    order = {"Alto": 0, "Medio": 1, "Bajo": 2}
    plan = sorted(plan, key=lambda p: order.get(p["impacto"], 1))
    def imp_pill(v):
        return f'<span class="pill {"crit" if v=="Alto" else "med" if v=="Medio" else "ok"}">{v}</span>'
    rows = "".join(
        f'<tr><td>{p["text"]}</td><td class="c">{imp_pill(p["impacto"])}</td></tr>'
        for p in plan)
    return f"""<table class="t"><thead><tr><th>Accion (en lenguaje de negocio)</th>
      <th class="c">Impacto</th></tr></thead>
      <tbody>{rows}</tbody></table>"""


def _target(score: int) -> int:
    return min(score + (28 if score < 45 else 22 if score < 65 else 12), 92)


def _objetivo_box(score: int) -> str:
    return f"""<div class="objbox">
      <div class="r"><span class="a">{score}</span><span class="ar">&#8594;</span><span class="b">~{_target(score)}</span></div>
      <div class="l">Objetivo de salud SEO/GEO tras el plan</div></div>"""


def _priority_segbar(r: dict) -> str:
    items = r.get("findings_improve", [])
    n_alto = sum(1 for f in items if f.get("severity") == "alto")
    n_med = sum(1 for f in items if f.get("severity") == "medio")
    n_bajo = sum(1 for f in items if f.get("severity") == "bajo")
    total = max(n_alto + n_med + n_bajo, 1)
    segs = [("Criticos/Altos", n_alto, RED), ("Medios", n_med, AMBER), ("Menores", n_bajo, CY6), ("Mejora", 1, "#5a6675")]
    bar = ""
    for lab, cnt, col in segs:
        w = max(10, round(100 * (cnt if cnt else 0.4) / (total + 0.4)))
        bar += f'<div class="sg" style="width:{w}%;background:{col}">{lab if w > 14 else ""}</div>'
    return f"""<div class="block card">
      <div class="sectic">Reparto de los hallazgos por prioridad</div>
      <div class="segbar">{bar}</div>
      <div class="seglg">
        <div class="i"><span class="sw" style="background:{RED}"></span>Criticos/Altos: lo que mas frena hoy tu captacion y tu visibilidad en la IA</div>
        <div class="i"><span class="sw" style="background:{AMBER}"></span>Medios: mejoras de indexacion y experiencia</div>
        <div class="i"><span class="sw" style="background:{CY6}"></span>Menores: ajustes finos</div>
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
    reconoce = "que la IA te reconozca y te recomiende al pedir tu servicio" \
        if not (ai.get("knows_brand") and ai.get("recommended")) else "consolidar tu presencia en la IA y ganar la categoria"
    return f"""<div class="two" style="margin-top:2px">
      {_objetivo_box(score)}
      <div class="card"><h3>Que esperamos ver</h3>
      <p style="font-size:9.5px;color:#3d4855;line-height:1.55">Primero: la web con textos propios por pagina, un titular claro y el movil mas rapido; tu marca ganando su propia busqueda. Despues: {reconoce} y primeras posiciones en busquedas de tu categoria. Pasar de <b style="color:{INK9}">{score}</b> a <b style="color:{GREEN}">~{tgt}</b> de salud SEO/GEO es trabajo de textos, senales y contenido: rapido de mover y medible desde el primer dia.</p></div>
    </div>"""


def _porque_como(r: dict) -> str:
    """Por que importa cada carencia (el 'como se corrige' va en el Plan de accion)."""
    m = r.get("meta", {}); s = r.get("signals", {})
    an = s.get("analytics") or {}
    por = []
    if not m.get("title") or not (25 <= len(m.get("title", "")) <= 65):
        por.append(("no", "Con el <b>titulo</b> mal dimensionado, Google recorta o ignora como te presenta en los resultados."))
    if not m.get("description"):
        por.append(("no", "Sin <b>meta descripcion</b>, Google inventa el resumen y la IA no tiene una frase clara con que citarte."))
    if m.get("h1_count", 0) != 1:
        por.append(("mid", "El <b>H1</b> le dice a Google de que va la pagina; si falta o hay varios, se diluye el mensaje."))
    if not m.get("schema_types"):
        por.append(("no", "Sin <b>datos estructurados</b>, la IA tiene cero etiquetas con que entender tu negocio y a quien sirves."))
    if not m.get("has_sameas"):
        por.append(("mid", "Tu <b>marca no esta conectada como entidad</b>: la IA no sabe si eres una empresa real y verificable."))
    if (m.get("img_total", 0) and m.get("img_alt", 0) / max(m["img_total"], 1) < 0.7):
        por.append(("mid", "Imagenes <b>sin texto ALT</b>: menos resultados enriquecidos en Google y menos accesibilidad."))
    if not s.get("llms_txt"):
        por.append(("mid", "Sin <b>guia para IA (llms.txt)</b>, los buscadores con IA no saben que priorizar de tu sitio."))
    if not an.get("has_any"):
        por.append(("mid", "Sin <b>analitica</b> no sabes que paginas convierten, asi que no puedes mejorar con datos."))
    por = por[:5] or [("ok", "La base on-page esta bien; quedan ajustes finos que refuerzan lo que ya funciona.")]

    def lis(items):
        out = ""
        for kind, txt in items:
            col = {"no": RED, "mid": AMBER, "ok": GREEN}[kind]
            ico = {"no": "&#10005;", "mid": "!", "ok": "&#10003;"}[kind]
            out += f'<li><span class="i" style="background:{col}">{ico}</span>{txt}</li>'
        return out
    return f"""<div class="block card">
      <div class="sectic" style="margin-bottom:8px">Por que importa cada carencia (como se corrige, en el plan de accion)</div>
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
        robots_st, robots_code, robots_obs = "crit", "Bloquea todo", "Disallow: / — Google no puede rastrear el sitio"
    elif not s["robots"]:
        robots_st, robots_code, robots_obs = "hi", "Falta", "Sin robots.txt: no guias el rastreo de Google"
    else:
        extra = f'{rb.get("disallow_count",0)} reglas' + (", declara sitemap" if rb.get("has_sitemap") else ", no declara el sitemap")
        robots_st = "ok" if rb.get("has_sitemap") else "med"
        robots_code, robots_obs = "OK", extra
    rows = [
        ("Conexion segura (HTTPS)", "OK" if s["https"] else "Falla", "ok" if s["https"] else "crit",
         "Certificado valido" if s["https"] else "Sin candado de seguridad"),
        ("Respuesta del servidor", f'{s["home_status"]}', "ok" if s["home_status"] < 300 else "hi",
         f'Responde en {s["home_time"]}s'),
        ("robots.txt", robots_code, robots_st, robots_obs),
        ("Mapa del sitio (sitemap)", "OK" if s["sitemap"] else "Falta", "ok" if s["sitemap"] else "hi",
         f'{s["sitemap_total"]} URLs listadas' if s["sitemap"] else "No encontrado"),
        ("llms.txt (guia para IA)", "OK" if s["llms_txt"] else "404", "ok" if s["llms_txt"] else "hi",
         "Presente" if s["llms_txt"] else "No existe: sin guia para los buscadores con IA"),
        ("Enlaces rotos (404)", f'{s["links_broken"]}/{s["links_checked"]}',
         "ok" if s["links_broken"] == 0 else ("crit" if s["broken_ratio"] > 0.2 else "med"),
         "Sin enlaces rotos en la muestra" if s["links_broken"] == 0 else "Paginas que ya no existen"),
        ("Preparada para movil", "OK" if m["viewport"] else "Falta", "ok" if m["viewport"] else "med",
         "Etiqueta viewport presente" if m["viewport"] else "Sin viewport movil"),
    ]
    return _check_list(rows)


def _robots_block(r: dict) -> str:
    """Recuadro con el analisis del robots.txt: que bloquea y que conviene bloquear."""
    rb = (r.get("signals") or {}).get("robots_info") or {}
    if not rb.get("present"):
        return ('<div class="block callout o"><b>robots.txt.</b> No encontramos robots.txt. Conviene publicarlo '
                'para guiar a Google (que rastree lo importante) y declarar ahi tu mapa del sitio.</div>')
    parts = []
    tone = "o"
    if rb.get("blocks_all"):
        tone = "r"
        parts.append('<span style="color:' + RED + '"><b>Bloquea TODO el sitio (User-agent: * Disallow: /)</b>: Google no puede rastrearte. Corregir ya.</span>')
    if rb.get("good_blocks"):
        parts.append("Ya bloqueas bien " + ", ".join(rb["good_blocks"]) +
                     " (evita que Google gaste rastreo en paginas sin valor).")
    elif rb.get("disallow_sample"):
        parts.append("Hoy bloquea: <b>" + ", ".join(rb["disallow_sample"][:5]) + "</b>.")
    if rb.get("suggest_block"):
        parts.append("Conviene bloquear tambien " + ", ".join(rb["suggest_block"]) + ".")
    if not rb.get("has_sitemap"):
        parts.append("No declara el <b>sitemap</b> dentro del robots: anadirlo ayuda a que Google lo descubra antes.")
    if not parts:
        parts.append("Bien configurado: guia el rastreo y declara el sitemap.")
    return f'<div class="block callout {tone}"><b>Analisis del robots.txt.</b> ' + " ".join(parts) + "</div>"


def _onpage_rows(r: dict) -> str:
    m = r["meta"]
    tl = len(m["title"]); dl = len(m["description"])
    rows = [
        ("Titulo de la pagina", "OK" if 25 <= tl <= 65 else ("Largo" if tl > 65 else ("Corto" if tl else "Falta")),
         "ok" if 25 <= tl <= 65 else ("med" if tl else "crit"), f'{tl} caracteres'),
        ("Titular principal (H1)", "OK" if m["h1_count"] == 1 else ("Varios" if m["h1_count"] > 1 else "Falta"),
         "ok" if m["h1_count"] == 1 else ("med" if m["h1_count"] > 1 else "crit"), f'{m["h1_count"]} en la home'),
        ("Meta descripcion", "OK" if 70 <= dl <= 165 else ("Corta" if dl else "Falta"),
         "ok" if 70 <= dl <= 165 else ("med" if dl else "crit"), f'{dl} caracteres'),
        ("URL canonica", "OK" if m["canonical"] else "Falta", "ok" if m["canonical"] else "med",
         "Presente" if m["canonical"] else "Sin canonical"),
        ("Vista previa (Open Graph)", "OK" if (m["og_title"] and m["og_image"]) else "Incompleta",
         "ok" if (m["og_title"] and m["og_image"]) else "hi",
         "Titulo e imagen" if (m["og_title"] and m["og_image"]) else "Se comparte sin tarjeta"),
        ("Datos estructurados (schema)", "OK" if m["schema_types"] else "Pobre", "ok" if m["schema_types"] else "hi",
         (", ".join(m["schema_raw_types"][:4]) if m["schema_types"] else "Sin datos estructurados")),
        ("Idioma declarado", "OK" if m["lang"] else "Falta", "ok" if m["lang"] else "med",
         m["lang"] or "Sin atributo lang"),
    ]
    it = m.get("img_total", 0); ia = m.get("img_alt", 0)
    cov = round(100 * ia / it) if it else 100
    rows.append(("Texto ALT en imagenes", "OK" if cov >= 70 else ("Parcial" if cov >= 30 else "Pobre"),
                 "ok" if cov >= 70 else ("med" if cov >= 30 else "hi"),
                 f"{ia}/{it} imagenes con ALT ({cov}%)" if it else "sin imagenes"))
    hl = m.get("hreflangs", [])
    rows.append(("Idiomas / hreflang", "OK" if hl else "Uno", "ok" if hl else "med",
                 (", ".join(hl[:6]) if hl else "Web en un solo idioma (sin hreflang)")))
    return _check_list(rows)


def _sitemap_comp_block(r: dict) -> str:
    comp = (r.get("signals") or {}).get("sitemap_comp")
    if not comp or not comp.get("total"):
        return ""
    labels = [("paginas", "Paginas reales"), ("entradas", "Noticias / blog"),
              ("etiquetas", "Etiquetas / categorias"), ("fichas", "Fichas / descargas"),
              ("otras", "Otras (feeds, adjuntos)")]
    trs = ""
    for k, lab in labels:
        n = comp.get(k, 0)
        if n:
            verdict = "ok" if k in ("paginas", "entradas") else "med"
            trs += f'<tr><td>{lab}</td><td class="c"><b>{n}</b></td><td class="c">{_pill(verdict, "util" if verdict=="ok" else "revisar")}</td></tr>'
    return f"""
      <div class="sectic" style="margin-top:12px">Composicion del mapa del sitio · {comp['total']} URLs</div>
      <table class="t"><thead><tr><th>Tipo de URL</th><th class="c">Cuantas</th><th class="c">Veredicto</th></tr></thead>
      <tbody>{trs}</tbody></table>"""


def _levels(r: dict) -> str:
    cats = r["categories"]; s = r["signals"]
    tec = cats.get("tecnico", {}); onp = cats.get("onpage", {}); geo = cats.get("geo", {})
    ai = r.get("geo_ai") or {}
    lv = [
        ("Fundamentos tecnicos (HTTPS, respuesta, robots)", tec.get("score", 0)),
        ("On-page (titulos, descripciones, H1)", onp.get("score", 0)),
        ("Preparacion para la IA (GEO / LLMO)", geo.get("score", 0)),
        ("Enlaces y rastreo (404, sitemap)", round((100 * (1 - s["broken_ratio"]) + (100 if s["sitemap"] else 0)) / 2)),
        ("Datos estructurados (schema)", _chk_pct(geo, "schema")),
        ("Respuesta del servidor", 100 if s["home_time"] < 1.5 else 60 if s["home_time"] < 3 else 30),
    ]
    if ai.get("available") and not ai.get("error"):
        lv.append(("Reconocimiento real por la IA", ai.get("ai_score", 0)))
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
    """Tacticas concretas de posicionamiento en IA (GEO), segun senales reales."""
    m = r.get("meta", {}); s = r.get("signals", {})
    st = [x.lower() for x in (m.get("schema_types") or [])]
    tips = []
    if "faqpage" not in st:
        tips.append("Anadir <b>Preguntas frecuentes con datos estructurados (FAQ schema)</b> en las paginas de servicio: la IA cita respuestas directas.")
    if not any(x in st for x in ("organization", "localbusiness", "professionalservice")):
        tips.append("Marcar tu <b>ficha de empresa (Organization/LocalBusiness schema)</b>: nombre, direccion, telefono y zona.")
    if not m.get("has_sameas"):
        tips.append("Conectar tus <b>perfiles oficiales (sameAs)</b> para que la IA te reconozca como entidad real y verificable.")
    if not s.get("llms_txt"):
        tips.append("Publicar <b>llms.txt</b> como guia para los buscadores con IA.")
    tips.append("Crear <b>una pagina por servicio</b> con el vocabulario del cliente y contenido que responda sus preguntas reales.")
    tips.append("Sumar <b>resenas y menciones externas</b> (Google, directorios, prensa) que la IA pueda citar.")
    return tips[:5]


def _ai_section(r: dict) -> str:
    ai = r.get("geo_ai") or {}
    answered = ai.get("answered_names") or []
    if not (ai.get("available") and not ai.get("error") and answered):
        geo = r["categories"].get("geo", {})
        return f"""
        <div class="eyebrow"><span class="bar"></span>04 · Como te ve la inteligencia artificial</div>
        <h2 class="sec">Como te ve la IA</h2>
        <p class="sub">Medimos las senales que la IA usa para entenderte y citarte, y le preguntamos por ti en vivo.</p>
        <div class="block scorewrap"><div class="gauge">{_gauge(geo.get('score',0), 'Preparacion IA')}</div>
        <div class="levels">{_levels(r)}</div></div>"""

    brand = ai.get("brand", r["domain"])
    country = ai.get("country") or ""
    knows = ai.get("knows_brand"); reco = ai.get("recommended")
    # respuesta textual sobre la marca (de ChatGPT), presentada como "la IA"
    raw = (ai.get("brand_description") or "").strip()
    if not raw:
        raw = "Te reconoce y te describe." if knows else "No tengo informacion fiable sobre esta empresa."
    comps = _comp_names(ai) or "otras firmas de tu sector"

    card1 = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">Le preguntamos a la IA: "¿Que es {brand} y a que se dedica?"</div>
        <div class="qt">"{raw[:320]}"</div></div></div>
      <div class="src"><span>Consulta a la IA en vivo · sobre tu marca</span>
        <span class="v {'yes' if knows else 'no'}">{'TE RECONOCE' if knows else 'NO TE RECONOCE'}</span></div>
    </div>"""
    card2 = f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">Le preguntamos a la IA: "¿Que empresas recomiendas para este servicio?"</div>
        <div class="qt">Menciono a: {comps}.</div></div></div>
      <div class="src"><span>Consulta a la IA en vivo · sobre tu categoria (sin nombrarte)</span>
        <span class="v {'yes' if reco else 'no'}">{'TE RECOMIENDA' if reco else ('NO TE RECOMIENDA' if reco is False else 'SIN DETERMINAR')}</span></div>
    </div>"""

    # Las 3 busquedas reales de un cliente (el corazon de la prueba GEO), como tarjetas
    questions = ai.get("questions") or []
    q_cards = ""
    for q in questions[:3]:
        ap = q.get("appears")
        named = ", ".join(q.get("named", [])[:4]) or "—"
        vt = "APARECES" if ap is True else ("NO APARECES" if ap is False else "SIN DATO")
        vc = "yes" if ap is True else ("no" if ap is False else "")
        q_cards += f"""
    <div class="aiq">
      <div class="q"><div class="ico">IA</div><div>
        <div class="ask">Un cliente busca: "{_esc(q.get('q',''))}"</div>
        <div class="qt">La IA recomienda a: {_esc(named)}.</div></div></div>
      <div class="src"><span>Busqueda de categoria{(' · ' + _esc(country)) if country else ''}</span>
        <span class="v {vc}">{vt}</span></div>
    </div>"""
    q_block = (f'<div class="sectic" style="margin-top:12px">Las 3 busquedas reales de un cliente'
               f'{(" en " + _esc(country)) if country else ""} · ¿sales tu?</div>{q_cards}') if q_cards else ""

    knows_any = knows; reco_any = reco
    verdict = ("La IA te reconoce y te recomienda: vas por delante de la mayoria." if knows_any and reco_any
               else f"La IA te describe, pero cuando alguien pide tu servicio nombra a {comps}, no a ti: pierdes a los clientes que aun no te conocen." if knows_any
               else f"La IA no sabe quien eres y recomienda a {comps}: hoy no apareces cuando preguntan por tu servicio.")

    if knows_any and not reco_any:
        topnote = ("<b>Solo apareces si dan tu direccion exacta.</b> La IA te describe cuando le pasan tu web, "
                   "pero cuando alguien busca tu servicio sin conocerte, no te nombra: ahi es donde pierdes clientes.")
    elif not knows_any:
        topnote = ("<b>La IA no te encuentra.</b> Ni sabiendo tu nombre te reconoce: hoy no existes para quien "
                   "pregunta a la IA antes de comprar.")
    else:
        topnote = "<b>Buena senal:</b> la IA te reconoce y te incluye. Toca mantener la ventaja."

    gap_c = _clean_gap(ai.get("gap"))
    tactics = _geo_tactics(r)
    tac_lis = "".join(f'<li><span class="i" style="background:{CY6}">+</span>{t}</li>' for t in tactics)
    gap_block = (f'<div class="block card"><div class="sectic" style="margin-bottom:8px">Que te falta para que la IA te recomiende (por corregir)</div>'
                 + (f'<p style="font-size:9.5px;color:#3d4855;line-height:1.55;margin-bottom:10px">{_esc(gap_c)}</p>' if gap_c else "")
                 + f'<ul class="chk">{tac_lis}</ul></div>')

    cards = card1 + card2
    return f"""
    <div class="eyebrow"><span class="bar"></span>04 · Como te ve la inteligencia artificial</div>
    <h2 class="sec">Como te ve la IA cuando preguntan por ti</h2>
    <p class="sub">Le preguntamos a la IA, en vivo: por tu marca, por tu servicio y con busquedas reales de cliente. Cada vez mas gente busca asi antes de decidir.</p>
    <div class="block callout {'g' if (knows_any and reco_any) else 'r'}">{topnote}</div>
    {cards}{q_block}
    <div class="block callout {'g' if (knows_any and reco_any) else 'o' if knows_any else 'r'}"><b>Veredicto IA.</b> {verdict}</div>
    {gap_block}"""


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
                    f'<p style="font-size:9px;color:#7b8694">Medicion no disponible en esta prueba.</p></div>')
        perf = d.get("performance")
        col = _color(perf if perf is not None else 0)
        rows = ""
        for lab, k in [("Primer contenido (FCP)", "fcp"), ("Contenido principal (LCP)", "lcp"),
                       ("Bloqueo por codigo (TBT)", "tbt"), ("Indice de velocidad", "si")]:
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
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>Rendimiento · movil frente a escritorio</div>
    <h2 class="sec">Velocidad de tu web</h2>
    <div class="block two">{card('Movil', m, True)}{card('Escritorio', d, False)}</div>"""


def _google_section(r: dict) -> str:
    g = r.get("google")
    if not g:
        return ""
    bq = g.get("brand_query") or {}
    bpos = bq.get("position")
    brand_verdict = (f"apareces el <b>nº{bpos}</b>" if bpos else "<b>no apareces</b> ni al buscar tu propio nombre")
    cat_rows = ""
    for c in g.get("category", []):
        pos = c.get("position")
        badge = (f'<span class="pill ok">nº{pos}</span>' if pos and pos <= 10 else '<span class="pill hi">Fuera top-10</span>')
        top = ", ".join(c.get("top", [])[:3]) or "-"
        cat_rows += f'<tr><td>{c.get("query","")}</td><td class="c">{badge}</td><td class="u">{top}</td></tr>'
    if not cat_rows:
        return ""
    ai = r.get("geo_ai") or {}
    pais = ai.get("country") or ""
    pais_txt = f" en {pais}" if pais else ""
    return f"""
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>Como te ve Google</div>
    <h2 class="sec">En que posicion apareces, busqueda a busqueda</h2>
    <p class="sub">Busquedas reales de un cliente de tu sector{pais_txt} (el mercado donde opera tu web). En tu propia marca {brand_verdict}.</p>
    <table class="t"><thead><tr><th>Lo que busca un cliente (categoria)</th><th class="c">¿Apareces?</th><th>Quien sale en tu lugar</th></tr></thead>
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
                f'<td>La IA lo recomienda a un cliente que pide tu mismo servicio</td></tr>')
    return f"""
    <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>Quien capta hoy tu demanda</div>
    <h2 class="sec">Tu competencia real, la que la IA sí recomienda</h2>
    <p class="sub">Negocios de tu mismo sector que la IA nombra cuando alguien pide tu servicio y tu marca no aparece. No tienen mejor producto: tienen mejor rastro digital, y por eso la IA los cita.</p>
    <table class="t"><thead><tr><th>Competidor</th><th>Su web</th><th>Por que aparece (y tu no)</th></tr></thead><tbody>{trs}</tbody></table>"""


def _estado_resumen(r: dict) -> str:
    """Resumen ejecutivo especifico de ESTE sitio: punto fuerte, punto debil y las
    carencias concretas detectadas (para que ningun informe se lea igual a otro)."""
    cats = r.get("categories", {}); s = r.get("signals", {}); m = r.get("meta", {})
    named = {"la salud tecnica": cats.get("tecnico", {}).get("score", 0),
             "el SEO on-page": cats.get("onpage", {}).get("score", 0),
             "la preparacion para la IA (GEO)": cats.get("geo", {}).get("score", 0)}
    best = max(named, key=named.get); worst = min(named, key=named.get)
    weak = []
    if not s.get("https"):
        weak.append("no tiene HTTPS")
    if (s.get("robots_info") or {}).get("blocks_all"):
        weak.append("el robots.txt bloquea todo el sitio")
    if not m.get("schema_types"):
        weak.append("le faltan los datos estructurados (schema)")
    if not m.get("description"):
        weak.append("no tiene meta descripcion")
    if m.get("h1_count", 0) != 1:
        weak.append(f"el H1 no esta bien definido ({m.get('h1_count',0)})")
    if s.get("links_broken", 0) > 0:
        weak.append(f"{s['links_broken']} enlace(s) roto(s)")
    mob = (r.get("psi_full") or {}).get("mobile") or {}
    if mob.get("performance") is not None and mob["performance"] < 60:
        weak.append(f"el movil es lento ({mob['performance']}/100)")
    an = s.get("analytics") or {}
    if not an.get("has_any"):
        weak.append("no detectamos analitica")
    weak_txt = ("; ".join(weak[:4]) + ".") if weak else "no encontramos fallos graves; toca pulir y consolidar."
    return (f'<div class="block callout {"g" if named[worst] >= 60 else "o"}">'
            f'<b>En concreto para {r.get("domain","tu web")}:</b> tu punto mas fuerte es <b>{best}</b> '
            f'({named[best]}/100) y donde mas pierdes es <b>{worst}</b> ({named[worst]}/100). '
            f'Lo que hay que corregir: {weak_txt}</div>')


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

    # Titular a dos tonos segun nota
    if score >= 70:
        h1 = 'Buena base, <span class="o">con margen para ganar en Google y en la IA</span>'
    elif score >= 55:
        h1 = 'Vas por buen camino, <span class="o">pero la IA todavia no te prioriza</span>'
    elif score >= 40:
        h1 = 'Tu web funciona, <span class="o">pero Google y la IA te dejan fuera</span>'
    else:
        h1 = 'Ahora mismo <span class="o">Google y la IA apenas te ven</span>'

    # Facts de portada
    ai_fact = ""
    if ai.get("available") and not ai.get("error"):
        ai_fact = (f'<div class="f"><div class="n {"c" if ai.get("knows_brand") else "r"}">{"Si" if ai.get("knows_brand") else "No"}</div>'
                   f'<div class="l">la IA {"reconoce" if ai.get("knows_brand") else "no reconoce"} tu marca al preguntarle directamente</div></div>')
    else:
        gs = cats.get("geo", {}).get("score", 0)
        ai_fact = f'<div class="f"><div class="n {"c" if gs>=55 else "o"}">{gs}</div><div class="l">preparacion para la IA (GEO) sobre 100</div></div>'

    facts = f"""
      <div class="f"><div class="n {"c" if score>=70 else "o" if score>=40 else "r"}">{score}</div><div class="l">salud digital global, verificada en vivo</div></div>
      {ai_fact}
      <div class="f"><div class="n {"r" if s["links_broken"]>0 else ""}">{s["links_broken"]}</div><div class="l">enlaces rotos (404) en la muestra revisada</div></div>
      <div class="f"><div class="n {"o" if s["home_time"]>=3 else "c"}">{s["home_time"]}s</div><div class="l">tiempo de respuesta del servidor</div></div>"""

    # Banda de veredicto
    if ai.get("available") and not ai.get("error"):
        band_b = "Marca " + ("reconocida" if ai.get("knows_brand") else "invisible") + " para la IA."
        band_p = ("Le preguntamos directamente a la IA: " +
                  ("te reconoce" if ai.get("knows_brand") else "no tiene informacion fiable de ti") + " y " +
                  ("te recomienda en tu sector." if ai.get("recommended") else "recomienda a otras empresas de tu sector, no a la tuya.") +
                  " En paralelo revisamos tu web tecnica y on-page, pagina a pagina.")
    else:
        band_b = "Tu visibilidad no se decide solo en Google: ahora tambien en la IA."
        band_p = ("Revisamos en vivo tu salud tecnica, tu on-page y tu preparacion para los buscadores con IA. "
                  "Lo que sigue es el detalle, comprobado sin accesos, y el plan para mejorar donde mas pesa.")

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
    .eyebrow{{font-family:"JetBrains Mono",monospace;font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:{CY6};margin:0 0 6px;display:flex;align-items:center;gap:8px}}
    .eyebrow .bar{{width:22px;height:2px;background:{OR};display:inline-block}}
    h2.sec{{font-family:"Sora",sans-serif;font-weight:800;font-size:20px;letter-spacing:-.01em;color:{INK9};line-height:1.14;margin:0 0 4px}}
    .sub{{font-size:11px;color:#7b8694;margin-bottom:13px}}
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
      <div class="s"><div class="n {'g' if cats.get('tecnico',{}).get('score',0)>=70 else 'o'}">{cats.get('tecnico',{}).get('score',0)}/100</div><div class="l">salud tecnica: HTTPS, velocidad, robots, sitemap, 404</div></div>
      <div class="s"><div class="n {'g' if cats.get('onpage',{}).get('score',0)>=70 else 'o'}">{cats.get('onpage',{}).get('score',0)}/100</div><div class="l">SEO on-page: titulos, descripciones, H1, schema</div></div>
      <div class="s"><div class="n {'c' if cats.get('geo',{}).get('score',0)>=55 else 'o'}">{cats.get('geo',{}).get('score',0)}/100</div><div class="l">preparacion para la IA (GEO / LLMO)</div></div>"""

    # Lede especifico con datos reales
    comps3 = _comp_names(ai, 3)
    ai_bit = ""
    if ai.get("available") and not ai.get("error") and ai.get("answered_names"):
        if ai.get("knows_brand") and ai.get("recommended") is False:
            ai_bit = (f" Le preguntamos a la <b>IA</b>: te describe, pero cuando alguien pide tu "
                      f"servicio nombra a {comps3}, no a ti.")
        elif not ai.get("knows_brand"):
            ai_bit = f" Le preguntamos a la <b>IA</b>: no sabe quien eres y recomienda a {comps3}."
        else:
            ai_bit = " Le preguntamos a la <b>IA</b>: te reconoce y te recomienda en tu sector."
    psi = r.get("psi_full") or {}
    mob = (psi.get("mobile") or {})
    speed_bit = f" Medimos la velocidad en movil y escritorio con PageSpeed (movil {mob.get('performance')}/100)." if mob.get("performance") is not None else ""
    n404 = s.get("links_broken", 0)
    tec_bit = (f" Rastreamos {s.get('links_checked',0)} direcciones una a una"
               + (f" y encontramos {n404} enlace(s) roto(s)." if n404 else " sin enlaces rotos."))
    lede = (f"Diagnostico con <b>datos reales comprobados en vivo</b>: revisamos tu web por dentro, "
            f"pagina a pagina.{tec_bit}{ai_bit}{speed_bit} Aqui tienes lo que encontramos y el plan para "
            f"que Google y la IA te encuentren y te recomienden.")

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><style>{css}</style></head><body>

<section class="cover">
  <div class="accent"></div>
  <div class="brand"><img src="{_logo_uri()}" alt="Cupperlab"><span class="cl">Diagnostico SEO &amp; GEO<b>{today}</b></span></div>
  <div class="ey">Como te ven Google y la IA hoy</div>
  <h1>{h1}<span class="dom">{dom}</span></h1>
  <p class="lede">{lede}</p>
  <div class="facts">{facts}</div>
  <div class="band"><b>{band_b}</b><p>{band_p}</p></div>
  <div class="msec">Que hemos analizado · en vivo</div>
  <div class="method4">
    <div class="m"><div class="no">01</div><b>Pruebas tecnicas</b><p>HTTPS, robots, sitemap, velocidad y enlaces rotos (404), uno a uno.</p></div>
    <div class="m"><div class="no">02</div><b>On-page</b><p>Titulos, descripciones, H1, canonical y vista previa al compartir.</p></div>
    <div class="m"><div class="no">03</div><b>Preparacion IA (GEO)</b><p>Datos estructurados, llms.txt, marca como entidad y estructura.</p></div>
    <div class="m"><div class="no">04</div><b>Consulta a la IA</b><p>Le preguntamos a la IA si te conoce y si te recomienda.</p></div>
  </div>
  <div class="foot"><span>Preparado por <b>Cupperlab</b> · {today} · Verificado en vivo</span><span>Confidencial</span></div>
</section>

<section class="pg">
  <div class="eyebrow"><span class="bar"></span>01 · Estado general</div>
  <h2 class="sec">Como esta tu web hoy</h2>
  <p class="sub"><b>Salud digital</b> = una nota 0-100 que resume que tan lista esta tu web para que Google te muestre y la IA te cite. La calculamos con lo verificado en vivo (tecnico 35%, on-page 35%, preparacion IA 30%). Es la valoracion de Cupperlab, no una metrica oficial de Google.</p>
  <div class="block scorewrap">
    <div class="gauge">{_gauge(score, 'Salud digital')}</div>
    <div class="levels">{_levels(r)}</div>
  </div>
  {_estado_resumen(r)}
  <div class="block stats3">{stats3}</div>
  {_severity_bars(r)}
</section>

<section class="pg">
  <div class="eyebrow"><span class="bar"></span>02 · Salud tecnica de tu web</div>
  <h2 class="sec">Que falla (y que funciona) por dentro</h2>
  <p class="sub">Lo tecnico que Google mira para decidir si te muestra: seguridad, respuesta del servidor, robots, mapa del sitio y enlaces rotos. En rojo lo que falla, en verde lo que ya funciona.</p>
  {_tech_rows(r)}
  {_robots_block(r)}
  {(''.join('<div class="block callout r"><b>Enlaces rotos.</b> Ejemplos reales encontrados: ' + ', '.join(e["url"] for e in s["broken_examples"][:3]) + '.</div>' for _ in [0]) if s.get("broken_examples") else '')}

  <div class="eyebrow" style="margin-top:16px"><span class="bar"></span>03 · SEO on-page</div>
  <h2 class="sec">Que le falta a tus paginas para posicionar</h2>
  <p class="sub">Primero lo que falla y hay que corregir; despues lo que ya esta bien. Son las senales que deciden si Google te muestra y si la IA te cita.</p>
  {_onpage_rows(r)}
  {_porque_como(r)}
</section>

<section class="pg">
  {_ai_section(r)}
  {_speed_section(r)}
</section>

{(f'''<section class="pg">
  {_google_section(r)}
</section>''') if _google_section(r) else ''}

<section class="pg">
  <div class="eyebrow"><span class="bar"></span>06 · Plan de accion</div>
  <h2 class="sec">Todo lo que hay que mejorar, por orden de impacto</h2>
  <p class="sub">La lista completa de lo que corregir para posicionar en Google y en la IA: base tecnica y robots primero (que puedan leerte), luego on-page y contenido, y por ultimo las senales para que la IA te reconozca y te recomiende.</p>
  {_priority_table(r)}
  {_que_esperamos(r)}
  <div class="closeband">
    <div class="l"><b>¿Damos el siguiente paso?</b><p>Ponemos en marcha este plan contigo: base tecnica, on-page, contenido y las senales que hacen que la IA te recomiende. Primera revision sin costo.</p></div>
    <div class="r">Tel <b>{contact.get('phone','')}</b><br>{contact.get('email','')}<br>Mejoramos tu rentabilidad.</div>
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
