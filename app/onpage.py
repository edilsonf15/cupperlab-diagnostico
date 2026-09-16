"""
onpage — Auditoría SEO on-page AVANZADA (multi-página, rastreo profundo).

Rastrea el sitio "de pies a cabeza": siembra desde el sitemap.xml y los enlaces
del home, y SIGUE enlaces internos (BFS) hasta cubrir el sitio (sitios pequeños
al 100%, grandes con una muestra amplia). Por página revisa: títulos, meta
descripciones, H1/H2 (estructura), Open Graph/Twitter, hreflang/idioma, contenido
(thin), imágenes sin ALT, canonical, indexabilidad (noindex), URLs amigables y
datos estructurados. Agrega los problemas del sitio con CONTEO real (suma de todas
las páginas) y EJEMPLOS de URLs concretas, y da una nota 0-100.

Todo se mide en vivo; nada se inventa. Español neutro, sin guion largo.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.6"}

MAX_PAGES = 120         # analizamos a fondo TODO el sitio (hasta este tope alto)
CRAWL_BUDGET = 35.0     # segundos máx. de rastreo profundo (para no eternizar webs enormes)
CONCURRENCY = 8
PAGE_TIMEOUT = 10.0
SITEMAP_CAP = 300       # URLs máximas a leer del sitemap para sembrar
BROKEN_CAP = 400        # URLs máximas a comprobar por 404 (todo el sitio)
BROKEN_CONC = 10
BROKEN_TIMEOUT = 9.0

TITLE_MIN, TITLE_MAX = 25, 65
DESC_MIN, DESC_MAX = 60, 165
THIN_WORDS = 200

_SKIP_RE = re.compile(
    r"/(wp-admin|wp-login|admin|login|signin|sign-in|logout|cart|checkout|"
    r"my-account|mi-cuenta|carrito|wp-json)(/|\.|$)", re.I)
_ASSET_RE = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|css|js|xml|ico|mp4|woff2?|avif)$", re.I)
# Navegación por facetas/filtros, orden, paginación de tienda y parámetros de
# seguimiento: NO son páginas reales ni "enlaces rotos". Google no debería
# rastrearlas y nosotros tampoco las contamos como 404.
_FACETED_RE = re.compile(
    r"[?&](filter_|filtering|min_price|max_price|orderby|product_orderby|per_page|"
    r"add[-_]to[-_](cart|wishlist)|remove_item|wc-ajax|query_type_|rating_filter|"
    r"product-page|added-to-cart|swoof|really_curr_tax|utm_|fbclid|gclid|mc_[a-z]+|"
    r"_ga|srsltid|replytocom)", re.I)


def _norm(u: str) -> str:
    return (u or "").split("#")[0].rstrip("/").lower()


# --- Análisis de contenido (frecuencia de términos, duplicados, frescura) ---
_STOP = set((
    "de la que el en y a los del se las por un para con no una su al es lo como mas pero "
    "sus le ya o este si porque esta entre cuando muy sin sobre tambien me hasta hay donde "
    "quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos e esto mi "
    "antes algunos que unos yo otro otras otra el tanto esa estos mucho quienes nada muchos "
    "cual sea poco ella estar haber estas estaba estamos algunas algo nosotros mi mis tu te ti "
    "the of to and a in is it you that he was for on are with as his they be at one have this "
    "from or had by hot but some what there we can out other were all your when up use word how "
    "said an each she which do their time if will way about many then them would write like so "
    "these her long make thing see him two has look more day could go come did my no most who "
    "para más está sí").split())


def _c_tokens(text: str) -> list:
    return [w for w in re.findall(r"[a-záéíóúñü0-9]{3,}", (text or "").lower()) if w not in _STOP]


def _shingles(tokens: list, n: int = 4) -> set:
    return set(" ".join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1)))


def _page_date(soup, raw_dates: str) -> str:
    """Fecha de publicación/actualización más reciente (YYYY-MM-DD) que se pueda leer."""
    found = re.findall(r"\"date(?:Modified|Published)\"\s*:\s*\"(\d{4}-\d{2}-\d{2})", raw_dates or "")
    mt = soup.find("meta", attrs={"property": re.compile("article:modified_time", re.I)})
    if mt and mt.get("content"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", mt["content"])
        if m:
            found.append(m.group(1))
    for tt in soup.find_all("time"):
        dt = tt.get("datetime") or ""
        m = re.match(r"(\d{4}-\d{2}-\d{2})", dt)
        if m:
            found.append(m.group(1))
    return max(found) if found else ""


# Campos obligatorios por tipo (alineado con lo que pide Google para resultados enriquecidos)
_REQ = {
    "organization": ["name", "url", "logo"],
    "localbusiness": ["name", "address", "telephone"],
    "professionalservice": ["name", "address"],
    "product": ["name"],
    "article": ["headline", "image", "datePublished"],
    "blogposting": ["headline", "image", "datePublished"],
    "newsarticle": ["headline", "image", "datePublished"],
    "breadcrumblist": ["itemListElement"],
    "faqpage": ["mainEntity"],
    "review": ["reviewRating", "author", "itemReviewed"],
    "aggregaterating": ["ratingValue", "reviewCount"],
    "event": ["name", "startDate", "location"],
    "recipe": ["name", "image", "recipeIngredient"],
}


def _flatten_ld(data) -> list:
    out = []

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("@graph"), list):
                for n in x["@graph"]:
                    walk(n)
            if x.get("@type"):
                out.append(x)
        elif isinstance(x, list):
            for n in x:
                walk(n)
    walk(data)
    return out


def _missing_req(node: dict):
    t = node.get("@type")
    if isinstance(t, list):
        t = t[0] if t else ""
    tl = str(t).lower()
    req = _REQ.get(tl)
    if not req:
        return None
    missing = [f for f in req if not node.get(f)]
    if tl == "product" and not any(k in node for k in ("offers", "review", "aggregateRating")):
        missing.append("offers")
    return {"type": str(t), "missing": missing[:4]} if missing else None


def _valid(u: str, base_net: str) -> bool:
    if not u or urlparse(u).netloc != base_net:
        return False
    if _ASSET_RE.search(u) or _SKIP_RE.search(u):
        return False
    if _FACETED_RE.search(u):
        return False
    return True


# --------------------------------------------------------------------------- #
# Parseo de una página
# --------------------------------------------------------------------------- #
def _parse_page(url: str, html: str, status: int, base_net: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")

    title = soup.title.string.strip() if (soup.title and soup.title.string) else ""

    def meta(name=None, prop=None):
        if name:
            t = soup.find("meta", attrs={"name": re.compile("^" + name + "$", re.I)})
        else:
            t = soup.find("meta", attrs={"property": re.compile("^" + prop + "$", re.I)})
        return (t.get("content") or "").strip() if t else ""

    desc = meta(name="description")
    robots = meta(name="robots").lower()
    noindex = "noindex" in robots
    og_title = meta(prop="og:title")
    og_image = meta(prop="og:image")
    twitter = bool(soup.find("meta", attrs={"name": re.compile("^twitter:card$", re.I)}))

    hreflangs = sorted({(lk.get("hreflang") or "").strip().lower()
                        for lk in soup.find_all("link", attrs={"rel": re.compile("alternate", re.I)})
                        if lk.get("hreflang")})

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2_count = len(soup.find_all("h2"))
    h3_count = len(soup.find_all("h3"))

    can_tag = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    canonical = (can_tag.get("href") or "").strip() if can_tag else ""

    imgs = soup.find_all("img")
    img_total = img_no_alt = 0
    for im in imgs:
        src = im.get("src") or im.get("data-src") or ""
        if not src or src.startswith("data:"):
            continue
        img_total += 1
        if not (im.get("alt") or "").strip():
            img_no_alt += 1

    # enlaces internos (para BFS, grafo y conteo) + anchor text pobre
    _GENERIC = {"aquí", "aqui", "aca", "acá", "clic", "click", "aquí.", "leer más", "leer mas",
                "ver más", "ver mas", "más info", "mas info", "más", "mas", "ver", "saber más",
                "saber mas", "read more", "here", "learn more", "more", "click here"}
    links = []
    poor_anchor = 0
    for a in soup.find_all("a", href=True):
        h = urljoin(url, a["href"]).split("#")[0]
        if _valid(h, base_net):
            links.append(h)
            txt = a.get_text(" ", strip=True).lower()
            if txt in _GENERIC or (0 < len(txt) <= 2):
                poor_anchor += 1
    links = list(dict.fromkeys(links))

    # --- Schema (ANTES de eliminar los <script>) + fechas ---
    schema_types = []
    schema_bad = 0
    schema_incomplete = []
    raw_ld = ""
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = s.get_text() or ""
        raw_ld += raw + "\n"
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            schema_bad += 1
            # fallback: aún así capturamos los @type por texto (str y elementos de array)
            for m in re.findall(r'"@type"\s*:\s*(\[[^\]]*\]|"[^"]+")', raw):
                for t in re.findall(r'"([^"]+)"', m):
                    schema_types.append(t.strip())
        else:
            for node in _flatten_ld(data):
                # @type puede ser string ("LocalBusiness") o lista (["LocalBusiness","Store"])
                t = node.get("@type") if isinstance(node, dict) else None
                if isinstance(t, str):
                    schema_types.append(t.strip())
                elif isinstance(t, list):
                    schema_types += [str(x).strip() for x in t if isinstance(x, (str, int))]
                miss = _missing_req(node)
                if miss:
                    schema_incomplete.append(miss)

    # breadcrumbs: schema BreadcrumbList o navegación de migas visible
    schema_low0 = [t.lower() for t in schema_types]
    breadcrumb = "breadcrumblist" in schema_low0
    if not breadcrumb:
        try:
            breadcrumb = bool(soup.select_one(
                'nav[aria-label*="bread" i], ol[class*="breadcrumb" i], '
                'ul[class*="breadcrumb" i], [class*="breadcrumb" i]'))
        except Exception:  # noqa: BLE001
            breadcrumb = False

    page_date = _page_date(soup, raw_ld)

    # --- Señales locales / contacto (NAP + mapa + horario), en TODA la página ---
    html_low0 = (html or "").lower()
    l_phone = (bool(soup.find("a", href=re.compile(r"^tel:", re.I)))
               or bool(soup.find("a", href=re.compile(r"(?:wa\.me|api\.whatsapp\.com|whatsapp://)", re.I)))
               or bool(re.search(r"(?:\+|\b00)\s?\d[\d\s().\-]{6,}\d", html_low0))
               or bool(re.search(r"(tel[eé]fono|tel[eé]f?\.|ll[áa]ma\w*|phone|m[óo]vil|celular|"
                                 r"whatsapp|contacto)[^0-9]{0,40}\d[\d\s().\-]{6,}\d", html_low0)))
    l_hours = ("openinghours" in html_low0 or "opening_hours" in html_low0)
    l_geo = ("geocoordinates" in schema_low0 or '"latitude"' in html_low0)
    # Mapa: solo cuenta un iframe de mapa incrustado (un enlace a Maps NO es un mapa).
    l_map = bool(soup.find("iframe", src=re.compile(r"google\.[a-z.]+/maps|maps\.google|/maps/embed|openstreetmap|mapbox", re.I)))
    # Dirección por schema: exige un VALOR real de calle (no solo el @type ni la clave
    # vacía). El resto (texto/pie) se resuelve más abajo con contexto estricto.
    _ip_addr = soup.find(attrs={"itemprop": re.compile("streetAddress", re.I)})
    l_addr = (bool(re.search(r'"streetaddress"\s*:\s*"[^"]{4,}"', html_low0))
              or (bool(_ip_addr) and len((_ip_addr.get_text(strip=True) or "")) >= 4))
    # Testimonios/opiniones VISIBLES en la web (distinto del marcado Schema Review):
    # por clase/id de widget o por frases típicas de sección de testimonios.
    # clase/id: exige que el término empiece el token o vaya tras separador, para no
    # confundir "overview"/"preview" (contienen "review") con una sección de opiniones.
    _test_cls = re.compile(r"(?:^|[-_ ])(testimoni|reviews?|opinion|rese[nñ]a|valorac)", re.I)
    has_testimonials = (
        bool(re.search(r"testimoni|lo que dicen (?:nuestros )?(?:clientes|usuarios)|"
                       r"opiniones de (?:nuestros )?clientes|nuestros clientes opinan|"
                       r"rese[nñ]as de (?:nuestros )?clientes|clientes satisfechos|"
                       r"trustpilot|elfsight|google-reviews|reviews-widget|senja\.io", html_low0))
        or bool(soup.find(attrs={"class": _test_cls}))
        or bool(soup.find(attrs={"id": _test_cls})))

    # --- Texto visible (ahora sí elimina scripts/estilos) ---
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    word_count = len(text.split())
    # Dirección real (precisión ante todo, para NO marcar dirección en falso a partir
    # de prosa de blog): SOLO cuenta si hay una vía+número en una etiqueta <address>,
    # o en el PIE de página junto a un código postal. El texto suelto del cuerpo NO
    # cuenta (cualquier "Calle... 2024" daba dirección en falso, p.ej. en ainoa).
    if not l_addr:
        _street_re = re.compile(
            r"\b(c/|calle|avda?\.?|avenida|carrera|cra\.?|cll\.?|diagonal|transversal|"
            r"pol[íi]gono|carrer|r[úu]a|jir[óo]n|jr\.?|street|road|avenue|ave\.?|blvd|"
            r"boulevard)\b[^\d\n]{0,25}\d{1,4}", re.I)
        _cp_re = re.compile(r"\b\d{4,6}\b")
        try:
            # <address> con una vía+número: señal clara y semántica.
            for adr in soup.find_all("address"):
                if _street_re.search(adr.get_text(" ", strip=True) or ""):
                    l_addr = True
                    break
            # pie de página: vía+número Y código postal en el MISMO pie (dirección fiscal).
            if not l_addr:
                for foot in soup.find_all("footer"):
                    ft = foot.get_text(" ", strip=True) or ""
                    if _street_re.search(ft) and _cp_re.search(ft):
                        l_addr = True
                        break
        except Exception:  # noqa: BLE001
            pass

    lang = ""
    htmltag = soup.find("html")
    if htmltag:
        lang = (htmltag.get("lang") or "").strip().lower()

    path, query = urlparse(url).path, urlparse(url).query
    url_issues = []
    if len(url) > 100:
        url_issues.append("larga")
    if query:
        url_issues.append("parámetros")
    if re.search(r"[A-Z]", path):
        url_issues.append("mayúsculas")
    if "_" in path:
        url_issues.append("guiones bajos")

    can_self = None
    if canonical:
        can_self = _norm(urljoin(url, canonical)) == _norm(url)

    return {
        "url": url, "status": status,
        "title": title, "title_len": len(title),
        "desc": desc, "desc_len": len(desc),
        "og_ok": bool(og_title and og_image), "twitter": twitter,
        "hreflangs": hreflangs, "lang": lang,
        "h1_count": len(h1s), "h1_first": h1s[0] if h1s else "",
        "h2_count": h2_count, "h3_count": h3_count,
        "canonical": canonical, "canonical_self": can_self, "noindex": noindex,
        "img_total": img_total, "img_no_alt": img_no_alt,
        "word_count": word_count,
        "schema_types": sorted(set(t.lower() for t in schema_types)),
        "schema_bad": schema_bad,
        "schema_incomplete": schema_incomplete,
        "date": page_date,
        "_text": text[:6000],
        "url_issues": url_issues,
        "int_links": len(links),
        "poor_anchor": poor_anchor,
        "breadcrumb": breadcrumb,
        "l_phone": l_phone, "l_addr": l_addr, "l_map": l_map,
        "l_hours": l_hours, "l_geo": l_geo,
        "has_testimonials": has_testimonials,
        "_links": links,
    }


async def _status(client, url, sem):
    """Código HTTP real de una URL. Reintenta una vez; los errores de red se marcan
    None (NO son 404) para no acusar en falso."""
    async with sem:
        for attempt in range(2):
            try:
                r = await client.get(url, timeout=BROKEN_TIMEOUT, follow_redirects=True)
                return (url, r.status_code)
            except Exception:  # noqa: BLE001
                if attempt == 0:
                    await asyncio.sleep(0.4)
                    continue
                return (url, None)


async def _check_broken(links: list[str]) -> dict:
    """Comprueba el estado de TODAS las URLs internas descubiertas (de pies a cabeza)."""
    if not links:
        return {"checked": 0, "broken": [], "count": 0, "errors": 0}
    sem = asyncio.Semaphore(BROKEN_CONC)
    try:
        async with httpx.AsyncClient(headers=_HEADERS, verify=False, follow_redirects=True,
                                     limits=httpx.Limits(max_connections=BROKEN_CONC + 2)) as c:
            res = await asyncio.gather(*(_status(c, u, sem) for u in links))
    except Exception:  # noqa: BLE001
        return {"checked": 0, "broken": [], "count": 0, "errors": 0}
    # Códigos que NO son enlace roto: piden auth, bloquean bots o limitan tasa. Contarlos
    # como 404 es un falso positivo clásico (401/403 = protegido, 429 = límite, 999 = LinkedIn).
    _NOT_BROKEN = {401, 403, 405, 429, 451, 503, 999}
    broken, checked, errors = [], 0, 0
    for u, st in res:
        if st is None:
            errors += 1
            continue
        checked += 1
        if st >= 400 and st not in _NOT_BROKEN:
            broken.append({"url": u, "status": st})
    broken.sort(key=lambda b: b["url"])
    return {"checked": checked, "broken": broken, "count": len(broken), "errors": errors}


async def _fetch(client, url, sem, base_net):
    async with sem:
        try:
            r = await client.get(url, timeout=PAGE_TIMEOUT, follow_redirects=True)
            if "html" not in (r.headers.get("content-type") or "").lower():
                return None
            return _parse_page(str(r.url), r.text, r.status_code, base_net)
        except Exception:  # noqa: BLE001
            return None


# --------------------------------------------------------------------------- #
# Sitemap (siembra del crawl)
# --------------------------------------------------------------------------- #
async def _sitemap_urls(client, home_url) -> list[str]:
    pr = urlparse(home_url)
    base = f"{pr.scheme}://{pr.netloc}"
    locs: list[str] = []
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        try:
            r = await client.get(base + path, timeout=PAGE_TIMEOUT, follow_redirects=True)
        except Exception:  # noqa: BLE001
            continue
        if r.status_code != 200 or "<" not in r.text:
            continue
        found = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        if "<sitemapindex" in r.text.lower():
            for child in found[:5]:
                try:
                    rc = await client.get(child, timeout=PAGE_TIMEOUT, follow_redirects=True)
                    locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", rc.text)
                except Exception:  # noqa: BLE001
                    pass
                if len(locs) >= SITEMAP_CAP:
                    break
        else:
            locs += found
        if locs:
            break
    return locs[:SITEMAP_CAP]


# --------------------------------------------------------------------------- #
# Agregación
# --------------------------------------------------------------------------- #
def _aggregate(pages: list[dict], norm_home: str = "") -> dict:
    def ex(items, n=5):
        return items[:n]

    title_missing, title_bad, h1_missing, h1_multi, h2_missing = [], [], [], [], []
    desc_missing, desc_bad, og_missing = [], [], []
    thin, canon_missing, canon_other, noindex, no_schema, url_bad, img_alt = [], [], [], [], [], [], []
    orphans, deep_pages = [], []
    titles, descs = {}, {}
    total_imgs = total_missing_alt = words_sum = 0
    hreflang_pages = breadcrumb_pages = poor_anchor_total = 0

    for p in pages:
        u = p["url"]
        if not p["title"]:
            title_missing.append(u)
        else:
            titles.setdefault(p["title"].strip().lower(), []).append(u)
            if not (TITLE_MIN <= p["title_len"] <= TITLE_MAX):
                title_bad.append({"url": u, "len": p["title_len"]})
        if not p["desc"]:
            desc_missing.append(u)
        else:
            descs.setdefault(p["desc"].strip().lower(), []).append(u)
            if not (DESC_MIN <= p["desc_len"] <= DESC_MAX):
                desc_bad.append({"url": u, "len": p["desc_len"]})
        if not p["og_ok"]:
            og_missing.append(u)
        if p["h1_count"] == 0:
            h1_missing.append(u)
        elif p["h1_count"] > 1:
            h1_multi.append({"url": u, "n": p["h1_count"]})
        if p["h2_count"] == 0:
            h2_missing.append(u)
        if p["word_count"] < THIN_WORDS:
            thin.append({"url": u, "words": p["word_count"]})
        if not p["canonical"]:
            canon_missing.append(u)
        elif p["canonical_self"] is False:
            canon_other.append({"url": u, "to": p["canonical"]})
        if p["noindex"]:
            noindex.append(u)
        if not p["schema_types"]:
            no_schema.append(u)
        if p["url_issues"]:
            url_bad.append({"url": u, "why": p["url_issues"]})
        if p["img_no_alt"] > 0:
            img_alt.append({"url": u, "missing": p["img_no_alt"], "total": p["img_total"]})
        if p["hreflangs"]:
            hreflang_pages += 1
        if p.get("breadcrumb"):
            breadcrumb_pages += 1
        poor_anchor_total += p.get("poor_anchor", 0)
        # arquitectura: huérfanas (nadie las enlaza) y profundidad de clics
        k = _norm(p["url"])
        if k != norm_home:
            if p.get("inbound", 0) == 0:
                orphans.append(u)
            if isinstance(p.get("depth"), int) and p["depth"] > 3:
                deep_pages.append({"url": u, "depth": p["depth"]})
        total_imgs += p["img_total"]
        total_missing_alt += p["img_no_alt"]
        words_sum += p["word_count"]

    dup_titles = [{"value": t, "urls": us} for t, us in titles.items() if len(us) > 1]
    dup_descs = [{"value": d, "urls": us} for d, us in descs.items() if len(us) > 1]

    # Datos estructurados (schema) agregados de TODO el sitio
    schema_all: dict = {}
    pages_with_schema = 0
    schema_errors = 0
    incomplete: dict = {}
    for p in pages:
        ts = p.get("schema_types") or []
        if ts:
            pages_with_schema += 1
        for t in ts:
            schema_all[t] = schema_all.get(t, 0) + 1
        schema_errors += p.get("schema_bad", 0)
        for it in (p.get("schema_incomplete") or []):
            incomplete.setdefault(it["type"], it["missing"])
    schema_info = {"types": sorted(schema_all.keys()), "counts": schema_all,
                   "pages_with": pages_with_schema, "pages": len(pages),
                   "errors": schema_errors,
                   "incomplete": [{"type": k, "missing": v} for k, v in incomplete.items()]}

    n = len(pages) or 1
    issues = {
        "title_missing": {"count": len(title_missing), "examples": ex(title_missing)},
        "title_dup": {"count": len(dup_titles), "groups": ex(dup_titles, 3)},
        "title_bad_len": {"count": len(title_bad), "examples": ex(title_bad)},
        "desc_missing": {"count": len(desc_missing), "examples": ex(desc_missing)},
        "desc_dup": {"count": len(dup_descs), "groups": ex(dup_descs, 3)},
        "desc_bad_len": {"count": len(desc_bad), "examples": ex(desc_bad)},
        "og_missing": {"count": len(og_missing), "examples": ex(og_missing)},
        "h1_missing": {"count": len(h1_missing), "examples": ex(h1_missing)},
        "h1_multiple": {"count": len(h1_multi), "examples": ex(h1_multi)},
        "h2_missing": {"count": len(h2_missing), "examples": ex(h2_missing)},
        "thin": {"count": len(thin), "examples": ex(thin)},
        "canonical_missing": {"count": len(canon_missing), "examples": ex(canon_missing)},
        "canonical_other": {"count": len(canon_other), "examples": ex(canon_other)},
        "noindex": {"count": len(noindex), "examples": ex(noindex)},
        "no_schema": {"count": len(no_schema), "examples": ex(no_schema)},
        "url_unfriendly": {"count": len(url_bad), "examples": ex(url_bad)},
        "img_no_alt": {"pages": len(img_alt), "total": total_missing_alt,
                       "total_imgs": total_imgs, "examples": ex(img_alt)},
        "orphans": {"count": len(orphans), "examples": ex(orphans)},
        "deep": {"count": len(deep_pages), "examples": ex(deep_pages)},
        "breadcrumbs": {"present": breadcrumb_pages > 0, "pages": breadcrumb_pages},
        "poor_anchor": {"count": poor_anchor_total},
    }

    def frac(c):
        return c / n

    score = 100.0
    score -= 26 * frac(len(title_missing))
    score -= 8 * frac(len(title_bad))
    score -= 8 * min(1.0, len(dup_titles) / n)
    score -= 14 * frac(len(desc_missing))
    score -= 5 * frac(len(desc_bad))
    score -= 5 * min(1.0, len(dup_descs) / n)
    score -= 6 * frac(len(og_missing))
    score -= 18 * frac(len(h1_missing))
    score -= 5 * frac(len(h1_multi))
    score -= 5 * frac(len(h2_missing))
    score -= 12 * frac(len(thin))
    score -= 9 * frac(len(canon_missing))
    score -= 7 * frac(len(canon_other))
    score -= 20 * frac(len(noindex))
    score -= 10 * (total_missing_alt / total_imgs if total_imgs else 0)
    score -= 5 * frac(len(url_bad))
    score -= 14 * frac(len(orphans))            # huérfanas: sin enlaces internos
    score -= 6 * frac(len(deep_pages))          # demasiado profundas (>3 clics)
    if breadcrumb_pages == 0 and n >= 3:
        score -= 5                               # sin breadcrumbs en todo el sitio
    score = max(0, min(100, round(score)))

    # Señales locales/contacto agregadas en TODAS las páginas rastreadas (no solo la home)
    local = {
        "has_phone": any(p.get("l_phone") for p in pages),
        "has_address": any(p.get("l_addr") for p in pages),
        "has_map": any(p.get("l_map") for p in pages),
        "has_hours": any(p.get("l_hours") for p in pages),
        "has_geo": any(p.get("l_geo") for p in pages),
        "has_testimonials": any(p.get("has_testimonials") for p in pages),
    }

    return {
        "issues": issues,
        "totals": {
            "pages": len(pages),
            "avg_words": round(words_sum / n),
            "img_total": total_imgs,
            "img_no_alt": total_missing_alt,
            "hreflang_pages": hreflang_pages,
            "breadcrumb_pages": breadcrumb_pages,
            "orphans": len(orphans),
        },
        "schema": schema_info,
        "local": local,
        "score": score,
    }


def _content(pages: list) -> dict:
    """Análisis de contenido: profundidad, casi-duplicados, frescura, términos,
    coherencia de tema y señales de confianza (E-E-A-T). Todo con el texto rastreado."""
    from collections import Counter  # noqa: PLC0415
    import datetime as _dt  # noqa: PLC0415
    n = len(pages) or 1
    toks_by = [_c_tokens(p.get("_text", "")) for p in pages]

    docfreq = Counter()
    for tk in toks_by:
        docfreq.update(set(tk))
    top_terms = [w for w, _ in docfreq.most_common(12)]

    sh = [_shingles(tk) for tk in toks_by]
    dups = []
    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            a, b = sh[i], sh[j]
            if len(a) < 20 or len(b) < 20:
                continue
            inter = len(a & b)
            uni = len(a | b)
            if uni and inter / uni >= 0.6:
                dups.append({"a": pages[i]["url"], "b": pages[j]["url"], "sim": round(inter / uni, 2)})
    dups.sort(key=lambda d: d["sim"], reverse=True)

    coherent = 0
    for p, tk in zip(pages, toks_by):
        ptop = [w for w, _ in Counter(tk).most_common(5)]
        blob = (p.get("title", "") + " " + p.get("h1_first", "")).lower()
        if ptop and any(w in blob for w in ptop):
            coherent += 1

    dated = [p["date"] for p in pages if p.get("date")]
    newest = max(dated) if dated else ""
    fresh = 0
    if dated:
        try:
            cutoff = (_dt.date.today() - _dt.timedelta(days=400)).isoformat()
            fresh = sum(1 for d in dated if d >= cutoff)
        except Exception:  # noqa: BLE001
            fresh = 0

    urls = [p["url"].lower() for p in pages]
    about = any(re.search(r"/(about|sobre|nosotros|quienes|equipo|team|adn|empresa|"
                          r"conocenos|conócenos|historia|company|acerca)(/|$)", u)
                for u in urls)
    # No todas las páginas "sobre nosotros" viven en una URL con esa palabra (p. ej.
    # /marca/). También la reconocemos por el título/H1 o por texto de "quiénes somos".
    if not about:
        _about_rx = re.compile(
            r"(sobre\s+nosotros|qui[eé]nes\s+somos|acerca\s+de|nuestra\s+(empresa|historia|"
            r"misi[óo]n|filosof[íi]a|compañ[íi]a)|nuestro\s+equipo|con[óo]cenos|about\s+us|"
            r"who\s+we\s+are|our\s+(story|company|team|mission))", re.I)
        _found_rx = re.compile(r"(desde|fundad[ao]s?\s+en|founded\s+in|est\.\s*)\s*(19|20)\d{2}", re.I)
        for p in pages:
            blob = (p.get("title") or "") + " " + (p.get("h1_first") or "")
            body = p.get("_text") or ""
            if _about_rx.search(blob) or _about_rx.search(body) or _found_rx.search(body):
                about = True
                break
    author = any(any(t in (p.get("schema_types") or []) for t in ("author", "person")) for p in pages)

    # estructura por temas (pilar + clusters): agrupa por primera carpeta de la URL
    segs: dict = {}
    for p in pages:
        path = urlparse(p["url"]).path.strip("/")
        seg = path.split("/")[0] if path else "(home)"
        segs[seg] = segs.get(seg, 0) + 1
    clusters = sum(1 for s, c in segs.items() if s not in ("", "(home)") and c >= 3)
    sections = len([s for s in segs if s not in ("", "(home)")])

    return {
        "avg_words": round(sum(p["word_count"] for p in pages) / n),
        "thin": sum(1 for p in pages if p["word_count"] < THIN_WORDS),
        "top_terms": top_terms,
        "duplicates": {"count": len(dups), "examples": dups[:4]},
        "coherence_pct": round(100 * coherent / n),
        "dated_pages": len(dated), "fresh_pages": fresh, "newest": newest, "pages": len(pages),
        "about_page": about, "author": author,
        "clusters": clusters, "sections": sections,
    }


# --------------------------------------------------------------------------- #
# Entrada principal — crawl BFS
# --------------------------------------------------------------------------- #
async def audit(url: str, home_html: str | None = None,
                extra_urls: list[str] | None = None) -> dict | None:
    base_net = urlparse(url).netloc
    try:
        async with httpx.AsyncClient(headers=_HEADERS, verify=False,
                                     limits=httpx.Limits(max_connections=CONCURRENCY + 2)) as client:
            if home_html is None:
                try:
                    r = await client.get(url, timeout=PAGE_TIMEOUT, follow_redirects=True)
                    home_html, url = r.text, str(r.url)
                    base_net = urlparse(url).netloc
                except Exception:  # noqa: BLE001
                    return None

            # ---- siembra: home + sitemap + enlaces del home + extra
            seen = set()
            queue: deque[str] = deque()

            def enqueue(u: str):
                n = _norm(u)
                if n and n not in seen and _valid(u, base_net):
                    seen.add(n)
                    queue.append(u)

            seen.add(_norm(url))
            queue.append(url)  # home primero
            for u in await _sitemap_urls(client, url):
                enqueue(u)
            if home_html:
                hs = BeautifulSoup(home_html, "html.parser")
                for a in hs.find_all("a", href=True):
                    enqueue(urljoin(url, a["href"]).split("#")[0])
            for u in (extra_urls or []):
                enqueue(u)

            # ---- crawl por rondas (BFS) hasta MAX_PAGES o el presupuesto de tiempo
            sem = asyncio.Semaphore(CONCURRENCY)
            pages, final_seen = [], set()
            _crawl_t0 = time.perf_counter()
            while queue and len(pages) < MAX_PAGES and (time.perf_counter() - _crawl_t0) < CRAWL_BUDGET:
                take = min(CONCURRENCY, len(queue), MAX_PAGES - len(pages))
                batch = [queue.popleft() for _ in range(take)]
                results = await asyncio.gather(*(_fetch(client, u, sem, base_net) for u in batch))
                for p in results:
                    if not p:
                        continue
                    key = _norm(p["url"])
                    if key in final_seen:
                        continue
                    final_seen.add(key)
                    for l in p.get("_links", []):
                        enqueue(l)
                    pages.append(p)
    except Exception:  # noqa: BLE001
        return None

    if not pages:
        return None

    # ---- Grafo de enlaces internos: profundidad de clics, inbound y huérfanas
    norm_home = _norm(url)
    nodes = {_norm(p["url"]): p for p in pages}
    adj = {k: set() for k in nodes}
    all_links: dict = {}   # norm -> url original (todos los enlaces internos descubiertos)
    for p in pages:
        src = _norm(p["url"])
        for l in p.get("_links", []):
            lu = l.split("#")[0]
            ln = _norm(lu)
            all_links.setdefault(ln, lu)
            if ln in nodes and ln != src:
                adj[src].add(ln)
    inbound = {k: 0 for k in nodes}
    for src, tgts in adj.items():
        for t in tgts:
            inbound[t] += 1
    depth = {k: None for k in nodes}
    if norm_home in nodes:
        dq = deque([norm_home])
        depth[norm_home] = 0
        while dq:
            cur = dq.popleft()
            for t in adj.get(cur, ()):
                if depth[t] is None:
                    depth[t] = depth[cur] + 1
                    dq.append(t)
    for p in pages:
        k = _norm(p["url"])
        p["inbound"] = inbound.get(k, 0)
        p["depth"] = depth.get(k)
        p.pop("_links", None)

    # ---- 404 de TODO el sitio: comprueba cada enlace interno descubierto que no
    # sea una página ya rastreada (esas ya devolvieron 200).
    known_ok = set(nodes.keys())
    to_check = [u for n, u in all_links.items()
                if n not in known_ok and not _FACETED_RE.search(u)][:BROKEN_CAP]
    broken = await _check_broken(to_check)
    # suma las páginas rastreadas como comprobadas (son 200)
    broken["checked"] = broken.get("checked", 0) + len(pages)

    model = _aggregate(pages, norm_home)
    model["broken"] = broken
    try:
        model["content"] = _content(pages)
    except Exception:  # noqa: BLE001
        model["content"] = None
    for p in pages:
        p.pop("_text", None)
    model["pages"] = pages
    model["engine"] = "onpage"
    return model


if __name__ == "__main__":
    import json
    import sys

    _url = sys.argv[1] if len(sys.argv) > 1 else "https://cupperlab.com"
    _m = asyncio.run(audit(_url))
    if _m:
        print(json.dumps({k: v for k, v in _m.items() if k != "pages"}, indent=2, ensure_ascii=False))
        print("\nPáginas analizadas:", len(_m["pages"]))
        for _p in _m["pages"]:
            print(f'  {_p["status"]} · {_p["word_count"]}w · T{_p["title_len"]} · H1x{_p["h1_count"]} H2x{_p["h2_count"]} · {_p["url"]}')
    else:
        print("None")
