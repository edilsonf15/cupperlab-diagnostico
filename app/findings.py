"""Fuente ÚNICA de los hallazgos del diagnóstico (los mismos que muestra la pantalla).

Porta fielmente las funciones JS de la plantilla (perf/onpage/geo/tech/schema/
security/local/content) a Python, para que el PDF (y el correo) muestren EXACTAMENTE
los mismos hallazgos que la página. compute(data, lang) devuelve una lista agrupada
[{key, ic, cat, items:[{sev, t, d}]}] en el mismo orden que la web.

REGLA DE NEGOCIO (cliente, repetida): los hallazgos describen el PROBLEMA y su IMPACTO,
nunca la solución. El "cómo se arregla" (301, instala SSL, minifica, etc.) lo damos
nosotros cuando nos contratan. Aquí solo se nombra el hecho y por qué importa.
"""
from __future__ import annotations

import re


def _P(lang):
    def P(es, en):
        return en if lang == "en" else es
    return P


def _shortu(u: str) -> str:
    s = re.sub(r"^https?://[^/]+", "", str(u or ""))
    return s or "/"


def _exurls(arr, key=None) -> str:
    out = []
    for x in (arr or [])[:3]:
        if isinstance(x, dict):
            v = x.get(key) if key else (x.get("url") or next(iter(x.values()), ""))
        else:
            v = x
        out.append(_shortu(v))
    return ", ".join(out)


def _rx(types, pattern) -> bool:
    return any(re.search(pattern, str(t or "").lower()) for t in (types or []))


# --------------------------------------------------------------------------- #
def perf_findings(r, P):
    pf = r.get("perf2")
    if not pf:
        return []
    M = pf.get("mobile") or {}
    mm = M.get("metrics") or {}
    A = M.get("audits") or {}
    inf = pf.get("infra") or {}
    D = pf.get("desktop") or {}
    dmx = D.get("metrics") or {}
    # Regla del cliente: si la velocidad GENERAL está bien en móvil Y escritorio (>=80),
    # NO ensuciamos los hallazgos con avisos de velocidad, aunque una submétrica (p.ej.
    # LCP) puntúe bajo. Las tarjetas de dimensión ya muestran el detalle fino.
    _ms = M.get("score")
    _ds = D.get("score")
    if (_ms is not None and _ms >= 80) and (_ds is not None and _ds >= 80):
        return []
    out = []

    def sv(s):
        return "critico" if (s is not None and s < 40) else "op"

    lcp = mm.get("lcp") or {}
    dlcp = dmx.get("lcp") or {}
    if lcp.get("value") is not None and (lcp.get("score") is None or lcp.get("score") < 80):
        lcpcmp = (P(" En escritorio va bien (" + str(dlcp.get("display")) + "), así que el problema está en cómo carga en móvil.",
                    " On desktop it's fine (" + str(dlcp.get("display")) + "), so the problem is how it loads on mobile.")
                  if (dlcp.get("value") is not None and (dlcp.get("score") or 0) >= 80) else "")
        out.append({"sev": sv(lcp.get("score")), "t": P("Carga lenta en móvil (LCP)", "Slow mobile load (LCP)"),
                    "d": P("Lo principal tarda " + str(lcp.get("display")) + " en verse en móvil (lo bueno es <2,5 s), y muchas visitas se van antes de que cargue." + lcpcmp,
                           "The main content takes " + str(lcp.get("display")) + " to appear on mobile (good is <2.5s), and many visitors leave before it loads." + lcpcmp)})
    cls = mm.get("cls") or {}
    if cls.get("value") is not None and (cls.get("score") or 0) < 80:
        out.append({"sev": sv(cls.get("score")), "t": P("El contenido salta al cargar (CLS)", "Content shifts while loading (CLS)"),
                    "d": P("El contenido se mueve mientras carga (" + str(cls.get("display")) + "), y eso hace que la gente pulse donde no quería y se frustre.",
                           "Content moves while loading (" + str(cls.get("display")) + "), so people tap the wrong thing and get frustrated.")})
    inp = mm.get("inp") or {}
    if inp.get("value") is not None and (inp.get("score") or 0) < 80:
        out.append({"sev": sv(inp.get("score")), "t": P("Respuesta lenta al tocar (INP)", "Slow tap response (INP)"),
                    "d": P("La página tarda en reaccionar al tocar o hacer clic (" + str(inp.get("display")) + ") y se siente pesada en el móvil.",
                           "The page is slow to react to taps or clicks (" + str(inp.get("display")) + ") and feels sluggish on mobile.")})
    ttfb = mm.get("ttfb") or {}
    if ttfb.get("value") is not None and (ttfb.get("score") or 0) < 80:
        out.append({"sev": sv(ttfb.get("score")), "t": P("Servidor lento en responder (TTFB)", "Slow server response (TTFB)"),
                    "d": P("Tu servidor tarda " + str(ttfb.get("display")) + " en responder, y ese retraso arrastra a toda la carga de la página.",
                           "Your server takes " + str(ttfb.get("display")) + " to respond, and that delay drags down the whole page load.")})
    imgkb = ((A.get("images_modern") or {}).get("savings_kb") or 0) + ((A.get("images_sized") or {}).get("savings_kb") or 0)
    if imgkb > 0:
        out.append({"sev": "op", "t": P("Imágenes sin optimizar", "Unoptimized images"),
                    "d": P("Tus imágenes pesan unos " + str(imgkb) + " KB de más para lo que muestran. Ese peso extra ralentiza la carga, sobre todo en móvil.",
                           "Your images carry about " + str(imgkb) + " KB more than needed for what they show. That extra weight slows the load, especially on mobile.")})
    mjs = A.get("minify_js") or {}
    mcss = A.get("minify_css") or {}
    mkb = (mjs.get("savings_kb") or 0) + (mcss.get("savings_kb") or 0)
    mms = (mjs.get("savings_ms") or 0) + (mcss.get("savings_ms") or 0)
    if mkb > 0:
        mdet = ((P("JavaScript (−" + str(mjs.get("savings_kb")) + " KB)", "JavaScript (−" + str(mjs.get("savings_kb")) + " KB)") if (mjs.get("savings_kb") or 0) > 0 else "")
                + ((P(" y ", "and ") + "") if ((mjs.get("savings_kb") or 0) > 0 and (mcss.get("savings_kb") or 0) > 0) else "")
                + (P("CSS (−" + str(mcss.get("savings_kb")) + " KB)", "CSS (−" + str(mcss.get("savings_kb")) + " KB)") if (mcss.get("savings_kb") or 0) > 0 else ""))
        mmsx = P(" Son ~" + str(mms) + " ms de procesado extra en cada visita.", " That's ~" + str(mms) + " ms of extra processing on every visit.") if mms > 0 else ""
        out.append({"sev": "op", "t": P("CSS y JavaScript sin minificar", "Unminified CSS and JavaScript"),
                    "d": P("Hay unos " + str(mkb) + " KB de código sin minificar: " + mdet + "." + mmsx,
                           "There are about " + str(mkb) + " KB of unminified code: " + mdet + "." + mmsx)})
    lz = A.get("lazy") or {}
    if (lz.get("savings_kb") or 0) > 0:
        out.append({"sev": "op", "t": P("Imágenes sin carga diferida", "Images without lazy-load"),
                    "d": P("Hay imágenes fuera de pantalla que se cargan de golpe (~" + str(lz.get("savings_kb")) + " KB) aunque el visitante todavía no las ve, y retrasan la primera vista.",
                           "Off-screen images load upfront (~" + str(lz.get("savings_kb")) + " KB) even though the visitor can't see them yet, delaying the first view.")})
    tp = A.get("third_party") or {}
    if (tp.get("block_ms") or 0) > 250 or (tp.get("count") or 0) > 8:
        out.append({"sev": "op", "t": P("Exceso de scripts de terceros", "Too many third-party scripts"),
                    "d": P("Los scripts externos (analítica, chats, píxeles) bloquean el navegador unos " + str(tp.get("block_ms")) + " ms mientras carga tu web.",
                           "External scripts (analytics, chats, pixels) block the browser about " + str(tp.get("block_ms")) + " ms while your site loads.")})
    if not (inf.get("brotli") or inf.get("gzip")):
        out.append({"sev": "op", "t": P("Sin compresión (gzip/brotli)", "No compression (gzip/brotli)"),
                    "d": P("Tu servidor no comprime el texto al enviarlo, así que el HTML, el CSS y el JS viajan más pesados de lo necesario.",
                           "Your server doesn't compress text on delivery, so your HTML, CSS and JS travel heavier than they need to.")})
    if not inf.get("cdn"):
        out.append({"sev": "op", "t": P("Sin CDN", "No CDN"),
                    "d": P("No detectamos un CDN. Sin él, tu web se sirve desde un único servidor y llega más lenta a los visitantes que están lejos de él.",
                           "No CDN detected. Without one, your site is served from a single server and reaches distant visitors more slowly.")})
    return out


