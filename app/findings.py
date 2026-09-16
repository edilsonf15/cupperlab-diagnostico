"""Fuente ÚNICA de los hallazgos del diagnóstico (los mismos que muestra la pantalla).

Porta fielmente las funciones JS de la plantilla (perf/onpage/geo/tech/schema/
security/local/content) a Python, para que el PDF (y el correo) muestren EXACTAMENTE
los mismos hallazgos que la página. compute(data, lang) devuelve una lista agrupada
[{key, ic, cat, items:[{sev, t, d}]}] en el mismo orden que la web.
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
        out.append({"sev": sv(lcp.get("score")), "t": P("Acelera la carga en móvil (LCP)", "Speed up mobile load (LCP)"),
                    "d": P("Ahora lo principal tarda " + str(lcp.get("display")) + " en verse en móvil (lo bueno es <2,5 s)." + lcpcmp + " Optimiza la imagen principal a WebP, precárgala (preload), sirve el tamaño correcto y reduce el JavaScript que bloquea el render.",
                           "The main content takes " + str(lcp.get("display")) + " on mobile (good is <2.5s)." + lcpcmp + " Optimize the hero image to WebP, preload it, serve the right size and cut render-blocking JavaScript.")})
    cls = mm.get("cls") or {}
    if cls.get("value") is not None and (cls.get("score") or 0) < 80:
        out.append({"sev": sv(cls.get("score")), "t": P("Evita que el contenido salte (CLS)", "Stop content from shifting (CLS)"),
                    "d": P("El contenido se mueve mientras carga (" + str(cls.get("display")) + "). Reserva el espacio de imágenes y anuncios con ancho y alto fijos, y evita insertar elementos por encima.",
                           "Content moves while loading (" + str(cls.get("display")) + "). Reserve space for images and ads with fixed width and height, and avoid inserting elements on top.")})
    inp = mm.get("inp") or {}
    if inp.get("value") is not None and (inp.get("score") or 0) < 80:
        out.append({"sev": sv(inp.get("score")), "t": P("Mejora la respuesta al tocar (INP)", "Improve tap responsiveness (INP)"),
                    "d": P("La página tarda en reaccionar al tocar o hacer clic (" + str(inp.get("display")) + "). Divide las tareas de JavaScript largas y quita los scripts de terceros que no sean críticos.",
                           "The page is slow to react to taps or clicks (" + str(inp.get("display")) + "). Break up long JavaScript tasks and remove non-critical third-party scripts.")})
    ttfb = mm.get("ttfb") or {}
    if ttfb.get("value") is not None and (ttfb.get("score") or 0) < 80:
        out.append({"sev": sv(ttfb.get("score")), "t": P("Reduce el tiempo de respuesta del servidor (TTFB)", "Cut server response time (TTFB)"),
                    "d": P("Tu servidor tarda " + str(ttfb.get("display")) + " en responder. Activa la caché de servidor, mejora el hosting o pon un CDN por delante.",
                           "Your server takes " + str(ttfb.get("display")) + " to respond. Enable server caching, upgrade hosting or put a CDN in front.")})
    imgkb = ((A.get("images_modern") or {}).get("savings_kb") or 0) + ((A.get("images_sized") or {}).get("savings_kb") or 0)
    if imgkb > 0:
        out.append({"sev": "op", "t": P("Comprime las imágenes (WebP/AVIF)", "Compress images (WebP/AVIF)"),
                    "d": P("Puedes ahorrar unos " + str(imgkb) + " KB pasando las imágenes a WebP/AVIF y sirviéndolas al tamaño correcto. Baja la carga y mejora el LCP.",
                           "You can save about " + str(imgkb) + " KB by converting images to WebP/AVIF and serving them at the right size. Cuts load and improves LCP.")})
    mjs = A.get("minify_js") or {}
    mcss = A.get("minify_css") or {}
    mkb = (mjs.get("savings_kb") or 0) + (mcss.get("savings_kb") or 0)
    mms = (mjs.get("savings_ms") or 0) + (mcss.get("savings_ms") or 0)
    if mkb > 0:
        mdet = ((P("JavaScript (−" + str(mjs.get("savings_kb")) + " KB)", "JavaScript (−" + str(mjs.get("savings_kb")) + " KB)") if (mjs.get("savings_kb") or 0) > 0 else "")
                + ((P(" y ", "and ") + "") if ((mjs.get("savings_kb") or 0) > 0 and (mcss.get("savings_kb") or 0) > 0) else "")
                + (P("CSS (−" + str(mcss.get("savings_kb")) + " KB)", "CSS (−" + str(mcss.get("savings_kb")) + " KB)") if (mcss.get("savings_kb") or 0) > 0 else ""))
        mmsx = P(" Puede ahorrar ~" + str(mms) + " ms de procesado.", " Can save ~" + str(mms) + " ms of processing.") if mms > 0 else ""
        out.append({"sev": "op", "t": P("Minifica CSS y JavaScript", "Minify CSS and JavaScript"),
                    "d": P("Hay unos " + str(mkb) + " KB de código sin minificar: " + mdet + "." + mmsx + " Minificar quita espacios y comentarios, y acelera la descarga y el procesado.",
                           "There are about " + str(mkb) + " KB of unminified code: " + mdet + "." + mmsx + " Minifying removes whitespace and comments, speeding download and parsing.")})
    lz = A.get("lazy") or {}
    if (lz.get("savings_kb") or 0) > 0:
        out.append({"sev": "op", "t": P("Activa la carga diferida de imágenes", "Enable image lazy-load"),
                    "d": P("Hay imágenes fuera de pantalla que se cargan de golpe (~" + str(lz.get("savings_kb")) + " KB). Cárgalas en diferido (loading=lazy) para que la primera vista sea más rápida.",
                           "Off-screen images load upfront (~" + str(lz.get("savings_kb")) + " KB). Lazy-load them (loading=lazy) so the first view is faster.")})
    tp = A.get("third_party") or {}
    if (tp.get("block_ms") or 0) > 250 or (tp.get("count") or 0) > 8:
        out.append({"sev": "op", "t": P("Reduce los scripts de terceros", "Reduce third-party scripts"),
                    "d": P("Los scripts externos bloquean el navegador unos " + str(tp.get("block_ms")) + " ms. Quita los que no uses y carga el resto en diferido (async o defer).",
                           "External scripts block the browser about " + str(tp.get("block_ms")) + " ms. Remove unused ones and defer the rest (async or defer).")})
    if not (inf.get("brotli") or inf.get("gzip")):
        out.append({"sev": "op", "t": P("Activa la compresión (gzip/brotli)", "Enable compression (gzip/brotli)"),
                    "d": P("Tu servidor no comprime el texto al enviarlo. Activar gzip o brotli reduce mucho el peso de HTML, CSS y JS.",
                           "Your server isn't compressing text on delivery. Enabling gzip or brotli greatly reduces HTML, CSS and JS weight.")})
    if not inf.get("cdn"):
        out.append({"sev": "op", "t": P("Sirve tu web desde un CDN", "Serve your site from a CDN"),
                    "d": P("No detectamos un CDN. Un CDN entrega tu web desde servidores cercanos a cada visitante: baja el TTFB y la hace más estable.",
                           "No CDN detected. A CDN delivers your site from servers near each visitor: it lowers TTFB and adds stability.")})
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
                    "d": P(str(c("noindex")) + " página(s) tienen 'noindex' y no aparecerán en Google: " + _exurls(i["noindex"].get("examples")) + ". Revisa que no sea un error.",
                           str(c("noindex")) + " page(s) have 'noindex' and won't show on Google: " + _exurls(i["noindex"].get("examples")) + ". Make sure it isn't a mistake.")})
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
                    "d": P("Encontramos " + str(ina.get("total")) + " de " + str(ina.get("total_imgs")) + " imágenes sin texto alternativo (p. ej. " + _exurls(ina.get("examples"), "url") + "). El ALT ayuda a Google a entender la imagen y mejora la accesibilidad.",
                           "We found " + str(ina.get("total")) + " of " + str(ina.get("total_imgs")) + " images without alt text (e.g. " + _exurls(ina.get("examples"), "url") + "). ALT helps Google understand the image and improves accessibility.")})
    if c("desc_missing") > 0:
        out.append({"sev": "op", "t": P("Páginas sin meta descripción", "Pages with no meta description"),
                    "d": P(str(c("desc_missing")) + " página(s) no tienen meta descripción: " + _exurls(i["desc_missing"].get("examples")) + ". Es el texto que Google muestra bajo el título y sube los clics.",
                           str(c("desc_missing")) + " page(s) have no meta description: " + _exurls(i["desc_missing"].get("examples")) + ". It's the snippet Google shows under the title and it lifts clicks.")})
    if c("title_dup") > 0:
        out.append({"sev": "op", "t": P("Títulos repetidos entre páginas", "Duplicate titles across pages"),
                    "d": P(str(c("title_dup")) + " grupo(s) de páginas comparten el mismo título. Google no sabe cuál priorizar y compiten entre sí. Dale a cada página un título único.",
                           str(c("title_dup")) + " group(s) of pages share the same title. Google can't tell which to prioritize and they compete. Give each page a unique title.")})
    if c("desc_dup") > 0:
        out.append({"sev": "op", "t": P("Descripciones repetidas", "Duplicate meta descriptions"),
                    "d": P(str(c("desc_dup")) + " grupo(s) de páginas usan la misma meta descripción. Conviene una por página, específica de su contenido.",
                           str(c("desc_dup")) + " group(s) of pages reuse the same meta description. Use one per page, specific to its content.")})
    if c("og_missing") > 0:
        out.append({"sev": "op", "t": P("Sin vista previa al compartir (Open Graph)", "No social preview (Open Graph)"),
                    "d": P(str(c("og_missing")) + " página(s) no tienen imagen/título Open Graph: " + _exurls(i["og_missing"].get("examples")) + ". Sin eso, al compartir el enlace en WhatsApp o redes se ve pobre y baja el clic.",
                           str(c("og_missing")) + " page(s) have no Open Graph image/title: " + _exurls(i["og_missing"].get("examples")) + ". Without it, links shared on WhatsApp or social look poor and get fewer clicks.")})
    if c("h2_missing") > 0:
        out.append({"sev": "op", "t": P("Páginas sin subtítulos (H2)", "Pages with no subheadings (H2)"),
                    "d": P(str(c("h2_missing")) + " página(s) no usan encabezados H2: " + _exurls(i["h2_missing"].get("examples")) + ". Los H2 ordenan el contenido y ayudan a Google y a la IA a entender la estructura.",
                           str(c("h2_missing")) + " page(s) use no H2 headings: " + _exurls(i["h2_missing"].get("examples")) + ". H2s organize content and help Google and AI understand the structure.")})
    if c("title_bad_len") > 0:
        out.append({"sev": "op", "t": P("Títulos demasiado cortos o largos", "Titles too short or long"),
                    "d": P(str(c("title_bad_len")) + " título(s) están fuera del rango ideal (25-65 car.) y Google puede recortarlos o no darles fuerza: " + _exurls(i["title_bad_len"].get("examples"), "url") + ".",
                           str(c("title_bad_len")) + " title(s) are outside the ideal range (25-65 chars) and Google may trim them or weight them less: " + _exurls(i["title_bad_len"].get("examples"), "url") + ".")})
    if c("thin") > 0:
        out.append({"sev": "op", "t": P("Páginas con poco contenido", "Thin content pages"),
                    "d": P(str(c("thin")) + " página(s) tienen poco texto (p. ej. " + _exurls(i["thin"].get("examples"), "url") + "). Con poco contenido, Google y la IA tienen menos para entenderte y citarte.",
                           str(c("thin")) + " page(s) have little text (e.g. " + _exurls(i["thin"].get("examples"), "url") + "). With thin content, Google and AI have less to understand and cite you.")})
    if c("canonical_missing") > 0:
        out.append({"sev": "op", "t": P("Falta la etiqueta canonical", "Missing canonical tag"),
                    "d": P(str(c("canonical_missing")) + " página(s) no declaran su URL canónica: " + _exurls(i["canonical_missing"].get("examples")) + ". Ayuda a evitar contenido duplicado.",
                           str(c("canonical_missing")) + " page(s) don't declare a canonical URL: " + _exurls(i["canonical_missing"].get("examples")) + ". It helps avoid duplicate content.")})
    if c("url_unfriendly") > 0:
        out.append({"sev": "op", "t": P("URLs poco amigables", "Unfriendly URLs"),
                    "d": P(str(c("url_unfriendly")) + " URL(s) no son ideales (largas, con parámetros o mayúsculas): " + _exurls(i["url_unfriendly"].get("examples"), "url") + ". Las URLs limpias posicionan y se comparten mejor.",
                           str(c("url_unfriendly")) + " URL(s) aren't ideal (long, with params or uppercase): " + _exurls(i["url_unfriendly"].get("examples"), "url") + ". Clean URLs rank and share better.")})
    if c("orphans") > 0:
        out.append({"sev": "op", "t": P("Páginas huérfanas (sin enlaces internos)", "Orphan pages (no internal links)"),
                    "d": P(_pg(c("orphans"), P) + " no reciben ningún enlace interno: " + _exurls(i["orphans"].get("examples")) + ". Google y los visitantes casi no llegan a ellas; enlázalas desde el menú o desde páginas relacionadas.",
                           _pg(c("orphans"), P) + " receive no internal links: " + _exurls(i["orphans"].get("examples")) + ". Google and visitors barely reach them; link them from the menu or related pages.")})
    if c("deep") > 0:
        out.append({"sev": "op", "t": P("Páginas demasiado profundas (más de 3 clics)", "Pages too deep (more than 3 clicks)"),
                    "d": P(_pg(c("deep"), P) + " están a más de 3 clics del inicio: " + _exurls(i["deep"].get("examples"), "url") + ". Cuanto más escondida, menos las prioriza Google. Acércalas con enlaces desde páginas principales.",
                           _pg(c("deep"), P) + " are more than 3 clicks from the home: " + _exurls(i["deep"].get("examples"), "url") + ". The deeper they sit, the less Google prioritizes them. Bring them closer with links from top pages.")})
    bc = i.get("breadcrumbs") or {}
    if bc.get("present") is False:
        out.append({"sev": "op", "t": P("Sin migas de pan (breadcrumbs)", "No breadcrumbs"),
                    "d": P("Tu sitio no usa breadcrumbs. Ayudan a Google a entender la jerarquía de páginas, mejoran la navegación y pueden salir en los resultados de búsqueda.",
                           "Your site has no breadcrumbs. They help Google understand the page hierarchy, improve navigation and can appear in search results.")})
    if c("poor_anchor") > 0:
        out.append({"sev": "op", "t": P("Enlaces con texto poco descriptivo", "Links with vague anchor text"),
                    "d": P(str(c("poor_anchor")) + " enlace(s) usan textos genéricos como 'aquí' o 'leer más'. Un texto de enlace descriptivo le dice a Google (y al usuario) a dónde lleva y reparte mejor la autoridad.",
                           str(c("poor_anchor")) + " link(s) use generic text like 'here' or 'read more'. Descriptive anchor text tells Google (and users) where it leads and spreads authority better.")})
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
    if not ai.get("available"):
        return out
    rec = ai.get("recognition")
    comp = [c.get("name") for c in (ai.get("competitors") or []) if c.get("name")][:3]
    if rec == "none":
        out.append({"sev": "critico", "t": P("La IA no sabe quién eres", "AI doesn't know who you are"),
                    "d": P("Le preguntamos por tu marca (con búsqueda web) y no te reconoce. Cada vez más clientes preguntan a la IA antes de decidir y hoy no apareces. Se corrige con entidad de marca (Schema Organization + perfiles), contenido citable y presencia en fuentes que la IA lee.",
                           "We asked about your brand (with web search) and it doesn't recognize you. More customers ask AI before deciding and today you don't appear. Fix it with brand entity (Organization Schema + profiles), citable content and presence in sources AI reads.")})
    elif rec == "weak":
        out.append({"sev": "op", "t": P("La IA solo te encuentra si le das tu web", "AI only finds you if you hand it your site"),
                    "d": P("Por su cuenta no te reconoce; solo te describe si visita tu dominio. Falta autoridad de entidad para que te tenga 'de memoria'.",
                           "On its own it doesn't recognize you; it only describes you if it visits your domain. You lack entity authority for it to know you 'by heart'.")})
    mh = ai.get("reco_hits")
    mt = ai.get("reco_total")
    measured = isinstance(mh, int) and isinstance(mt, int) and mt > 0
    mce = (" " + P("Lo medimos con " + str(mt) + " búsquedas reales de cliente (sin nombrarte): apareces en " + str(mh) + " de " + str(mt) + ".",
                   "We measured it with " + str(mt) + " real customer searches (without naming you): you appear in " + str(mh) + " of " + str(mt) + ".")) if measured else ""
    if ai.get("recommended") is False:
        out.append({"sev": "critico", "t": P("No apareces cuando piden tu servicio", "You don't appear when your service is requested"),
                    "d": P("Cuando un cliente pide tu tipo de servicio sin nombrarte, la IA nombra a otros antes que a ti" + (": " + ", ".join(comp) if comp else "") + "." + mce + " Pierdes a los clientes que aún no te conocen. Necesitas una página por servicio con vocabulario de cliente y señales de autoridad.",
                           "When a customer asks for your type of service without naming you, AI names others before you" + (": " + ", ".join(comp) if comp else "") + "." + mce + " You lose customers who don't know you yet. You need a page per service with customer vocabulary and authority signals.")})
    elif ai.get("recommended") is None and rec != "none":
        out.append({"sev": "op", "t": P("Apareces de forma inconsistente", "You appear inconsistently"),
                    "d": P("Cuando piden tu servicio, a veces sales y a veces no." + mce + " Reforzar contenido por servicio y señales de autoridad te hará aparecer siempre.",
                           "When your service is requested, sometimes you show up and sometimes not." + mce + " Strengthening per-service content and authority signals will make you appear consistently.")})
    srcs = ai.get("sources") or []
    ext = [s for s in srcs if not s.get("own")]
    if len(ext) > 0:
        out.append({"sev": "op", "t": P("Fuentes con las que la IA te cita", "Sources AI cites you from"),
                    "d": P("La IA se apoyó en: " + ", ".join(s.get("domain", "") for s in ext[:5]) + ". Consigue más menciones en sitios así (prensa, directorios, reseñas) para que te cite con más fuerza.",
                           "AI relied on: " + ", ".join(s.get("domain", "") for s in ext[:5]) + ". Get more mentions on sites like these (press, directories, reviews) so it cites you more strongly.")})
    else:
        out.append({"sev": "op", "t": P("La IA no cita fuentes que hablen de ti", "AI cites no sources about you"),
                    "d": P("Al describirte, la IA no se apoya en fuentes externas (prensa, directorios, reseñas). Esas menciones son las que te dan autoridad ante la IA.",
                           "When describing you, AI relies on no external sources (press, directories, reviews). Those mentions are what give you authority with AI.")})
    lq = sig.get("llms_quality")
    if lq == "none":
        out.append({"sev": "op", "t": P("Falta el llms.txt (guía para la IA)", "Missing llms.txt (guide for AI)"),
                    "d": P("Es un archivo que le dice a los buscadores con IA qué páginas tuyas son clave y cómo entenderte. No lo tienes; añadirlo ayuda a que te citen bien.",
                           "It's a file that tells AI search engines which of your pages are key and how to understand you. You don't have it; adding it helps them cite you correctly.")})
    elif lq == "thin":
        out.append({"sev": "op", "t": P("Tu llms.txt está incompleto", "Your llms.txt is thin"),
                    "d": P("Tienes llms.txt pero le faltan secciones y enlaces a tus páginas clave. Complétalo para guiar mejor a la IA.",
                           "You have llms.txt but it lacks sections and links to your key pages. Complete it to guide AI better.")})
    sch = meta.get("schema_types") or []
    if not (meta.get("has_faq") or _rx(sch, r"faqpage|qapage")):
        out.append({"sev": "op", "t": P("Falta contenido citable (FAQ)", "Missing citable content (FAQ)"),
                    "d": P("La IA cita respuestas cortas y claras. Una sección de preguntas frecuentes con datos estructurados hace que la IA tome respuestas directas de tu web.",
                           "AI quotes short, clear answers. An FAQ section with structured data lets AI take direct answers from your site.")})
    org = _rx(sch, r"organization|localbusiness|professionalservice")
    if not (org and meta.get("has_sameas")):
        out.append({"sev": "op", "t": P("Tu marca no está definida como entidad", "Your brand isn't defined as an entity"),
                    "d": P("Para que la IA te reconozca 'de memoria' necesitas Schema Organization con tus perfiles oficiales (sameAs a LinkedIn, Instagram, etc.) y presencia en directorios. Hoy " + (P("tienes schema pero sin perfiles enlazados", "you have schema but no linked profiles") if org else P("no declaras entidad de marca", "you don't declare a brand entity")) + ".",
                           "For AI to recognize you 'by heart' you need Organization Schema with your official profiles (sameAs to LinkedIn, Instagram, etc.) and directory presence. Today " + (P("tienes schema pero sin perfiles enlazados", "you have schema but no linked profiles") if org else P("no declaras entidad de marca", "you don't declare a brand entity")) + ".")})
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
                    "d": P("Tanto " + host + " como www." + host + " responden por separado, así que Google ve DOS webs iguales y reparte tu fuerza entre las dos (contenido duplicado). Elige una versión y redirige la otra con 301.",
                           "Both " + host + " and www." + host + " respond separately, so Google sees TWO identical sites and splits your strength between them (duplicate content). Pick one version and 301-redirect the other.")})
    elif w.get("one_fails") and w.get("broken_host"):
        out.append({"sev": "op", "t": P("Una versión (www / no-www) da error", "One version (www / non-www) errors out"),
                    "d": P(str(w.get("broken_host")) + " da error en vez de redirigir a la versión buena. Si alguien la enlaza o la escribe, se encuentra un fallo. Configura una redirección 301 de " + str(w.get("broken_host")) + " a " + str(w.get("canonical_host") or host) + ".",
                           str(w.get("broken_host")) + " returns an error instead of redirecting to the good version. If someone links or types it, they hit a failure. Set a 301 redirect from " + str(w.get("broken_host")) + " to " + str(w.get("canonical_host") or host) + ".")})
    pages = sig.get("pages_found") or 0
    idx = ix.get("indexed_estimate") if ix.get("indexed_estimate") is not None else ((ix.get("sample_count") or 0) if ix.get("indexed") else 0)
    if sig.get("https") is False:
        out.append({"sev": "critico", "t": P("Tu web no tiene conexión segura (HTTPS)", "Your site has no secure connection (HTTPS)"),
                    "d": P("Los navegadores la marcan como 'no segura' y Google la penaliza. Es lo primero a corregir: instala el certificado SSL.",
                           "Browsers flag it as 'not secure' and Google penalizes it. Fix this first: install the SSL certificate.")})
    elif sig.get("https_forced") is False:
        out.append({"sev": "op", "t": P("No fuerzas HTTPS", "You don't force HTTPS"),
                    "d": P("Tu web abre en HTTPS pero no redirige http a https. Configura la redirección 301 para no dividir señales ni servir contenido inseguro.",
                           "Your site opens on HTTPS but doesn't redirect http to https. Set the 301 redirect so signals aren't split and no insecure content is served.")})
    brkidx = ix.get("broken_indexed") or []
    if brkidx:
        out.append({"sev": "critico", "t": P("Páginas rotas indexadas en Google", "Broken pages indexed on Google"),
                    "d": P("Google tiene indexadas " + str(len(brkidx)) + " página(s) que ya dan error 404 (p. ej. " + ", ".join(_shortu(b.get("url")) for b in brkidx[:2]) + "). Restan confianza y desperdician rastreo; redirígelas con 301.",
                           "Google has " + str(len(brkidx)) + " indexed page(s) returning 404 (e.g. " + ", ".join(_shortu(b.get("url")) for b in brkidx[:2]) + "). They erode trust and waste crawl budget; 301-redirect them.")})
    opb = (r.get("onpage") or {}).get("broken") or {}
    brkn = opb.get("count") if opb.get("count") is not None else (sig.get("links_broken") or 0)
    brkchecked = opb.get("checked") if opb.get("checked") is not None else (sig.get("links_checked") or 0)
    brkex = opb.get("broken") if opb.get("broken") else (sig.get("broken_examples") or [])
    if (brkn or 0) > 0:
        out.append({"sev": ("critico" if brkn >= 5 else "op"), "t": P("Enlaces rotos (404) en el sitio", "Broken links (404) on the site"),
                    "d": P("Comprobamos " + str(brkchecked) + " enlaces del sitio y " + str(brkn) + " dan error" + (" (p. ej. " + ", ".join(_shortu(b.get("url")) for b in brkex[:3]) + ")" if brkex else "") + ". Cada enlace roto frustra al visitante y desperdicia rastreo: arréglalos o redirígelos con 301.",
                           "We checked " + str(brkchecked) + " links across the site and " + str(brkn) + " return an error" + (" (e.g. " + ", ".join(_shortu(b.get("url")) for b in brkex[:3]) + ")" if brkex else "") + ". Every broken link frustrates visitors and wastes crawl budget: fix or 301-redirect them.")})
    if not sig.get("sitemap"):
        out.append({"sev": "op", "t": P("Falta el mapa del sitio (sitemap.xml)", "Missing sitemap.xml"),
                    "d": P("El sitemap le dice a Google qué páginas rastrear. Sin él, tarda más en descubrir tu contenido. Genera uno y decláralo en robots.txt.",
                           "The sitemap tells Google which pages to crawl. Without it, discovery is slower. Generate one and declare it in robots.txt.")})
    else:
        comp = sig.get("sitemap_comp") or {}
        if ((comp.get("etiquetas") or 0) > (comp.get("paginas") or 0)) or ((comp.get("fichas") or 0) > (comp.get("paginas") or 0)):
            out.append({"sev": "op", "t": P("Tu sitemap está sucio", "Your sitemap is messy"),
                        "d": P("El sitemap manda a Google sobre todo a etiquetas o fichas (" + str(comp.get("etiquetas") or 0) + " etiquetas, " + str(comp.get("fichas") or 0) + " fichas) en vez de páginas reales (" + str(comp.get("paginas") or 0) + "). Límpialo para que rastree lo que importa.",
                               "The sitemap points Google mostly to tags or files (" + str(comp.get("etiquetas") or 0) + " tags, " + str(comp.get("fichas") or 0) + " files) instead of real pages (" + str(comp.get("paginas") or 0) + "). Clean it so it crawls what matters.")})
    if meta.get("robots_noindex"):
        out.append({"sev": "critico", "t": P("Tu web se está bloqueando a sí misma (noindex)", "Your site is blocking itself (noindex)"),
                    "d": P("La etiqueta meta robots de tu página dice 'noindex': le pides a Google y a la IA que NO te muestren. Es lo más grave que puede tener una web para su visibilidad; hay que quitarlo ya.",
                           "Your page's meta robots tag says 'noindex': you're telling Google and AI NOT to show you. It's the most serious visibility problem a site can have; remove it right away.")})
    rob = sig.get("robots_info") or {}
    if not sig.get("robots"):
        out.append({"sev": "op", "t": P("Falta robots.txt", "Missing robots.txt"),
                    "d": P("Es el archivo que guía a los rastreadores. Añádelo y declara ahí tu sitemap.",
                           "It's the file that guides crawlers. Add it and declare your sitemap there.")})
    elif rob.get("blocks_all"):
        out.append({"sev": "critico", "t": P("Tu robots.txt bloquea todo el sitio a Google", "Your robots.txt blocks the whole site from Google"),
                    "d": P("El robots.txt tiene 'Disallow: /' para todos los rastreadores: le estás pidiendo a Google que NO rastree nada. Ninguna página podrá posicionarse. Quítalo de inmediato.",
                           "robots.txt has 'Disallow: /' for all crawlers: you're telling Google NOT to crawl anything. No page can rank. Remove it immediately.")})
    else:
        if rob.get("blocks_render"):
            out.append({"sev": "critico", "t": P("Bloqueas CSS/JS que Google necesita para ver tu web", "You block CSS/JS Google needs to render your site"),
                        "d": P("Tu robots.txt bloquea recursos como " + ", ".join(rob["blocks_render"][:3]) + ". Google necesita el CSS y el JavaScript para 'ver' la página como un usuario; si los bloqueas, la ve rota y baja tus posiciones. Permite esos recursos.",
                               "Your robots.txt blocks resources like " + ", ".join(rob["blocks_render"][:3]) + ". Google needs CSS and JavaScript to 'see' the page like a user; blocking them makes it look broken and hurts rankings. Allow those resources.")})
        if rob.get("blocks_content"):
            out.append({"sev": "op", "t": P("Tu robots.txt bloquea secciones de contenido", "Your robots.txt blocks content sections"),
                        "d": P("Estás impidiendo que Google rastree " + ", ".join(rob["blocks_content"][:3]) + ". Si ahí hay contenido que quieres posicionar, no aparecerá en Google. Revisa que solo bloquees lo que de verdad no debe indexarse (admin, carrito, búsquedas).",
                               "You're preventing Google from crawling " + ", ".join(rob["blocks_content"][:3]) + ". If that's content you want to rank, it won't show on Google. Make sure you only block what truly shouldn't be indexed (admin, cart, search).")})
        if not sig.get("sitemap_in_robots") and sig.get("sitemap"):
            out.append({"sev": "op", "t": P("Tu robots.txt no declara el sitemap", "Your robots.txt doesn't declare the sitemap"),
                        "d": P("Añade la línea 'Sitemap:' con la URL de tu mapa del sitio en el robots.txt para que Google lo encuentre antes.",
                               "Add a 'Sitemap:' line with your sitemap URL in robots.txt so Google finds it sooner.")})
    hasidx = ix.get("indexed") or ix.get("indexed_estimate") is not None or (ix.get("sample_count") or 0) > 0
    if hasidx and pages > 0 and idx < pages * 0.3:
        out.append({"sev": "op", "t": P("Revisa tu cobertura de indexación", "Review your indexing coverage"),
                    "d": P("Rastreamos ~" + str(pages) + " páginas y en el buscador aparecen alrededor de " + str(idx) + ". La medición es orientativa (no consultamos Google directo); confírmalo en Search Console (Cobertura) y refuerza el sitemap y los enlaces internos.",
                           "We crawled ~" + str(pages) + " pages and about " + str(idx) + " show in search. This is an estimate (we don't query Google directly); confirm it in Search Console (Coverage) and reinforce the sitemap and internal links.")})
    if not meta.get("viewport"):
        out.append({"sev": "op", "t": P("No está preparada para móvil", "Not mobile-ready"),
                    "d": P("Falta la etiqueta viewport. Google indexa en modo móvil primero; sin esto se ve mal en el teléfono y pierdes posiciones.",
                           "The viewport tag is missing. Google indexes mobile-first; without it the site looks bad on phones and you lose rankings.")})
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
                    "d": P("El Schema Organization le dice a Google y a la IA tu nombre, logo, contacto y perfiles oficiales. Es la base para que te reconozcan como entidad. Añádelo en todo el sitio.",
                           "Organization Schema tells Google and AI your name, logo, contact and official profiles. It's the base for being recognized as an entity. Add it site-wide.")})
    if not by["website"]:
        out.append({"sev": "op", "t": P("Falta WebSite + búsqueda del sitio (SearchAction)", "Missing WebSite + SearchAction"),
                    "d": P("El marcado WebSite con SearchAction permite que Google muestre tu buscador interno en los resultados. Un extra que da imagen de marca sólida.",
                           "WebSite markup with SearchAction lets Google show your internal search box in results. An extra that signals a solid brand.")})
    if not by["prodserv"]:
        out.append({"sev": "op", "t": P("Falta marcar tus productos o servicios", "Product/Service markup missing"),
                    "d": P("Marcar cada servicio o producto con Schema ayuda a Google y a la IA a saber exactamente qué ofreces y a mostrarte cuando lo buscan.",
                           "Marking each service or product with Schema helps Google and AI know exactly what you offer and show you when it's searched.")})
    if not by["review"]:
        _has_test = bool((r.get("meta") or {}).get("has_testimonials"))
        if _has_test:
            # La web SÍ muestra testimonios/opiniones, pero no están marcados como Review:
            # no digas "no tienes valoraciones", di que las marques para ganar estrellas.
            out.append({"sev": "op", "t": P("Tienes testimonios pero no están marcados (Review)", "You have testimonials but no Review markup"),
                        "d": P("Tu web muestra testimonios u opiniones de clientes, pero no llevan el Schema Review/AggregateRating. Márcalos para que Google pueda mostrar tus estrellas en los resultados y para que la IA los use como señal de confianza al recomendarte.",
                               "Your site shows customer testimonials, but they lack Review/AggregateRating Schema. Mark them up so Google can show your stars in results and AI uses them as a trust signal when recommending you.")})
        else:
            out.append({"sev": "op", "t": P("Falta marcar reseñas y valoraciones (Review)", "Missing Review/Rating markup"),
                        "d": P("El Schema Review/AggregateRating puede mostrar tus estrellas en Google y es una señal que la IA usa para recomendarte. Márcalo si tienes valoraciones.",
                               "Review/AggregateRating Schema can show your stars in Google and is a signal AI uses to recommend you. Mark it up if you have ratings.")})
    sch = (r.get("onpage") or {}).get("schema") or {}
    errs = sch.get("errors") or 0
    incompl = sch.get("incomplete") or []
    if errs > 0:
        out.append({"sev": "op", "t": P("Tus datos estructurados tienen errores de formato", "Your structured data has format errors"),
                    "d": P("Detectamos " + str(errs) + " bloque(s) de datos estructurados que no son JSON válido, así que Google los ignora. Corrige el formato para que cuenten.",
                           "We found " + str(errs) + " structured-data block(s) that aren't valid JSON, so Google ignores them. Fix the format so they count.")})
    if incompl:
        out.append({"sev": "op", "t": P("A tus datos estructurados les faltan datos", "Your structured data is incomplete"),
                    "d": P("Algunas de tus etiquetas (" + ", ".join(str(x.get("type")) for x in incompl[:3]) + ") no traen toda la información que Google pide, así que no te darán los resultados destacados (estrellas, imágenes, precios). Conviene completarlas para que cuenten.",
                           "Some of your tags (" + ", ".join(str(x.get("type")) for x in incompl[:3]) + ") don't carry all the info Google asks for, so they won't earn rich results (stars, images, prices). Completing them makes them count.")})
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
                    "d": P("Detectamos " + str(len(exp)) + " recurso(s) accesibles que deberían estar protegidos (p. ej. " + ", ".join(str(e.get("path")) for e in exp[:2]) + "). Bloquéalos cuanto antes: pueden filtrar datos o credenciales.",
                           "We found " + str(len(exp)) + " accessible resource(s) that should be protected (e.g. " + ", ".join(str(e.get("path")) for e in exp[:2]) + "). Block them ASAP: they can leak data or credentials.")})
    if ssl.get("valid") is False:
        out.append({"sev": "critico", "t": P("Tu certificado SSL no es válido", "Your SSL certificate is invalid"),
                    "d": P("El certificado está caducado, no coincide con el dominio o es autofirmado. Los navegadores muestran una alerta roja de 'sitio no seguro' que espanta a los visitantes. Renuévalo ya.",
                           "The certificate is expired, doesn't match the domain or is self-signed. Browsers show a red 'not secure' warning that scares visitors away. Renew it now.")})
    elif ssl.get("valid") is True and ssl.get("days_left") is not None and ssl.get("days_left") < 15:
        out.append({"sev": "op", "t": P("Tu certificado SSL caduca pronto", "Your SSL certificate expires soon"),
                    "d": P("Le quedan " + str(ssl.get("days_left")) + " día(s). Si caduca, tu web sale como 'no segura'. Renuévalo o activa la renovación automática.",
                           "It has " + str(ssl.get("days_left")) + " day(s) left. If it expires, your site shows as 'not secure'. Renew it or enable auto-renewal.")})
    if sig.get("https") is False:
        out.append({"sev": "critico", "t": P("Tu web no usa HTTPS", "Your site doesn't use HTTPS"),
                    "d": P("Sin conexión segura, los navegadores la marcan como 'no segura' y Google la penaliza. Instala un certificado SSL.",
                           "Without a secure connection, browsers flag it as 'not secure' and Google penalizes it. Install an SSL certificate.")})
    elif mixed:
        out.append({"sev": "op", "t": P("Cargas contenido inseguro (mixto)", "You load insecure (mixed) content"),
                    "d": P("Tu web es HTTPS pero carga " + str(len(mixed)) + " recurso(s) por http:// (p. ej. " + ", ".join(re.sub(r"^https?://", "", str(u))[:40] for u in mixed[:2]) + "). El navegador los bloquea o muestra el candado roto. Cámbialos a https.",
                           "Your site is HTTPS but loads " + str(len(mixed)) + " resource(s) over http:// (e.g. " + ", ".join(re.sub(r"^https?://", "", str(u))[:40] for u in mixed[:2]) + "). The browser blocks them or shows a broken padlock. Switch them to https.")})
    if miss:
        out.append({"sev": "op", "t": P("Faltan cabeceras de seguridad", "Missing security headers"),
                    "d": P("Te faltan " + str(len(miss)) + " cabecera(s) que protegen contra ataques comunes: " + ", ".join(str(x) for x in miss[:3]) + ". Son fáciles de añadir en el servidor y suben tu seguridad.",
                           "You're missing " + str(len(miss)) + " header(s) that protect against common attacks: " + ", ".join(str(x) for x in miss[:3]) + ". They're easy to add on the server and boost your security.")})
    if leaks:
        out.append({"sev": "op", "t": P("El servidor revela su versión", "The server reveals its version"),
                    "d": P("Tu servidor expone su tecnología y versión (" + ", ".join(str(x) for x in leaks[:2]) + "). Eso facilita a un atacante buscar vulnerabilidades conocidas. Ocúltalo.",
                           "Your server exposes its tech and version (" + ", ".join(str(x) for x in leaks[:2]) + "). That helps an attacker look up known vulnerabilities. Hide it.")})
    if ck:
        out.append({"sev": "op", "t": P("Tus cookies no son seguras", "Your cookies aren't secure"),
                    "d": P("Detectamos " + ", ".join(str(x) for x in ck) + ". Las cookies deben llevar los atributos Secure y HttpOnly para que no las roben.",
                           "We found " + ", ".join(str(x) for x in ck) + ". Cookies should carry the Secure and HttpOnly attributes so they can't be stolen.")})
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
                    "d": P("Sin ficha no apareces en el mapa ni en las búsquedas locales, y pierdes las reseñas que Google y la IA usan para recomendarte. Una ficha activa y bien completa (categoría, dirección, teléfono, horario, fotos) es de lo que más mueve tu visibilidad local.",
                           "Without a profile you don't appear on the map or in local searches, and you lose the reviews Google and AI use to recommend you. Create it for free and complete it (category, address, phone, hours, photos).")})
    elif ai.get("gbp") is True and rev == 0:
        out.append({"sev": "op", "t": P("Tu ficha de Google no tiene reseñas", "Your Google profile has no reviews"),
                    "d": P("Las reseñas son de lo que más miran los clientes y la IA para elegir. Pide reseñas a tus clientes contentos: suben tu posición local y tu confianza.",
                           "Reviews are one of the things customers and AI look at most when choosing. Ask your happy customers for reviews: they boost your local ranking and trust.")})
    if not (meta.get("has_address") and meta.get("has_phone")):
        out.append({"sev": "op", "t": P("Faltan datos de contacto claros (NAP)", "Missing clear contact details (NAP)"),
                    "d": P("No mostramos " + (P("tu dirección", "your address") if not meta.get("has_address") else P("tu teléfono", "your phone")) + " de forma clara. El nombre, la dirección y el teléfono (NAP) deben aparecer iguales en la web y en tu ficha para que Google y la IA confíen en ti.",
                           "We couldn't clearly find " + (P("your address", "your address") if not meta.get("has_address") else P("your phone", "your phone")) + ". Your name, address and phone (NAP) should appear the same on the site and your profile so Google and AI trust you.")})
    if not (meta.get("has_map") or meta.get("has_geo")):
        out.append({"sev": "op", "t": P("No incrustas un mapa en tu web", "No map embedded on your site"),
                    "d": P("Tu ubicación aparece en Google, pero no incrustas un mapa en tu página de contacto. Añadirlo ayuda a los clientes a llegar y refuerza la señal de negocio local en tu propio sitio.",
                           "Your location shows on Google, but you don't embed a map on your contact page. Adding one helps customers reach you and reinforces the local-business signal on your own site.")})
    if not lb:
        out.append({"sev": "op", "t": P("Falta marcar tu negocio (LocalBusiness)", "Missing LocalBusiness markup"),
                    "d": P("El Schema LocalBusiness con dirección, teléfono, horario y geolocalización le dice a Google exactamente qué eres y dónde, y te ayuda a salir en el mapa y en la IA local.",
                           "LocalBusiness Schema with address, phone, hours and geolocation tells Google exactly what and where you are, helping you appear on the map and in local AI.")})
    if ai.get("gbp") is True and not ai.get("gbp_category"):
        out.append({"sev": "op", "t": P("Define bien la categoría de tu ficha", "Set your profile's category properly"),
                    "d": P("La categoría principal de tu ficha de Google decide en qué búsquedas apareces. Si no está bien elegida, no sales cuando te buscan. Revisa y ajusta tu categoría (y añade categorías secundarias).",
                           "Your Google profile's main category decides which searches you appear in. If it's not well chosen, you don't show up. Review and fix your category (and add secondary ones).")})
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
                    "d": P("Detectamos " + str(dup.get("count")) + " par(es) de páginas con texto casi idéntico" + (" (p. ej. " + _shortu(ex.get("a")) + " ↔ " + _shortu(ex.get("b")) + ")" if ex.get("a") else "") + ". El contenido duplicado confunde a Google sobre cuál posicionar; unifícalas o diferéncialas.",
                           "We found " + str(dup.get("count")) + " pair(s) of pages with near-identical text" + (" (e.g. " + _shortu(ex.get("a")) + " ↔ " + _shortu(ex.get("b")) + ")" if ex.get("a") else "") + ". Duplicate content confuses Google about which to rank; merge or differentiate them.")})
    dated = c.get("dated_pages") or 0
    fresh = c.get("fresh_pages") or 0
    fr = (fresh / dated) if dated else 0
    if dated == 0:
        out.append({"sev": "op", "t": P("No se ve cuándo actualizas el contenido", "No visible content dates"),
                    "d": P("Tus páginas no muestran fecha de publicación o actualización. Google y la IA valoran el contenido fresco; añade fechas visibles y datePublished/dateModified en el schema.",
                           "Your pages show no publish/update date. Google and AI value fresh content; add visible dates and datePublished/dateModified in schema.")})
    elif fr < 0.5:
        out.append({"sev": "op", "t": P("Buena parte del contenido está desactualizado", "Much of the content is outdated"),
                    "d": P("Solo " + str(fresh) + " de " + str(dated) + " páginas con fecha están actualizadas (lo más nuevo: " + str(c.get("newest") or "") + "). Refresca los artículos clave: el contenido antiguo pierde posiciones.",
                           "Only " + str(fresh) + " of " + str(dated) + " dated pages are recent (newest: " + str(c.get("newest") or "") + "). Refresh key articles: stale content loses rankings.")})
    coh = c.get("coherence_pct")
    if coh is not None and coh < 70:
        out.append({"sev": "op", "t": P("Contenido poco enfocado en su tema", "Content not focused on its topic"),
                    "d": P("En el " + str(100 - coh) + "% de las páginas el título o el H1 no reflejan bien de qué trata el texto. Alinea título, H1 y contenido en torno a una idea principal por página.",
                           "On " + str(100 - coh) + "% of pages the title or H1 doesn't reflect the text well. Align title, H1 and content around one main idea per page.")})
    if not c.get("about_page"):
        out.append({"sev": "op", "t": P("Falta una página 'Sobre nosotros'", "Missing an 'About' page"),
                    "d": P("No encontramos una página que cuente quién está detrás. Es una señal de confianza (E-E-A-T) que Google y la IA valoran para recomendarte.",
                           "We couldn't find a page telling who's behind the site. It's a trust signal (E-E-A-T) Google and AI value when recommending you.")})
    if not c.get("author"):
        out.append({"sev": "op", "t": P("Tus contenidos no muestran autor", "Your content shows no author"),
                    "d": P("Los artículos sin autor pierden autoridad (E-E-A-T). Añade el autor con su experiencia y marcado Author en el schema.",
                           "Articles with no author lose authority (E-E-A-T). Add the author with their expertise and Author schema markup.")})
    kws = (ca.get("keywords") if ca else None) or ((r.get("geo_ai") or {}).get("keywords")) or []
    if kws:
        terms = " ".join(c.get("top_terms") or []).lower()
        cov = any(any(len(w) > 3 and w in terms for w in str(k).lower().split()) for k in kws)
        if not cov:
            out.append({"sev": "op", "t": P("Tu contenido no cubre bien tus palabras clave", "Content doesn't cover your keywords"),
                        "d": P("Por lo que ofreces, deberías posicionar por: " + ", ".join(kws[:4]) + ". Tu texto actual no las trabaja de forma clara. Crea contenido enfocado en esos términos. (El volumen exacto de búsqueda se confirma con una herramienta de keywords).",
                               "For what you offer, you should rank for: " + ", ".join(kws[:4]) + ". Your current copy doesn't clearly target them. Create content focused on those terms. (Exact search volume needs a keyword tool).")})
        else:
            out.append({"sev": "op", "t": P("Palabras clave objetivo (según la IA)", "Target keywords (per AI)"),
                        "d": P("La IA identificó tus palabras clave: " + ", ".join(kws[:5]) + ". Refuerza una página por tema para dominarlas. (El volumen exacto se confirma con una herramienta de keywords).",
                               "AI identified your keywords: " + ", ".join(kws[:5]) + ". Strengthen one page per topic to own them. (Exact volume needs a keyword tool).")})
    if (c.get("clusters") or 0) < 1:
        out.append({"sev": "op", "t": P("Falta estructura por temas (pilar + clusters)", "Missing topic structure (pillar + clusters)"),
                    "d": P("Tu contenido no está agrupado por temas con una página pilar y sus artículos relacionados enlazados entre sí. Esa estructura es de lo que más ayuda a posicionar y a que la IA te entienda.",
                           "Your content isn't grouped into topics with a pillar page and related articles linked together. That structure is one of the biggest helps for ranking and for AI to understand you.")})
    if ca and ca.get("gaps"):
        out.append({"sev": "op", "t": P("Mejoras de contenido (análisis de IA)", "Content improvements (AI analysis)"),
                    "d": P("La IA revisó tu contenido en vivo y sugiere: " + "; ".join(ca["gaps"]) + ".",
                           "AI reviewed your content live and suggests: " + "; ".join(ca["gaps"]) + ".")})
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