def _pg(n, P):
    return str(n) + " " + (P("página", "page") if n == 1 else P("páginas", "pages"))


def onpage_findings(r, P):
    op = r.get("onpage")
    if not op or not op.get("issues"):
        return []
    i = op["issues"]
    out = []

    def c(k):
        return (i.get(k) or {}).get("count") or 0

    if c("noindex") > 0:
        out.append({"sev": "critico", "t": P("Páginas que Google no puede indexar", "Pages Google can't index"),
                    "d": P(str(c("noindex")) + " página(s) tienen 'noindex' y no aparecerán en Google: " + _exurls(i["noindex"].get("examples")) + ". Puede ser un error de configuración que te está dejando fuera.",
                           str(c("noindex")) + " page(s) have 'noindex' and won't show on Google: " + _exurls(i["noindex"].get("examples")) + ". It may be a configuration mistake leaving you out.")})
    if c("title_missing") > 0:
        out.append({"sev": "critico", "t": P("Páginas sin título", "Pages with no title"),
                    "d": P(str(c("title_missing")) + " página(s) no tienen etiqueta <title>: " + _exurls(i["title_missing"].get("examples")) + ". El título es lo primero que lee Google y el usuario en los resultados.",
                           str(c("title_missing")) + " page(s) have no <title>: " + _exurls(i["title_missing"].get("examples")) + ". The title is the first thing Google and users read in results.")})
    if c("h1_missing") > 0:
        out.append({"sev": "op", "t": P("Páginas sin titular principal (H1)", "Pages with no main heading (H1)"),
                    "d": P(str(c("h1_missing")) + " página(s) no tienen H1: " + _exurls(i["h1_missing"].get("examples")) + ". El H1 le dice a Google de qué trata la página.",
                           str(c("h1_missing")) + " page(s) have no H1: " + _exurls(i["h1_missing"].get("examples")) + ". The H1 tells Google what the page is about.")})
    ina = i.get("img_no_alt") or {}
    if (ina.get("total") or 0) > 0:
        out.append({"sev": "op", "t": P("Imágenes sin texto ALT", "Images without ALT text"),
                    "d": P("Encontramos " + str(ina.get("total")) + " de " + str(ina.get("total_imgs")) + " imágenes sin texto alternativo (p. ej. " + _exurls(ina.get("examples"), "url") + "). El ALT es lo que Google usa para entender la imagen y lo que necesita quien navega sin ver.",
                           "We found " + str(ina.get("total")) + " of " + str(ina.get("total_imgs")) + " images without alt text (e.g. " + _exurls(ina.get("examples"), "url") + "). ALT is how Google understands the image and what people who can't see it rely on.")})
    if c("desc_missing") > 0:
        out.append({"sev": "op", "t": P("Páginas sin meta descripción", "Pages with no meta description"),
                    "d": P(str(c("desc_missing")) + " página(s) no tienen meta descripción: " + _exurls(i["desc_missing"].get("examples")) + ". Es el texto que Google muestra bajo el título y el que decide muchos clics.",
                           str(c("desc_missing")) + " page(s) have no meta description: " + _exurls(i["desc_missing"].get("examples")) + ". It's the snippet Google shows under the title and it drives many clicks.")})
    if c("title_dup") > 0:
        out.append({"sev": "op", "t": P("Títulos repetidos entre páginas", "Duplicate titles across pages"),
                    "d": P(str(c("title_dup")) + " grupo(s) de páginas comparten el mismo título. Google no sabe cuál priorizar y acaban compitiendo entre sí.",
                           str(c("title_dup")) + " group(s) of pages share the same title. Google can't tell which to prioritize and they end up competing.")})
    if c("desc_dup") > 0:
        out.append({"sev": "op", "t": P("Descripciones repetidas", "Duplicate meta descriptions"),
                    "d": P(str(c("desc_dup")) + " grupo(s) de páginas usan la misma meta descripción, así que en los resultados se ven iguales y pierden fuerza.",
                           str(c("desc_dup")) + " group(s) of pages reuse the same meta description, so they look identical in results and lose impact.")})
    if c("og_missing") > 0:
        out.append({"sev": "op", "t": P("Sin vista previa al compartir (Open Graph)", "No social preview (Open Graph)"),
                    "d": P(str(c("og_missing")) + " página(s) no tienen imagen/título Open Graph: " + _exurls(i["og_missing"].get("examples")) + ". Al compartir el enlace en WhatsApp o redes se ve pobre y recibe menos clics.",
                           str(c("og_missing")) + " page(s) have no Open Graph image/title: " + _exurls(i["og_missing"].get("examples")) + ". Links shared on WhatsApp or social look poor and get fewer clicks.")})
    if c("h2_missing") > 0:
        out.append({"sev": "op", "t": P("Páginas sin subtítulos (H2)", "Pages with no subheadings (H2)"),
                    "d": P(str(c("h2_missing")) + " página(s) no usan encabezados H2: " + _exurls(i["h2_missing"].get("examples")) + ". Los H2 ordenan el contenido; sin ellos Google y la IA entienden peor la estructura.",
                           str(c("h2_missing")) + " page(s) use no H2 headings: " + _exurls(i["h2_missing"].get("examples")) + ". H2s organize content; without them Google and AI understand the structure worse.")})
    if c("title_bad_len") > 0:
        out.append({"sev": "op", "t": P("Títulos demasiado cortos o largos", "Titles too short or long"),
                    "d": P(str(c("title_bad_len")) + " título(s) están fuera del rango ideal (25-65 car.), así que Google puede recortarlos o restarles fuerza: " + _exurls(i["title_bad_len"].get("examples"), "url") + ".",
                           str(c("title_bad_len")) + " title(s) are outside the ideal range (25-65 chars), so Google may trim them or weight them less: " + _exurls(i["title_bad_len"].get("examples"), "url") + ".")})
    if c("thin") > 0:
        out.append({"sev": "op", "t": P("Páginas con poco contenido", "Thin content pages"),
                    "d": P(str(c("thin")) + " página(s) tienen poco texto (p. ej. " + _exurls(i["thin"].get("examples"), "url") + "). Con poco contenido, Google y la IA tienen menos para entenderte y citarte.",
                           str(c("thin")) + " page(s) have little text (e.g. " + _exurls(i["thin"].get("examples"), "url") + "). With thin content, Google and AI have less to understand and cite you.")})
    if c("canonical_missing") > 0:
        out.append({"sev": "op", "t": P("Falta la etiqueta canonical", "Missing canonical tag"),
                    "d": P(str(c("canonical_missing")) + " página(s) no declaran su URL canónica: " + _exurls(i["canonical_missing"].get("examples")) + ". Sin ella Google puede tratar como duplicadas varias URLs de la misma página.",
                           str(c("canonical_missing")) + " page(s) don't declare a canonical URL: " + _exurls(i["canonical_missing"].get("examples")) + ". Without it Google may treat several URLs of the same page as duplicates.")})
    if c("url_unfriendly") > 0:
        out.append({"sev": "op", "t": P("URLs poco amigables", "Unfriendly URLs"),
                    "d": P(str(c("url_unfriendly")) + " URL(s) no son ideales (largas, con parámetros o mayúsculas): " + _exurls(i["url_unfriendly"].get("examples"), "url") + ". Las URLs limpias posicionan y se comparten mejor.",
                           str(c("url_unfriendly")) + " URL(s) aren't ideal (long, with params or uppercase): " + _exurls(i["url_unfriendly"].get("examples"), "url") + ". Clean URLs rank and share better.")})
    if c("orphans") > 0:
        out.append({"sev": "op", "t": P("Páginas huérfanas (sin enlaces internos)", "Orphan pages (no internal links)"),
                    "d": P(_pg(c("orphans"), P) + " no reciben ningún enlace interno: " + _exurls(i["orphans"].get("examples")) + ". Google y los visitantes casi no llegan a ellas.",
                           _pg(c("orphans"), P) + " receive no internal links: " + _exurls(i["orphans"].get("examples")) + ". Google and visitors barely reach them.")})
    if c("deep") > 0:
        out.append({"sev": "op", "t": P("Páginas demasiado profundas (más de 3 clics)", "Pages too deep (more than 3 clicks)"),
                    "d": P(_pg(c("deep"), P) + " están a más de 3 clics del inicio: " + _exurls(i["deep"].get("examples"), "url") + ". Cuanto más escondida, menos las prioriza Google.",
                           _pg(c("deep"), P) + " are more than 3 clicks from the home: " + _exurls(i["deep"].get("examples"), "url") + ". The deeper they sit, the less Google prioritizes them.")})
    bc = i.get("breadcrumbs") or {}
    if bc.get("present") is False:
        out.append({"sev": "op", "t": P("Sin migas de pan (breadcrumbs)", "No breadcrumbs"),
                    "d": P("Tu sitio no usa breadcrumbs, la ruta de navegación que ayuda a Google a entender la jerarquía de páginas y que puede salir en los resultados de búsqueda.",
                           "Your site has no breadcrumbs, the navigation trail that helps Google understand your page hierarchy and can appear in search results.")})
    if c("poor_anchor") > 0:
        out.append({"sev": "op", "t": P("Enlaces con texto poco descriptivo", "Links with vague anchor text"),
                    "d": P(str(c("poor_anchor")) + " enlace(s) usan textos genéricos como 'aquí' o 'leer más', que no le dicen a Google (ni al usuario) a dónde llevan.",
                           str(c("poor_anchor")) + " link(s) use generic text like 'here' or 'read more', which don't tell Google (or users) where they lead.")})
    return out


def geo_findings(r, P):
    ai = r.get("geo_ai") or {}
    sig = r.get("signals") or {}
    meta = r.get("meta") or {}
    out = []
    if ai.get("limited"):
        return [{"sev": "op", "t": P("Consulta a la IA no completada", "AI query not completed"),
                 "d": P("En este análisis no pudimos completar la consulta en vivo a la IA (límite temporal del servicio). No significa que la IA no te conozca; se reintenta. El resto del diagnóstico es completo.",
                        "We couldn't complete the live AI query in this analysis (a temporary service limit). It does not mean AI doesn't know you; we'll retry. The rest of the diagnosis is complete.")}]
    if not ai.get("available") or ai.get("status") in ("failed", "skipped"):
        return out
    rec = ai.get("recognition")
    comp = [c.get("name") for c in (ai.get("competitors") or [])
            if isinstance(c, dict) and c.get("name") and c.get("source") != "google"][:3]
    # v2: qué motores respondieron y qué preguntó el cliente (transparencia de la medición)
    _eng = ai.get("answered_names") or []
    _qs = [q.get("q") for q in (ai.get("questions") or []) if q.get("q")]
    if _eng and _qs:
        out.append({"sev": "op", "t": P("Cómo lo medimos", "How we measured it"),
                    "d": P("Preguntamos a " + " y ".join(_eng) + " como lo haría un cliente, sin nombrarte: " +
                           " · ".join("“" + q + "”" for q in _qs[:3]) + ".",
                           "We asked " + " and ".join(_eng) + " the way a customer would, without naming you: " +
                           " · ".join("“" + q + "”" for q in _qs[:3]) + ".")})
    if rec == "none":
        out.append({"sev": "critico", "t": P("La IA no sabe quién eres", "AI doesn't know who you are"),
                    "d": P("Le preguntamos por tu marca (con búsqueda web) y no te reconoce. Cada vez más clientes preguntan a la IA antes de decidir, y hoy no apareces.",
                           "We asked about your brand (with web search) and it doesn't recognize you. More and more customers ask AI before deciding, and today you don't appear.")})
    elif rec == "weak":
        out.append({"sev": "op", "t": P("La IA solo te encuentra si le das tu web", "AI only finds you if you hand it your site"),
                    "d": P("Por su cuenta no te reconoce; solo te describe si visita tu dominio. Te falta autoridad de marca para que la IA te tenga 'de memoria'.",
                           "On its own it doesn't recognize you; it only describes you if it visits your domain. You lack brand authority for AI to know you 'by heart'.")})
    mh = ai.get("reco_hits")
    mt = ai.get("reco_total")
    measured = isinstance(mh, int) and isinstance(mt, int) and mt > 0
    mce = (" " + P("Lo medimos con " + str(mt) + " búsquedas reales de cliente (sin nombrarte): apareces en " + str(mh) + " de " + str(mt) + ".",
                   "We measured it with " + str(mt) + " real customer searches (without naming you): you appear in " + str(mh) + " of " + str(mt) + ".")) if measured else ""
    if ai.get("recommended") is False:
        out.append({"sev": "critico", "t": P("No apareces cuando piden tu servicio", "You don't appear when your service is requested"),
                    "d": P("Cuando un cliente pide tu tipo de servicio sin nombrarte, la IA nombra a otros antes que a ti" + (": " + ", ".join(comp) if comp else "") + "." + mce + " Pierdes a los clientes que aún no te conocen.",
                           "When a customer asks for your type of service without naming you, AI names others before you" + (": " + ", ".join(comp) if comp else "") + "." + mce + " You lose customers who don't know you yet.")})
    elif ai.get("recommended") is None and rec != "none":
        out.append({"sev": "op", "t": P("Apareces de forma inconsistente", "You appear inconsistently"),
                    "d": P("Cuando piden tu servicio, a veces sales y a veces no." + mce + " Esa irregularidad hace que dependas de la suerte en cada consulta.",
                           "When your service is requested, sometimes you show up and sometimes not." + mce + " That inconsistency leaves each query up to chance.")})
    srcs = ai.get("sources") or []
    ext = [s for s in srcs if not s.get("own")]
    if len(ext) > 0:
        out.append({"sev": "op", "t": P("Fuentes con las que la IA te cita", "Sources AI cites you from"),
                    "d": P("La IA se apoyó en: " + ", ".join(s.get("domain", "") for s in ext[:5]) + ". Son las fuentes externas que hoy te dan autoridad ante la IA.",
                           "AI relied on: " + ", ".join(s.get("domain", "") for s in ext[:5]) + ". These are the external sources that give you authority with AI today.")})
    else:
        out.append({"sev": "op", "t": P("La IA no cita fuentes que hablen de ti", "AI cites no sources about you"),
                    "d": P("Al describirte, la IA no se apoya en ninguna fuente externa (prensa, directorios, reseñas). Esas menciones son las que dan autoridad ante la IA, y hoy no las tienes.",
                           "When describing you, AI relies on no external sources (press, directories, reviews). Those mentions are what give authority with AI, and today you have none.")})
    lq = sig.get("llms_quality")
    if lq == "none":
        out.append({"sev": "op", "t": P("Falta el llms.txt (guía para la IA)", "Missing llms.txt (guide for AI)"),
                    "d": P("Es el archivo que le indica a los buscadores con IA qué páginas tuyas son clave y cómo entenderte. Hoy no lo tienes, así que la IA va a ciegas por tu sitio.",
                           "It's the file that tells AI search engines which of your pages are key and how to understand you. Today you don't have it, so AI navigates your site blind.")})
    elif lq == "thin":
        out.append({"sev": "op", "t": P("Tu llms.txt está incompleto", "Your llms.txt is thin"),
                    "d": P("Tienes llms.txt, pero le faltan secciones y enlaces a tus páginas clave, así que guía poco a la IA.",
                           "You have llms.txt, but it lacks sections and links to your key pages, so it barely guides AI.")})
    sch = meta.get("schema_types") or []
    if not (meta.get("has_faq") or _rx(sch, r"faqpage|qapage")):
        out.append({"sev": "op", "t": P("Falta contenido citable (FAQ)", "Missing citable content (FAQ)"),
                    "d": P("La IA cita respuestas cortas y claras. Sin una sección de preguntas frecuentes con datos estructurados, tiene menos respuestas directas que tomar de tu web.",
                           "AI quotes short, clear answers. Without an FAQ section with structured data, it has fewer direct answers to take from your site.")})
    org = _rx(sch, r"organization|localbusiness|professionalservice")
    if not (org and meta.get("has_sameas")):
        out.append({"sev": "op", "t": P("Tu marca no está definida como entidad", "Your brand isn't defined as an entity"),
                    "d": P("Para que la IA te reconozca 'de memoria' necesita ver tu marca declarada como entidad, con tus perfiles oficiales enlazados. Hoy " + (P("tienes schema pero sin perfiles enlazados", "you have schema but no linked profiles") if org else P("no declaras entidad de marca", "you don't declare a brand entity")) + ", así que le falta esa señal.",
                           "For AI to recognize you 'by heart' it needs to see your brand declared as an entity, with your official profiles linked. Today " + (P("tienes schema pero sin perfiles enlazados", "you have schema but no linked profiles") if org else P("no declaras entidad de marca", "you don't declare a brand entity")) + ", so it lacks that signal.")})
    return out


def tech_findings(r, P):
    sig = r.get("signals") or {}
    ix = r.get("indexation") or {}
    meta = r.get("meta") or {}
    out = []
    host = re.sub(r"^www\.", "", (r.get("domain") or "").split("/")[0])
    w = sig.get("www") or {}
    if w.get("duplicate"):
        out.append({"sev": "critico", "t": P("Tu web abre con www y sin www a la vez", "Your site opens with and without www at once"),
                    "d": P("Tanto " + host + " como www." + host + " responden por separado, así que Google ve DOS webs iguales y reparte tu fuerza entre las dos (contenido duplicado).",
                           "Both " + host + " and www." + host + " respond separately, so Google sees TWO identical sites and splits your strength between them (duplicate content).")})
    elif w.get("one_fails") and w.get("broken_host"):
        out.append({"sev": "op", "t": P("Una versión (www / no-www) da error", "One version (www / non-www) errors out"),
                    "d": P(str(w.get("broken_host")) + " da error en vez de redirigir a la versión buena. Si alguien la enlaza o la escribe, se encuentra un fallo.",
                           str(w.get("broken_host")) + " returns an error instead of redirecting to the good version. If someone links or types it, they hit a failure.")})
    pages = sig.get("pages_found") or 0
    _est_idx = ix.get("indexed_estimate")  # nº REAL de Google (int) o None si solo hay muestra
    if sig.get("https") is False:
        out.append({"sev": "critico", "t": P("Tu web no tiene conexión segura (HTTPS)", "Your site has no secure connection (HTTPS)"),
                    "d": P("Los navegadores la marcan como 'no segura' y Google la penaliza. Es de los primeros motivos por los que un visitante se va sin comprar.",
                           "Browsers flag it as 'not secure' and Google penalizes it. It's one of the first reasons a visitor leaves without buying.")})
    elif sig.get("https_forced") is False:
        out.append({"sev": "op", "t": P("No fuerzas HTTPS", "You don't force HTTPS"),
                    "d": P("Tu web abre en HTTPS pero no redirige http a https, así que se dividen las señales y algunos visitantes navegan por la versión insegura.",
                           "Your site opens on HTTPS but doesn't redirect http to https, so signals split and some visitors browse the insecure version.")})
    brkidx = ix.get("broken_indexed") or []
    if brkidx:
        out.append({"sev": "critico", "t": P("Páginas rotas indexadas en Google", "Broken pages indexed on Google"),
                    "d": P("Google tiene indexadas " + str(len(brkidx)) + " página(s) que ya dan error 404 (p. ej. " + ", ".join(_shortu(b.get("url")) for b in brkidx[:2]) + "). Restan confianza y desperdician el rastreo de Google.",
                           "Google has " + str(len(brkidx)) + " indexed page(s) returning 404 (e.g. " + ", ".join(_shortu(b.get("url")) for b in brkidx[:2]) + "). They erode trust and waste Google's crawl budget.")})
    opb = (r.get("onpage") or {}).get("broken") or {}
    brkn = opb.get("count") if opb.get("count") is not None else (sig.get("links_broken") or 0)
    brkchecked = opb.get("checked") if opb.get("checked") is not None else (sig.get("links_checked") or 0)
    brkex = opb.get("broken") if opb.get("broken") else (sig.get("broken_examples") or [])
    if (brkn or 0) > 0:
        out.append({"sev": ("critico" if brkn >= 5 else "op"), "t": P("Enlaces rotos (404) en el sitio", "Broken links (404) on the site"),
                    "d": P("Comprobamos " + str(brkchecked) + " enlaces del sitio y " + str(brkn) + " dan error" + (" (p. ej. " + ", ".join(_shortu(b.get("url")) for b in brkex[:3]) + ")" if brkex else "") + ". Cada enlace roto frustra al visitante y desperdicia el rastreo de Google.",
                           "We checked " + str(brkchecked) + " links across the site and " + str(brkn) + " return an error" + (" (e.g. " + ", ".join(_shortu(b.get("url")) for b in brkex[:3]) + ")" if brkex else "") + ". Every broken link frustrates visitors and wastes Google's crawl budget.")})
    if not sig.get("sitemap"):
        out.append({"sev": "op", "t": P("Falta el mapa del sitio (sitemap.xml)", "Missing sitemap.xml"),
                    "d": P("El sitemap le dice a Google qué páginas rastrear. Sin él, Google tarda más en descubrir tu contenido y algunas páginas pueden quedarse sin indexar.",
                           "The sitemap tells Google which pages to crawl. Without it, Google is slower to discover your content and some pages may stay unindexed.")})
    else:
        comp = sig.get("sitemap_comp") or {}
        if ((comp.get("etiquetas") or 0) > (comp.get("paginas") or 0)) or ((comp.get("fichas") or 0) > (comp.get("paginas") or 0)):
            out.append({"sev": "op", "t": P("Tu sitemap está sucio", "Your sitemap is messy"),
                        "d": P("El sitemap manda a Google sobre todo a etiquetas o fichas (" + str(comp.get("etiquetas") or 0) + " etiquetas, " + str(comp.get("fichas") or 0) + " fichas) en vez de a tus páginas reales (" + str(comp.get("paginas") or 0) + "). Google gasta su rastreo en lo que no importa.",
                               "The sitemap points Google mostly to tags or files (" + str(comp.get("etiquetas") or 0) + " tags, " + str(comp.get("fichas") or 0) + " files) instead of your real pages (" + str(comp.get("paginas") or 0) + "). Google spends its crawl on what doesn't matter.")})
    if meta.get("robots_noindex"):
        out.append({"sev": "critico", "t": P("Tu web se está bloqueando a sí misma (noindex)", "Your site is blocking itself (noindex)"),
                    "d": P("La etiqueta meta robots de tu página dice 'noindex': le estás pidiendo a Google y a la IA que NO te muestren. Es lo más grave que puede tener una web para su visibilidad.",
                           "Your page's meta robots tag says 'noindex': you're telling Google and AI NOT to show you. It's the most serious visibility problem a site can have.")})
    rob = sig.get("robots_info") or {}
    if not sig.get("robots"):
        out.append({"sev": "op", "t": P("Falta robots.txt", "Missing robots.txt"),
                    "d": P("Es el archivo que guía a los rastreadores de Google y de la IA. Sin él, entran sin instrucciones sobre qué mirar y qué evitar.",
                           "It's the file that guides Google and AI crawlers. Without it, they enter with no instructions on what to look at and what to skip.")})
    elif rob.get("blocks_all"):
        out.append({"sev": "critico", "t": P("Tu robots.txt bloquea todo el sitio a Google", "Your robots.txt blocks the whole site from Google"),
                    "d": P("El robots.txt tiene 'Disallow: /' para todos los rastreadores: le estás pidiendo a Google que NO rastree nada. Con esto, ninguna página puede posicionarse.",
                           "robots.txt has 'Disallow: /' for all crawlers: you're telling Google NOT to crawl anything. With this, no page can rank.")})
    else:
        if rob.get("blocks_render"):
            out.append({"sev": "critico", "t": P("Bloqueas CSS/JS que Google necesita para ver tu web", "You block CSS/JS Google needs to render your site"),
                        "d": P("Tu robots.txt bloquea recursos como " + ", ".join(rob["blocks_render"][:3]) + ". Google necesita el CSS y el JavaScript para 'ver' la página como un usuario; al bloquearlos, la ve rota y te baja de posiciones.",
                               "Your robots.txt blocks resources like " + ", ".join(rob["blocks_render"][:3]) + ". Google needs CSS and JavaScript to 'see' the page like a user; blocking them makes it look broken and drops your rankings.")})
        if rob.get("blocks_content"):
            out.append({"sev": "op", "t": P("Tu robots.txt bloquea secciones de contenido", "Your robots.txt blocks content sections"),
                        "d": P("Estás impidiendo que Google rastree " + ", ".join(rob["blocks_content"][:3]) + ". Si ahí hay contenido que quieres posicionar, no aparecerá en Google.",
                               "You're preventing Google from crawling " + ", ".join(rob["blocks_content"][:3]) + ". If that's content you want to rank, it won't show on Google.")})
        if not sig.get("sitemap_in_robots") and sig.get("sitemap"):
            out.append({"sev": "op", "t": P("Tu robots.txt no declara el sitemap", "Your robots.txt doesn't declare the sitemap"),
                        "d": P("Tu robots.txt no menciona tu mapa del sitio, así que Google tarda más en encontrarlo.",
                               "Your robots.txt doesn't mention your sitemap, so Google takes longer to find it.")})
    # Solo avisamos de "poca cobertura" si tenemos el TOTAL REAL de Google (no una muestra topada).
    if isinstance(_est_idx, int) and _est_idx > 0 and pages > 0 and _est_idx < pages * 0.3:
        out.append({"sev": "op", "t": P("Poca cobertura de indexación", "Low indexing coverage"),
                    "d": P("Analizamos ~" + str(pages) + " páginas y en Google aparecen alrededor de " + str(_est_idx) + ". Se confirma con Search Console (Cobertura), pero apunta a que gran parte de tu web no está indexada.",
                           "We analyzed ~" + str(pages) + " pages and about " + str(_est_idx) + " show in Google. It's confirmed in Search Console (Coverage), but it points to much of your site being unindexed.")})
    if not meta.get("viewport"):
        out.append({"sev": "op", "t": P("No está preparada para móvil", "Not mobile-ready"),
                    "d": P("Falta la etiqueta viewport. Google indexa en modo móvil primero; sin ella tu web se ve mal en el teléfono y pierdes posiciones.",
                           "The viewport tag is missing. Google indexes mobile-first; without it your site looks bad on phones and you lose rankings.")})
    return out


def _schema_points(r):
    op = r.get("onpage") or {}
    meta = r.get("meta") or {}
    types = ((op.get("schema") or {}).get("types")) or meta.get("schema_types") or []
    have = ((op.get("schema") or {}).get("pages")) or (meta.get("schema_types") is not None)
    if not have:
        return None
    def has(p):
        return _rx(types, p)
    by = {
        "org": has(r"organization|localbusiness|professionalservice"),
        "website": has(r"website") and has(r"searchaction"),
        "prodserv": has(r"product|service|offer"),
        "review": has(r"review|aggregaterating|rating"),
    }
    return by


def schema_findings(r, P):
    by = _schema_points(r)
    if by is None:
        return []
    out = []
    if not by["org"]:
        out.append({"sev": "critico", "t": P("Falta el marcado de tu empresa (Organization)", "Missing company markup (Organization)"),
                    "d": P("Los datos estructurados son etiquetas invisibles en el código que le explican a Google y a la IA qué es tu negocio (nombre, logo, contacto). Sin la etiqueta de empresa (Organization), no te reconocen como una marca real y fiable.",
                           "Structured data are invisible tags in the code that tell Google and AI what your business is (name, logo, contact). Without the company tag (Organization), you're not recognized as a real, trustworthy brand.")})
    if not by["prodserv"]:
        out.append({"sev": "op", "t": P("No marcas tus productos o servicios", "Products/services not marked up"),
                    "d": P("No tienes las etiquetas invisibles que le dicen a Google y a la IA qué productos o servicios ofreces, así que no saben exactamente qué vendes cuando alguien lo busca.",
                           "You don't have the invisible tags that tell Google and AI which products or services you offer, so they don't know exactly what you sell when someone searches.")})
    if not by["review"]:
        # Solo hecho verificable: NO hay Schema Review/AggregateRating.
        out.append({"sev": "op", "t": P("Tus reseñas no aparecen como estrellas en Google", "Your reviews don't show as stars on Google"),
                    "d": P("No tienes la etiqueta invisible de valoraciones (Review). Es la que hace que Google pueda mostrar las estrellas doradas junto a tu web en los resultados, y la IA la usa como señal de confianza.",
                           "You don't have the invisible reviews tag (Review). It's what lets Google show the gold stars next to your site in results, and AI uses it as a trust signal.")})
    sch = (r.get("onpage") or {}).get("schema") or {}
    errs = sch.get("errors") or 0
    incompl = sch.get("incomplete") or []
    if errs > 0:
        out.append({"sev": "op", "t": P("Tus datos estructurados tienen errores y Google los ignora", "Your structured data has errors and Google ignores it"),
                    "d": P("Detectamos " + str(errs) + " bloque(s) de esas etiquetas invisibles mal escritas (no son código válido), así que Google no las lee y no te sirven de nada.",
                           "We found " + str(errs) + " block(s) of those invisible tags written incorrectly (not valid code), so Google can't read them and they're useless.")})
    if incompl:
        out.append({"sev": "op", "t": P("A tus etiquetas invisibles les falta información", "Your invisible tags are incomplete"),
                    "d": P("Algunas de tus etiquetas (" + ", ".join(str(x.get("type")) for x in incompl[:3]) + ") no traen todos los datos que Google pide, así que no consigues los resultados llamativos (estrellas, fotos, precios) que atraen clics.",
                           "Some of your tags (" + ", ".join(str(x.get("type")) for x in incompl[:3]) + ") don't carry all the data Google asks for, so you don't get the eye-catching results (stars, photos, prices) that attract clicks.")})
    return out


def security_findings(r, P):
    sig = r.get("signals") or {}
    sec = sig.get("security") or {}
    meta = r.get("meta") or {}
    out = []
    if sec.get("score") is None and sig.get("https") is None:
        return out
    ssl = sec.get("ssl") or {}
    mixed = sec.get("mixed") or []
    miss = sec.get("headers_missing") or []
    exp = sec.get("exposed") or []
    leaks = sec.get("leaks") or []
    ck = sec.get("cookie_flags") or []
    if exp:
        out.append({"sev": "critico", "t": P("Archivos sensibles expuestos", "Sensitive files exposed"),
                    "d": P("Detectamos " + str(len(exp)) + " recurso(s) accesibles que deberían estar protegidos (p. ej. " + ", ".join(str(e.get("path")) for e in exp[:2]) + "). Pueden filtrar datos internos o credenciales a cualquiera que los abra.",
                           "We found " + str(len(exp)) + " accessible resource(s) that should be protected (e.g. " + ", ".join(str(e.get("path")) for e in exp[:2]) + "). They can leak internal data or credentials to anyone who opens them.")})
    if ssl.get("valid") is False:
        out.append({"sev": "critico", "t": P("Tu certificado SSL no es válido", "Your SSL certificate is invalid"),
                    "d": P("El certificado está caducado, no coincide con el dominio o es autofirmado. Los navegadores muestran una alerta roja de 'sitio no seguro' que espanta a los visitantes.",
                           "The certificate is expired, doesn't match the domain or is self-signed. Browsers show a red 'not secure' warning that scares visitors away.")})
    elif ssl.get("valid") is True and ssl.get("days_left") is not None and ssl.get("days_left") < 15:
        out.append({"sev": "op", "t": P("Tu certificado SSL caduca pronto", "Your SSL certificate expires soon"),
                    "d": P("Le quedan " + str(ssl.get("days_left")) + " día(s). Si caduca, tu web pasa a mostrarse como 'no segura' de un día para otro.",
                           "It has " + str(ssl.get("days_left")) + " day(s) left. If it expires, your site starts showing as 'not secure' overnight.")})
    if sig.get("https") is False:
        out.append({"sev": "critico", "t": P("Tu web no usa HTTPS", "Your site doesn't use HTTPS"),
                    "d": P("Sin conexión segura, los navegadores la marcan como 'no segura' y Google la penaliza.",
                           "Without a secure connection, browsers flag it as 'not secure' and Google penalizes it.")})
    elif mixed:
        out.append({"sev": "op", "t": P("Cargas contenido inseguro (mixto)", "You load insecure (mixed) content"),
                    "d": P("Tu web es HTTPS pero carga " + str(len(mixed)) + " recurso(s) por http:// (p. ej. " + ", ".join(re.sub(r"^https?://", "", str(u))[:40] for u in mixed[:2]) + "). El navegador los bloquea o muestra el candado roto.",
                           "Your site is HTTPS but loads " + str(len(mixed)) + " resource(s) over http:// (e.g. " + ", ".join(re.sub(r"^https?://", "", str(u))[:40] for u in mixed[:2]) + "). The browser blocks them or shows a broken padlock.")})
    if miss:
        out.append({"sev": "op", "t": P("Faltan cabeceras de seguridad", "Missing security headers"),
                    "d": P("Te faltan " + str(len(miss)) + " cabecera(s) que protegen contra ataques comunes: " + ", ".join(str(x) for x in miss[:3]) + ". Sin ellas, tu web queda más expuesta.",
                           "You're missing " + str(len(miss)) + " header(s) that protect against common attacks: " + ", ".join(str(x) for x in miss[:3]) + ". Without them, your site is more exposed.")})
    if leaks:
        out.append({"sev": "op", "t": P("El servidor revela su versión", "The server reveals its version"),
                    "d": P("Tu servidor expone su tecnología y versión (" + ", ".join(str(x) for x in leaks[:2]) + "), lo que le facilita a un atacante buscar vulnerabilidades conocidas para esa versión.",
                           "Your server exposes its tech and version (" + ", ".join(str(x) for x in leaks[:2]) + "), which makes it easy for an attacker to look up known vulnerabilities for that version.")})
    if ck:
        out.append({"sev": "op", "t": P("Tus cookies no son seguras", "Your cookies aren't secure"),
                    "d": P("Detectamos " + ", ".join(str(x) for x in ck) + ". Sin los atributos Secure y HttpOnly, esas cookies son más fáciles de robar.",
                           "We found " + ", ".join(str(x) for x in ck) + ". Without the Secure and HttpOnly attributes, those cookies are easier to steal.")})
    if not meta.get("favicon"):
        out.append({"sev": "op", "t": P("Falta el favicon", "Missing favicon"),
                    "d": P("Es el iconito de la pestaña. Sin él tu web se ve menos profesional y confiable en el navegador y en los marcadores.",
                           "It's the little tab icon. Without it your site looks less professional and trustworthy in the browser and bookmarks.")})
    return out


def local_findings(r, P):
    ai = r.get("geo_ai") or {}
    meta = r.get("meta") or {}
    op = r.get("onpage") or {}
    out = []
    if not ai.get("available") and meta.get("has_address") is None and meta.get("has_phone") is None:
        return out
    types = ((op.get("schema") or {}).get("types")) or meta.get("schema_types") or []
    lb = _rx(types, r"localbusiness|professionalservice")
    rev = ai.get("gbp_reviews_n")  # None = no se pudo leer el nº (distinto de 0 reseñas)
    if ai.get("gbp") is False:
        out.append({"sev": "critico", "t": P("No encontramos tu ficha de Google Business", "We couldn't find your Google Business profile"),
                    "d": P("Sin ficha no apareces en el mapa ni en las búsquedas locales, y pierdes las reseñas que Google y la IA usan para recomendarte. Es de lo que más pesa en la visibilidad local.",
                           "Without a profile you don't appear on the map or in local searches, and you lose the reviews Google and AI use to recommend you. It's one of the biggest factors in local visibility.")})
    elif ai.get("gbp") is True and isinstance(rev, int) and rev > 0:
        _rt = ai.get("gbp_rating")
        out.append({"sev": "ok", "t": P("Ficha de Google verificada", "Google listing verified"),
                    "d": P("Según Google (Places API): ficha activa con " + str(rev) + " reseñas" + (", valoración " + str(_rt) + "/5" if _rt else "") + (", categoría “" + ai.get("gbp_category") + "”" if ai.get("gbp_category") else "") + ".",
                           "According to Google (Places API): active listing with " + str(rev) + " reviews" + (", rating " + str(_rt) + "/5" if _rt else "") + (", category “" + ai.get("gbp_category") + "”" if ai.get("gbp_category") else "") + ".")})
    elif ai.get("gbp") is None:
        out.append({"sev": "op", "t": P("Ficha de Google: no medida", "Google listing: not measured"),
                    "d": P("No pudimos consultar Google Maps en este análisis, así que no afirmamos si tienes ficha o no. Lo revisamos contigo en la sesión.",
                           "We couldn't query Google Maps in this analysis, so we make no claim about your listing. We'll review it with you in the session.")})
    elif ai.get("gbp") is True and rev == 0:
        out.append({"sev": "op", "t": P("Tu ficha de Google no tiene reseñas", "Your Google profile has no reviews"),
                    "d": P("Las reseñas son de lo que más miran los clientes y la IA para elegir, y hoy tu ficha no tiene ninguna.",
                           "Reviews are one of the things customers and AI look at most when choosing, and today your profile has none.")})
    if not (meta.get("has_address") and meta.get("has_phone")):
        out.append({"sev": "op", "t": P("Faltan datos de contacto claros en tu web (NAP)", "Missing clear contact details on your site (NAP)"),
                    "d": P("En tu web no mostramos " + (P("tu dirección", "your address") if not meta.get("has_address") else P("tu teléfono", "your phone")) + " de forma clara. El nombre, la dirección y el teléfono (NAP) deben aparecer iguales en la web y en tu ficha para que Google y la IA confíen en ti.",
                           "On your site we couldn't clearly find " + (P("your address", "your address") if not meta.get("has_address") else P("your phone", "your phone")) + ". Your name, address and phone (NAP) should appear the same on the site and your profile so Google and AI trust you.")})
    if not (meta.get("has_map") or meta.get("has_geo")):
        out.append({"sev": "op", "t": P("No incrustas un mapa en tu web", "No map embedded on your site"),
                    "d": P("Tu ubicación aparece en Google, pero en tu propia página de contacto no incrustas un mapa, una señal de negocio local que hoy no estás dando en tu sitio.",
                           "Your location shows on Google, but on your own contact page you don't embed a map, a local-business signal you're not giving on your site today.")})
    if not lb:
        out.append({"sev": "op", "t": P("Tu negocio no está marcado como local (LocalBusiness)", "Your business isn't marked as local (LocalBusiness)"),
                    "d": P("No declaras el Schema LocalBusiness (las etiquetas invisibles con dirección, teléfono, horario y ubicación), así que a Google y a la IA local les falta esa señal para situarte.",
                           "You don't declare LocalBusiness Schema (the invisible tags with address, phone, hours and location), so Google and local AI lack that signal to place you.")})
    if ai.get("gbp") is True and not ai.get("gbp_category"):
        out.append({"sev": "op", "t": P("La categoría de tu ficha no está bien definida", "Your profile's category isn't well set"),
                    "d": P("La categoría principal de tu ficha de Google decide en qué búsquedas apareces, y la tuya no está bien definida, así que dejas de salir en búsquedas que te corresponden.",
                           "Your Google profile's main category decides which searches you appear in, and yours isn't well set, so you miss searches you should be in.")})
    return out


def content_findings(r, P):
    c = (r.get("onpage") or {}).get("content")
    ca = r.get("content_ai")
    out = []
    if not c:
        return out
    dup = c.get("duplicates") or {}
    if (dup.get("count") or 0) > 0:
        ex = (dup.get("examples") or [{}])[0] or {}
        out.append({"sev": "op", "t": P("Páginas con contenido casi duplicado", "Near-duplicate content pages"),
                    "d": P("Detectamos " + str(dup.get("count")) + " par(es) de páginas con texto casi idéntico" + (" (p. ej. " + _shortu(ex.get("a")) + " ↔ " + _shortu(ex.get("b")) + ")" if ex.get("a") else "") + ". El contenido duplicado confunde a Google sobre cuál posicionar y las dos pierden fuerza.",
                           "We found " + str(dup.get("count")) + " pair(s) of pages with near-identical text" + (" (e.g. " + _shortu(ex.get("a")) + " ↔ " + _shortu(ex.get("b")) + ")" if ex.get("a") else "") + ". Duplicate content confuses Google about which to rank and both lose strength.")})
    dated = c.get("dated_pages") or 0
    fresh = c.get("fresh_pages") or 0
    fr = (fresh / dated) if dated else 0
    if dated == 0:
        out.append({"sev": "op", "t": P("No se ve cuándo actualizas el contenido", "No visible content dates"),
                    "d": P("Tus páginas no muestran fecha de publicación o actualización. Google y la IA valoran el contenido fresco, y sin fechas no pueden saber si el tuyo lo está.",
                           "Your pages show no publish/update date. Google and AI value fresh content, and without dates they can't tell if yours is.")})
    elif fr < 0.5:
        out.append({"sev": "op", "t": P("Buena parte del contenido está desactualizado", "Much of the content is outdated"),
                    "d": P("Solo " + str(fresh) + " de " + str(dated) + " páginas con fecha están actualizadas (lo más nuevo: " + str(c.get("newest") or "") + "). El contenido antiguo va perdiendo posiciones con el tiempo.",
                           "Only " + str(fresh) + " of " + str(dated) + " dated pages are recent (newest: " + str(c.get("newest") or "") + "). Stale content loses rankings over time.")})
    coh = c.get("coherence_pct")
    if coh is not None and coh < 70:
        out.append({"sev": "op", "t": P("Contenido poco enfocado en su tema", "Content not focused on its topic"),
                    "d": P("En el " + str(100 - coh) + "% de las páginas el título o el H1 no reflejan bien de qué trata el texto, así que Google duda de por qué tema posicionarlas.",
                           "On " + str(100 - coh) + "% of pages the title or H1 doesn't reflect the text well, so Google is unsure which topic to rank them for.")})
    if not c.get("about_page"):
        out.append({"sev": "op", "t": P("Falta una página 'Sobre nosotros'", "Missing an 'About' page"),
                    "d": P("No encontramos una página que cuente quién está detrás. Es una señal de confianza (E-E-A-T) que Google y la IA valoran para recomendarte.",
                           "We couldn't find a page telling who's behind the site. It's a trust signal (E-E-A-T) Google and AI value when recommending you.")})
    if not c.get("author"):
        out.append({"sev": "op", "t": P("Tus contenidos no muestran autor", "Your content shows no author"),
                    "d": P("Los artículos sin autor pierden autoridad (E-E-A-T): Google y la IA no saben quién respalda lo que dices.",
                           "Articles with no author lose authority (E-E-A-T): Google and AI don't know who stands behind what you say.")})
    kws = (ca.get("keywords") if ca else None) or ((r.get("geo_ai") or {}).get("keywords")) or []
    if kws:
        terms = " ".join(c.get("top_terms") or []).lower()
        cov = any(any(len(w) > 3 and w in terms for w in str(k).lower().split()) for k in kws)
        if not cov:
            out.append({"sev": "op", "t": P("Tu contenido no cubre bien tus palabras clave", "Content doesn't cover your keywords"),
                        "d": P("Por lo que ofreces, deberías posicionar por: " + ", ".join(kws[:4]) + ". Tu texto actual no trabaja esos términos de forma clara. (El volumen exacto de búsqueda se confirma con una herramienta de keywords).",
                               "For what you offer, you should rank for: " + ", ".join(kws[:4]) + ". Your current copy doesn't clearly target those terms. (Exact search volume needs a keyword tool).")})
        else:
            out.append({"sev": "op", "t": P("Palabras clave objetivo (según la IA)", "Target keywords (per AI)"),
                        "d": P("La IA identificó tus palabras clave: " + ", ".join(kws[:5]) + ". (El volumen exacto se confirma con una herramienta de keywords).",
                               "AI identified your keywords: " + ", ".join(kws[:5]) + ". (Exact volume needs a keyword tool).")})
    if (c.get("clusters") or 0) < 1:
        out.append({"sev": "op", "t": P("Falta estructura por temas (pilar + clusters)", "Missing topic structure (pillar + clusters)"),
                    "d": P("Tu contenido no está agrupado por temas con una página pilar y sus artículos relacionados enlazados entre sí. Esa estructura es de lo que más pesa para posicionar y para que la IA te entienda.",
                           "Your content isn't grouped into topics with a pillar page and related articles linked together. That structure is one of the biggest factors for ranking and for AI to understand you.")})
    if ca and ca.get("gaps"):
        out.append({"sev": "op", "t": P("Temas que la IA echa en falta", "Topics AI finds missing"),
                    "d": P("Al revisar tu contenido en vivo, la IA detectó vacíos de tema: " + "; ".join(ca["gaps"]) + ".",
                           "Reviewing your content live, AI spotted topic gaps: " + "; ".join(ca["gaps"]) + ".")})
    return out


def compute(data, lang="es"):
    """Devuelve los hallazgos agrupados por categoría, en el mismo orden que la web."""
    P = _P(lang)
    groups = [
        ("geo", "ai", P("Visibilidad en la IA (GEO)", "AI visibility (GEO)"), geo_findings),
        ("perf", "speed", P("Velocidad y rendimiento", "Speed & performance"), perf_findings),
        ("onpage", "seo", P("SEO on-page", "On-page SEO"), onpage_findings),
        ("content", "content", P("Contenido y relevancia", "Content & relevance"), content_findings),
        ("tech", "tech", P("Salud técnica e indexación", "Technical & indexing"), tech_findings),
        ("schema", "geo", P("Datos estructurados (Schema)", "Structured data (Schema)"), schema_findings),
        ("local", "geo", P("Presencia local y reputación", "Local presence & reputation"), local_findings),
        ("security", "shield", P("Seguridad", "Security"), security_findings),
    ]
    out = []
    for key, ic, cat, fn in groups:
        try:
            items = fn(data, P) or []
        except Exception:  # noqa: BLE001
            items = []
        if items:
            # críticos primero, como en la web
            items = sorted(items, key=lambda x: 0 if x.get("sev") == "critico" else 1)
            out.append({"key": key, "ic": ic, "cat": cat, "items": items})
    return out
