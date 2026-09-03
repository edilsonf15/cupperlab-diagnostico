"""
Motor de diagnostico rapido SEO + GEO de Cupperlab.

Corre en vivo, con presupuesto de tiempo (<50 s), lo que se puede medir sin
accesos: salud tecnica, on-page y preparacion para la IA (GEO/LLMO). No inventa
nada: cada senal se comprueba contra el dominio real. Lo que no se puede medir
sin accesos (PageSpeed sin API key, indexacion exacta) se marca como pendiente.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

# ---- Presupuestos de tiempo (segundos) ---------------------------------------
TOTAL_BUDGET = float(os.getenv("ANALYSIS_TOTAL_BUDGET", "45"))
HOME_TIMEOUT = 12.0
FILE_TIMEOUT = 8.0
LINK_TIMEOUT = 9.0
PSI_TIMEOUT = 60.0
AI_WAIT = float(os.getenv("AI_GEO_WAIT", "26"))
LINK_SAMPLE = 14          # URLs de sitemap a muestrear para 404
LINK_CONCURRENCY = 8

UA = (
    "Mozilla/5.0 (compatible; CupperlabDiagnostico/1.0; +https://cupperlab.com) "
    "AppleWebKit/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.6"}

GEO_SCHEMA_TYPES = {
    "organization", "localbusiness", "website", "webpage", "product",
    "faqpage", "article", "breadcrumblist", "service", "professionalservice",
}


# ---- Utilidades --------------------------------------------------------------

def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    p = urlparse(raw)
    netloc = p.netloc or p.path
    return f"{p.scheme}://{netloc}".rstrip("/")


def domain_of(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


@dataclass
class Check:
    key: str
    label: str
    earned: float
    possible: float
    ok: bool
    detail: str = ""


@dataclass
class Result:
    url: str = ""
    final_url: str = ""
    domain: str = ""
    reachable: bool = False
    error: str = ""
    analyzed_at: str = ""
    elapsed: float = 0.0
    score: int = 0
    grade: str = ""
    categories: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    signals: dict = field(default_factory=dict)
    findings_good: list = field(default_factory=list)
    findings_improve: list = field(default_factory=list)
    psi: dict | None = None
    geo_ai: dict | None = None
    authority: dict | None = None


# ---- Fetchers ----------------------------------------------------------------

async def _get(client: httpx.AsyncClient, url: str, timeout: float, method: str = "GET"):
    try:
        t0 = time.perf_counter()
        r = await client.request(method, url, timeout=timeout, follow_redirects=True)
        return r, time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001
        return exc, 0.0


async def fetch_home(client: httpx.AsyncClient, url: str):
    r, dt = await _get(client, url, HOME_TIMEOUT)
    if isinstance(r, Exception):
        # reintento sobre http:// si https falla
        alt = url.replace("https://", "http://", 1)
        r, dt = await _get(client, alt, HOME_TIMEOUT)
        if isinstance(r, Exception):
            return None, str(r), 0.0
    return r, "", dt


async def fetch_text(client: httpx.AsyncClient, url: str):
    r, _ = await _get(client, url, FILE_TIMEOUT)
    if isinstance(r, Exception):
        return None, None
    return r.status_code, (r.text if r.status_code < 400 else None)


async def check_status(client: httpx.AsyncClient, url: str):
    r, _ = await _get(client, url, LINK_TIMEOUT, method="GET")
    if isinstance(r, Exception):
        return url, None            # error de red, NO es un 404 real
    return url, r.status_code


# ---- Parsers ----------------------------------------------------------------

def parse_home(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""

    def meta(name=None, prop=None):
        if name:
            tag = soup.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)})
        else:
            tag = soup.find("meta", attrs={"property": re.compile(f"^{prop}$", re.I)})
        return (tag.get("content") or "").strip() if tag else ""

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = soup.find_all("h2")
    h3s = soup.find_all("h3")

    # hreflang / idiomas
    hreflangs = []
    for lk in soup.find_all("link", attrs={"rel": re.compile("alternate", re.I)}):
        hl = lk.get("hreflang")
        if hl:
            hreflangs.append(hl.strip().lower())
    hreflangs = sorted(set(hreflangs))

    # cobertura de ALT en imagenes
    imgs = soup.find_all("img")
    img_total = len(imgs)
    img_alt = sum(1 for i in imgs if (i.get("alt") or "").strip())

    # JSON-LD structured data
    schema_types = []
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = s.string or s.get_text() or ""
        for m in re.findall(r'"@type"\s*:\s*"([^"]+)"', raw):
            schema_types.append(m.strip())
    has_sameas = bool(re.search(r'"sameAs"', html or ""))

    html_tag = soup.find("html")
    lang = (html_tag.get("lang") if html_tag else "") or ""

    canonical_tag = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    canonical = canonical_tag.get("href") if canonical_tag else ""

    favicon = bool(soup.find("link", attrs={"rel": re.compile("icon", re.I)}))
    viewport = meta(name="viewport")

    text = soup.get_text(" ", strip=True)
    word_count = len(text.split())

    schema_low = [t.lower() for t in schema_types]
    # FAQ / Q&A: schema FAQPage o varios titulares en forma de pregunta
    heading_texts = [h.get_text(strip=True) for h in (h2s + h3s)]
    q_headings = sum(1 for t in heading_texts if t.endswith("?"))
    has_faq = ("faqpage" in schema_low) or ("qapage" in schema_low) or q_headings >= 2
    # Ficha de contacto (NAP): tel: link, schema de contacto/direccion o telefono real en texto
    has_contact = (
        bool(soup.find("a", href=re.compile(r"^tel:", re.I)))
        or any(x in schema_low for x in ("contactpoint", "postaladdress", "localbusiness"))
        or bool(re.search(r"(?:\+|\b00)\s?\d[\d\s().\-]{6,}\d", text))
    )

    return {
        "title": title,
        "description": meta(name="description"),
        "h1_count": len(h1s),
        "h1_first": h1s[0] if h1s else "",
        "h2_count": len(h2s),
        "lang": lang.strip(),
        "h3_count": len(h3s),
        "hreflangs": hreflangs,
        "img_total": img_total,
        "img_alt": img_alt,
        "canonical": bool(canonical),
        "favicon": favicon,
        "viewport": bool(viewport),
        "og_title": meta(prop="og:title"),
        "og_image": meta(prop="og:image"),
        "og_desc": meta(prop="og:description"),
        "og_site_name": meta(prop="og:site_name"),
        "twitter": bool(meta(name="twitter:card")),
        "schema_types": sorted(set(t.lower() for t in schema_types)),
        "schema_raw_types": sorted(set(schema_types)),
        "has_sameas": has_sameas,
        "word_count": word_count,
        "has_faq": has_faq,
        "has_contact": has_contact,
    }


def parse_sitemap_locs(xml_text: str) -> tuple[list[str], bool]:
    """Devuelve (locs, is_index)."""
    locs = []
    is_index = False
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "ignore"))
        tag = root.tag.lower()
        is_index = tag.endswith("sitemapindex")
        for loc in root.iter():
            if loc.tag.lower().endswith("loc") and loc.text:
                locs.append(loc.text.strip())
    except Exception:  # noqa: BLE001
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text or "")
        is_index = "<sitemapindex" in (xml_text or "").lower()
    return locs, is_index


def categorize_sitemap(locs: list[str]) -> dict:
    """Clasifica las URLs del sitemap por tipo (como la skill): paginas reales vs
    etiquetas/tags vs fichas/descargas vs noticias/entradas."""
    comp = {"paginas": 0, "etiquetas": 0, "fichas": 0, "entradas": 0, "otras": 0}
    for u in locs:
        p = u.lower()
        if re.search(r"/(tag|tags|etiqueta|categoria|category|author|autor)/", p):
            comp["etiquetas"] += 1
        elif re.search(r"(wpfd_file|/descargas?/|/downloads?/|\.pdf$|/documento)", p):
            comp["fichas"] += 1
        elif re.search(r"/(blog|noticias?|news|actualidad|post|\d{4}/\d{2})/", p):
            comp["entradas"] += 1
        elif re.search(r"/(feed|comments|attachment)/", p):
            comp["otras"] += 1
        else:
            comp["paginas"] += 1
    comp["total"] = len(locs)
    return comp


# ---- Deteccion de pais real (por contenido, NO solo por TLD) -----------------

# Prefijos telefonicos -> (pais, codigo gl). Orden: mas largos primero.
_CALL_CODES = [
    ("593", "Ecuador", "ec"), ("591", "Bolivia", "bo"), ("595", "Paraguay", "py"),
    ("598", "Uruguay", "uy"), ("502", "Guatemala", "gt"), ("503", "El Salvador", "sv"),
    ("504", "Honduras", "hn"), ("505", "Nicaragua", "ni"), ("506", "Costa Rica", "cr"),
    ("507", "Panamá", "pa"), ("509", "Haití", "ht"), ("351", "Portugal", "pt"),
    ("52", "México", "mx"), ("57", "Colombia", "co"), ("54", "Argentina", "ar"),
    ("56", "Chile", "cl"), ("51", "Perú", "pe"), ("58", "Venezuela", "ve"),
    ("55", "Brasil", "br"), ("34", "España", "es"), ("44", "Reino Unido", "gb"),
    ("33", "Francia", "fr"), ("39", "Italia", "it"), ("49", "Alemania", "de"),
    ("1", "Estados Unidos", "us"),
]

# Menciones de pais/ciudad como respaldo (cuando no hay telefono claro).
_GEO_HINTS = {
    "es": ("España", ["españa", "madrid", "barcelona", "valencia", "sevilla", "málaga", "€", " iva", "cif ", " dni"]),
    "mx": ("México", ["méxico", "mexico", "cdmx", "guadalajara", "monterrey", "puebla", "querétaro", " rfc", " mxn"]),
    "co": ("Colombia", ["colombia", "bogotá", "bogota", "medellín", "medellin", "cali", "barranquilla", " nit", " cop"]),
    "ar": ("Argentina", ["argentina", "buenos aires", "córdoba", "rosario", "mendoza", " cuit", " ars"]),
    "cl": ("Chile", ["chile", "santiago", "valparaíso", "concepción", " rut", " clp"]),
    "pe": ("Perú", ["perú", "peru", "lima", "arequipa", "trujillo", " ruc", " pen", "soles"]),
    "pa": ("Panamá", ["panamá", "panama", "ciudad de panamá"]),
    "us": ("Estados Unidos", ["united states", "u.s.a", "new york", "miami", "los angeles", "texas", "california"]),
}


def detect_country(html: str, domain: str) -> dict:
    """Detecta el pais REAL de operacion mirando el contenido de la pagina
    (telefono del footer, menciones de pais/ciudad, moneda). El TLD es el ultimo
    recurso. Devuelve {name, gl, source}."""
    h = (html or "")
    low = h.lower()

    # 1) Telefonos REALES: prefijo internacional + un numero de 8-15 digitos.
    #    (evita falsos positivos con "+100", "+1.000", "+50%", etc. de marketing)
    votes: dict[tuple, int] = {}
    for m in re.finditer(r"(?:\+|\b00)\s?(\d[\d\s().\-]{6,}\d)", h):
        digs = re.sub(r"\D", "", m.group(1))
        if not (8 <= len(digs) <= 15):
            continue
        for code, name, gl in _CALL_CODES:
            if digs.startswith(code):
                votes[(name, gl)] = votes.get((name, gl), 0) + 1
                break
    if votes:
        (name, gl), _ = max(votes.items(), key=lambda kv: kv[1])
        return {"name": name, "gl": gl, "source": "telefono"}

    # 2) Menciones de pais/ciudad/moneda
    hint_votes: dict[str, int] = {}
    for gl, (name, kws) in _GEO_HINTS.items():
        c = sum(low.count(kw) for kw in kws)
        if c:
            hint_votes[gl] = c
    if hint_votes:
        gl = max(hint_votes, key=hint_votes.get)
        return {"name": _GEO_HINTS[gl][0], "gl": gl, "source": "contenido"}

    # 3) TLD de pais como ultimo recurso
    host = (domain or "").lower().split("/")[0]
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    tld_map = {"es": ("España", "es"), "mx": ("México", "mx"), "co": ("Colombia", "co"),
               "ar": ("Argentina", "ar"), "cl": ("Chile", "cl"), "pe": ("Perú", "pe"),
               "pa": ("Panamá", "pa"), "pt": ("Portugal", "pt")}
    if tld in tld_map:
        return {"name": tld_map[tld][0], "gl": tld_map[tld][1], "source": "tld"}
    return {"name": "", "gl": "", "source": "desconocido"}


# ---- Analisis de robots.txt (que bloquea, que conviene bloquear) -------------

def analyze_robots(robots_text: str) -> dict:
    """Analiza robots.txt por grupos de user-agent. 'blocks_all' SOLO si el grupo
    global (User-agent: *) bloquea todo el sitio (evita falsos positivos cuando un
    Disallow: / esta bajo un bot concreto). Reconoce buenas practicas ya aplicadas."""
    txt = robots_text or ""
    groups: dict[str, dict] = {}
    cur = None
    for raw in txt.splitlines():
        l = raw.strip()
        if not l or l.startswith("#"):
            continue
        low = l.lower()
        if low.startswith("user-agent:"):
            cur = l.split(":", 1)[1].strip() or "*"
            groups.setdefault(cur, {"disallow": [], "allow": []})
        elif low.startswith("disallow:") and cur is not None:
            p = l.split(":", 1)[1].strip()
            if p:
                groups[cur]["disallow"].append(p)
        elif low.startswith("allow:") and cur is not None:
            groups[cur]["allow"].append(l.split(":", 1)[1].strip())

    star = groups.get("*", {"disallow": [], "allow": []})
    all_dis = [d for g in groups.values() for d in g["disallow"]]
    blocks_all = ("/" in star["disallow"]) and not any(a in ("/", "/*", "") for a in star["allow"])
    has_sitemap = bool(re.search(r"(?im)^\s*sitemap:", txt))

    # ¿Bloquea a los bots de IA? (si los bloqueas, la IA no puede leerte ni citarte)
    ai_bots = ["gptbot", "chatgpt-user", "oai-searchbot", "google-extended", "ccbot",
               "perplexitybot", "claudebot", "anthropic-ai", "applebot-extended",
               "bytespider", "meta-externalagent", "amazonbot"]
    ai_blocked = []
    for ua, g in groups.items():
        if ua.lower() in ai_bots and "/" in g["disallow"] and not any(a in ("/", "") for a in g["allow"]):
            ai_blocked.append(ua)
    if blocks_all:
        ai_blocked = ai_bots[:]
    joined = " ".join(d.lower() for d in all_dis)

    checks = {
        "el buscador interno": ["?s=", "/search", "/buscar", "?q=", "/?s"],
        "el area de administracion": ["wp-admin", "/admin", "wp-login", "/login"],
        "los feeds y adjuntos": ["/feed", "attachment", "/wp-json", "/trackback"],
        "los filtros con parametros (?)": ["*?", "/*?"],
        "las paginas de etiqueta/tag": ["/tag", "/etiqueta", "/label"],
    }
    good_blocks = [name for name, kws in checks.items() if any(k in joined for k in kws)]
    suggest = [name for name, kws in checks.items() if not any(k in joined for k in kws)]
    return {
        "present": bool(txt.strip()),
        "disallow_count": len(all_dis),
        "blocks_all": blocks_all,
        "has_sitemap": has_sitemap,
        "disallow_sample": all_dis[:8],
        "good_blocks": good_blocks,
        "suggest_block": suggest[:3],
        "ai_blocked": ai_blocked,
    }


# ---- Deteccion de analitica/pixeles (y duplicados = plus) --------------------

def detect_analytics(html: str) -> dict:
    h = html or ""
    ga4 = sorted(set(re.findall(r"G-[A-Z0-9]{6,}", h)))
    ua = sorted(set(re.findall(r"UA-\d{4,}-\d+", h)))
    gtm = sorted(set(re.findall(r"GTM-[A-Z0-9]{5,}", h)))
    aw = sorted(set(re.findall(r"AW-\d{6,}", h)))
    fb_ids = sorted(set(re.findall(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{6,})['\"]", h)))
    gtag_loads = len(re.findall(r"gtag/js\?id=", h))

    tools = []
    if ga4:
        tools.append("Google Analytics 4")
    if ua:
        tools.append("Universal Analytics (obsoleto)")
    if gtm:
        tools.append("Google Tag Manager")
    if aw:
        tools.append("Google Ads")
    if fb_ids or re.search(r"connect\.facebook\.net|fbq\(", h):
        tools.append("Meta Pixel")
    if re.search(r"clarity\.ms|clarity\(", h):
        tools.append("Microsoft Clarity")
    if re.search(r"static\.hotjar\.com|hotjar", h):
        tools.append("Hotjar")
    if re.search(r"analytics\.tiktok\.com", h):
        tools.append("TikTok Pixel")
    if re.search(r"snap\.licdn\.com|_linkedin_partner_id", h):
        tools.append("LinkedIn Insight")

    dup = []
    if len(ga4) > 1:
        dup.append(f"{len(ga4)} mediciones GA4 distintas ({', '.join(ga4)})")
    if gtag_loads > 1:
        dup.append(f"la libreria de Google (gtag.js) se carga {gtag_loads} veces")
    if ga4 and gtm:
        dup.append("GA4 cargado directo Y por Tag Manager (posible doble conteo)")
    if len(fb_ids) > 1:
        dup.append(f"{len(fb_ids)} Meta Pixel distintos")
    for _id in ga4:
        if len(re.findall(re.escape(_id), h)) >= 3:
            dup.append(f"el ID {_id} aparece repetido en la pagina")
            break

    return {"tools": tools, "ga4": ga4, "ua": ua, "gtm": gtm, "fb": fb_ids,
            "has_any": bool(tools), "duplicated": bool(dup), "dup_notes": dup[:3]}


# ---- PageSpeed Insights (opcional, requiere API key) -------------------------

async def fetch_psi(client: httpx.AsyncClient, url: str, strategy: str = "mobile") -> dict | None:
    key = os.getenv("GOOGLE_PSI_API_KEY", "").strip()
    if not key:
        return None
    api = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {"url": url, "key": key, "strategy": strategy,
              "category": ["performance", "seo"]}
    try:
        r = await client.get(api, params=params, timeout=PSI_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        lh = data.get("lighthouseResult", {})
        cats = lh.get("categories", {})
        aud = lh.get("audits", {})

        def dv(k):
            return aud.get(k, {}).get("displayValue", "")

        perf = cats.get("performance", {}).get("score")
        seo = cats.get("seo", {}).get("score")
        return {
            "performance": round(perf * 100) if perf is not None else None,
            "seo": round(seo * 100) if seo is not None else None,
            "fcp": dv("first-contentful-paint"),
            "lcp": dv("largest-contentful-paint"),
            "tbt": dv("total-blocking-time"),
            "si": dv("speed-index"),
        }
    except Exception:  # noqa: BLE001
        return None


async def _psi_desktop(url: str, tries: int = 2) -> dict | None:
    """Escritorio con PageSpeed (Lighthouse). None si no hay API key."""
    if not os.getenv("GOOGLE_PSI_API_KEY", "").strip():
        return None
    async with httpx.AsyncClient(headers=HEADERS) as client:
        for _ in range(tries):
            r = await fetch_psi(client, url, "desktop")
            if r:
                return r
            await asyncio.sleep(1.5)
    return None


async def fetch_psi_full(url: str) -> dict | None:
    """Velocidad movil + escritorio.

    - MOVIL: medido con dispositivo propio (Playwright, iPhone), SIN el throttling
      4G de PageSpeed, para una lectura justa de movil en buena conexion.
    - ESCRITORIO: PageSpeed / Lighthouse (no aplica throttling agresivo).
    """
    import perf  # noqa: PLC0415
    try:
        m, d = await asyncio.gather(
            perf.measure_device(url, mobile=True),
            _psi_desktop(url),
            return_exceptions=True,
        )
        m = m if isinstance(m, dict) else None
        d = d if isinstance(d, dict) else None
        # analitica detectada con el navegador real (network + variables JS)
        analytics = m.pop("analytics", None) if isinstance(m, dict) else None
        # si el escritorio (PSI) falla pero tenemos Chromium, lo medimos tambien con dispositivo
        if not d:
            try:
                d = await perf.measure_device(url, mobile=False)
            except Exception:  # noqa: BLE001
                d = None
        if not m and not d:
            return None
        return {"mobile": m, "desktop": d, "analytics": analytics}
    except Exception:  # noqa: BLE001
        return None


# ---- Motor principal ---------------------------------------------------------

async def analyze(raw_url: str) -> Result:
    url = normalize_url(raw_url)
    res = Result(url=url, domain=domain_of(url),
                 analyzed_at=datetime.now(timezone.utc).isoformat())
    if not url:
        res.error = "URL vacia o invalida."
        return res

    t0 = time.perf_counter()
    async with httpx.AsyncClient(headers=HEADERS, verify=False,
                                 limits=httpx.Limits(max_connections=LINK_CONCURRENCY + 4)) as client:
        home, err, dt = await fetch_home(client, url)
        if home is None:
            res.error = f"No pudimos acceder al sitio: {err}"
            res.elapsed = time.perf_counter() - t0
            return res

        res.reachable = True
        res.final_url = str(home.url)
        home_html = home.text
        home_status = home.status_code
        home_time = dt
        https_ok = str(home.url).lower().startswith("https://")

        meta = parse_home(home_html, res.final_url)
        # Pais real por contenido (telefono/menciones), no solo por TLD
        country = detect_country(home_html, res.domain)
        meta["country"] = country.get("name", "")
        meta["gl"] = country.get("gl", "")
        meta["country_source"] = country.get("source", "")
        # Analitica / pixeles instalados (y si estan duplicados)
        analytics = detect_analytics(home_html)
        res.meta = meta

        # Archivos clave en paralelo
        robots_task = fetch_text(client, urljoin(url + "/", "robots.txt"))
        sitemap_task = fetch_text(client, urljoin(url + "/", "sitemap.xml"))
        sitemap_idx_task = fetch_text(client, urljoin(url + "/", "sitemap_index.xml"))
        llms_task = fetch_text(client, urljoin(url + "/", "llms.txt"))

        (robots_status, robots_text), (sm_status, sm_text), \
            (smi_status, smi_text), (llms_status, llms_text) = await asyncio.gather(
                robots_task, sitemap_task, sitemap_idx_task, llms_task
            )
        psi = None

        robots_ok = robots_status == 200 and bool(robots_text)
        sitemap_text = sm_text or smi_text
        sitemap_ok = bool(sitemap_text)
        sitemap_in_robots = bool(robots_text and re.search(r"(?i)sitemap:", robots_text))
        llms_ok = llms_status == 200 and bool(llms_text)
        res.psi = psi

        # ---- Muestreo de 404 -------------------------------------------------
        broken = 0
        checked = 0
        broken_examples = []
        sitemap_total = 0
        sitemap_comp = None
        try:
            if sitemap_text:
                locs, is_index = parse_sitemap_locs(sitemap_text)
                if is_index and locs:
                    child_status, child_text = await fetch_text(client, locs[0])
                    if child_text:
                        child_locs, _ = parse_sitemap_locs(child_text)
                        sitemap_total = len(child_locs) or len(locs)
                        locs = child_locs or locs
                else:
                    sitemap_total = len(locs)
                if locs:
                    sitemap_comp = categorize_sitemap(locs)
                sample = random.sample(locs, min(LINK_SAMPLE, len(locs))) if locs else []
            else:
                sample = []

            # incluir enlaces internos del home si hay pocos del sitemap
            if len(sample) < 6 and home_html:
                soup = BeautifulSoup(home_html, "html.parser")
                internal = []
                for a in soup.find_all("a", href=True):
                    href = urljoin(res.final_url, a["href"])
                    if urlparse(href).netloc == urlparse(res.final_url).netloc:
                        internal.append(href.split("#")[0])
                internal = list(dict.fromkeys(internal))
                sample += internal[: (8 - len(sample))]

            sample = list(dict.fromkeys(sample))[:LINK_SAMPLE]
            if sample:
                sem = asyncio.Semaphore(LINK_CONCURRENCY)

                async def bounded(u):
                    async with sem:
                        return await check_status(client, u)

                results = await asyncio.gather(*(bounded(u) for u in sample))
                for u, status in results:
                    if status is None:
                        continue  # error de red, no cuenta
                    checked += 1
                    if status >= 400:
                        broken += 1
                        if len(broken_examples) < 4:
                            broken_examples.append({"url": u, "status": status})
        except Exception:  # noqa: BLE001
            pass

        broken_ratio = (broken / checked) if checked else 0.0

    res.elapsed = round(time.perf_counter() - t0, 1)

    res.signals = {
        "sitemap_comp": sitemap_comp,
        "https": https_ok,
        "home_status": home_status,
        "home_time": round(home_time, 2),
        "robots": robots_ok,
        "sitemap": sitemap_ok,
        "sitemap_in_robots": sitemap_in_robots,
        "sitemap_total": sitemap_total,
        "llms_txt": llms_ok,
        "links_checked": checked,
        "links_broken": broken,
        "broken_ratio": round(broken_ratio, 2),
        "broken_examples": broken_examples,
        "analytics": analytics,
        "robots_info": analyze_robots(robots_text if robots_ok else ""),
    }

    _score(res)
    return res


# ---- Scoring y hallazgos -----------------------------------------------------

def _score(res: Result) -> None:
    m = res.meta
    s = res.signals
    tecnico: list[Check] = []
    onpage: list[Check] = []
    geo: list[Check] = []
    good = res.findings_good
    improve = res.findings_improve

    def add(cat, key, label, earned, possible, ok, detail=""):
        cat.append(Check(key, label, earned, possible, ok, detail))

    # ---------- Tecnico y accesibilidad ----------
    add(tecnico, "https", "Conexion segura (HTTPS)", 20 if s["https"] else 0, 20, s["https"])
    ok200 = 200 <= s["home_status"] < 300
    add(tecnico, "status", "La web responde correctamente", 15 if ok200 else 0, 15, ok200)
    ht = s["home_time"]
    speed = 10 if ht < 1.5 else (6 if ht < 3 else (3 if ht < 5 else 0))
    add(tecnico, "speed", "Tiempo de respuesta", speed, 10, ht < 3, f"{ht}s")
    add(tecnico, "robots", "Archivo robots.txt", 10 if s["robots"] else 0, 10, s["robots"])
    add(tecnico, "sitemap", "Mapa del sitio (sitemap)", 15 if s["sitemap"] else 0, 15, s["sitemap"])
    add(tecnico, "viewport", "Preparada para movil", 10 if m["viewport"] else 0, 10, m["viewport"])
    health404 = round(20 * (1 - s["broken_ratio"])) if s["links_checked"] else 12
    add(tecnico, "links", "Enlaces sin errores (404)", health404, 20, s["broken_ratio"] < 0.1,
        f'{s["links_broken"]}/{s["links_checked"]} rotos')

    # ---------- SEO on-page ----------
    tlen = len(m["title"])
    tscore = 20 if 25 <= tlen <= 65 else (10 if m["title"] else 0)
    add(onpage, "title", "Titulo de pagina", tscore, 20, bool(m["title"]), f"{tlen} car.")
    dlen = len(m["description"])
    dscore = 20 if 70 <= dlen <= 165 else (10 if m["description"] else 0)
    add(onpage, "desc", "Descripcion (meta description)", dscore, 20, bool(m["description"]), f"{dlen} car.")
    h1ok = m["h1_count"] == 1
    h1score = 15 if h1ok else (8 if m["h1_count"] > 1 else 0)
    add(onpage, "h1", "Titular principal (H1)", h1score, 15, h1ok, f'{m["h1_count"]} H1')
    add(onpage, "canonical", "URL canonica", 10 if m["canonical"] else 0, 10, m["canonical"])
    add(onpage, "lang", "Idioma declarado", 10 if m["lang"] else 0, 10, bool(m["lang"]), m["lang"])
    wc = m["word_count"]
    wscore = 15 if wc >= 400 else round(15 * wc / 400) if wc else 0
    add(onpage, "content", "Contenido suficiente", wscore, 15, wc >= 300, f"{wc} palabras")
    ogok = bool(m["og_title"] and m["og_image"])
    add(onpage, "og", "Vista previa al compartir (Open Graph)", 10 if ogok else (5 if m["og_title"] else 0),
        10, ogok)
    it = m.get("img_total", 0); ia = m.get("img_alt", 0)
    cov = (ia / it) if it else 1.0
    add(onpage, "alt", "Imagenes con texto ALT", round(10 * cov), 10, cov >= 0.7,
        f"{ia}/{it}" if it else "sin imagenes")

    # ---------- GEO / preparacion para la IA (todo lo que hace que la IA te lea,
    #            te entienda y te recomiende por delante de la competencia) ----------
    rob = s.get("robots_info") or {}
    ai_ok = not rob.get("ai_blocked")
    add(geo, "ai_crawlers", "La IA puede leer tu sitio (rastreo IA permitido)", 15 if ai_ok else 0, 15, ai_ok,
        "Rastreo de IA permitido" if ai_ok else ("Bloqueas bots de IA: " + ", ".join(rob.get("ai_blocked", [])[:4])))
    schema_set = set(m["schema_types"])
    valuable = schema_set & {"faqpage", "qapage", "organization", "localbusiness", "professionalservice",
                             "product", "article", "howto", "service", "review"}
    schema_pts = 18 if valuable else (9 if schema_set else 0)
    add(geo, "schema", "Datos estructurados utiles (schema)", schema_pts, 18, bool(valuable),
        ", ".join(m["schema_raw_types"][:4]) or "sin datos estructurados")
    geo_entity = bool(schema_set & {"organization", "localbusiness", "professionalservice"}) or m["has_sameas"]
    add(geo, "entity", "Tu marca como entidad reconocible", 14 if geo_entity else 0, 14, geo_entity,
        "Organization/sameAs presentes" if geo_entity else "sin ficha de entidad ni sameAs")
    add(geo, "faq", "Contenido en preguntas y respuestas (FAQ)", 12 if m.get("has_faq") else 0, 12, m.get("has_faq"),
        "FAQ / Q&A presente" if m.get("has_faq") else "sin FAQ ni preguntas frecuentes")
    wc = m.get("word_count", 0)
    content_pts = 12 if wc >= 700 else (round(12 * wc / 700) if wc else 0)
    add(geo, "content_ia", "Contenido suficiente para citar", content_pts, 12, wc >= 500, f"{wc} palabras")
    heading_ok = m["h1_count"] == 1 and m["h2_count"] >= 3
    add(geo, "headings", "Estructura de titulares clara", 9 if heading_ok else (5 if (m["h1_count"] >= 1 and m["h2_count"] >= 1) else 0),
        9, heading_ok, f'{m["h1_count"]} H1 / {m["h2_count"]} H2')
    add(geo, "contact", "Ficha de contacto (nombre, telefono, direccion)", 6 if m.get("has_contact") else 0, 6, m.get("has_contact"),
        "Datos de contacto visibles" if m.get("has_contact") else "sin telefono/direccion clara")
    add(geo, "llms", "Guia para buscadores con IA (llms.txt)", 6 if s["llms_txt"] else 0, 6, s["llms_txt"])
    dlen = len(m["description"])
    add(geo, "desc_ia", "Resumen que la IA puede citar", 5 if 70 <= dlen <= 165 else (2 if dlen else 0),
        5, bool(m["description"]), f"{dlen} car.")
    add(geo, "title_ia", "Titulo descriptivo", 3 if m["title"] else 0, 3, bool(m["title"]))

    def cat_score(checks):
        earned = sum(c.earned for c in checks)
        possible = sum(c.possible for c in checks)
        return round(100 * earned / possible) if possible else 0

    # PSI opcional: mezcla en tecnico si esta disponible
    psi_note = None
    t_score = cat_score(tecnico)
    if res.psi and res.psi.get("performance") is not None:
        t_score = round(0.6 * t_score + 0.4 * res.psi["performance"])
        psi_note = res.psi

    cats = {
        "tecnico": {"label": "Salud tecnica", "score": t_score,
                    "checks": [c.__dict__ for c in tecnico]},
        "onpage": {"label": "SEO on-page (Google)", "score": cat_score(onpage),
                   "checks": [c.__dict__ for c in onpage]},
        "geo": {"label": "Preparacion para la IA (GEO)", "score": cat_score(geo),
                "checks": [c.__dict__ for c in geo]},
    }
    res.categories = cats

    overall = round(0.35 * cats["tecnico"]["score"] +
                    0.35 * cats["onpage"]["score"] +
                    0.30 * cats["geo"]["score"])
    res.score = overall
    res.grade = ("A" if overall >= 85 else "B" if overall >= 70 else
                 "C" if overall >= 55 else "D" if overall >= 40 else "E")

    # ---------- Hallazgos en lenguaje de cliente ----------
    if s["https"]:
        good.append("Tu web usa conexion segura (HTTPS).")
    else:
        improve.append({"title": "Falta conexion segura (HTTPS)",
                        "detail": "Los navegadores y Google penalizan las webs sin candado de seguridad.",
                        "severity": "alto"})
    if s["sitemap"]:
        good.append("Tienes mapa del sitio, Google sabe que paginas rastrear.")
    else:
        improve.append({"title": "No encontramos el mapa del sitio",
                        "detail": "Sin sitemap, Google y la IA tardan mas en descubrir tus paginas.",
                        "severity": "medio"})
    if s["links_checked"] and s["links_broken"] > 0:
        ex = ", ".join(e["url"] for e in s["broken_examples"][:2])
        improve.append({"title": f'Encontramos {s["links_broken"]} enlace(s) roto(s) en la muestra',
                        "detail": f"Paginas que ya no existen restan confianza. Ejemplo: {ex}",
                        "severity": "alto" if s["broken_ratio"] > 0.2 else "medio"})
    elif s["links_checked"]:
        good.append("En nuestra muestra no encontramos enlaces rotos.")

    # ---------- Analitica / medicion ----------
    an = s.get("analytics") or {}
    if an.get("duplicated"):
        improve.append({"title": "Tu analitica esta duplicada",
                        "detail": "Detectamos medicion repetida (" + "; ".join(an.get("dup_notes", [])) +
                                  "). Eso infla visitas y conversiones y ensucia tus decisiones. Hay que dejar una sola.",
                        "severity": "medio"})
    elif not an.get("has_any"):
        improve.append({"title": "No detectamos analitica web",
                        "detail": "Sin Google Analytics 4 ni Tag Manager no sabes que paginas te traen clientes "
                                  "ni de donde llegan. Es lo primero para poder mejorar con datos.",
                        "severity": "medio"})
    else:
        good.append("Tienes analitica instalada (" + ", ".join(an.get("tools", [])[:3]) + ").")

    if not m["description"]:
        improve.append({"title": "Falta la descripcion de la pagina",
                        "detail": "Es el resumen que Google muestra en resultados y que la IA usa para citarte.",
                        "severity": "medio"})
    if m["h1_count"] != 1:
        improve.append({"title": "El titular principal no esta bien definido",
                        "detail": f'Detectamos {m["h1_count"]} titulares H1. Lo ideal es uno claro por pagina.',
                        "severity": "bajo"})

    if not res.categories["geo"] or cats["geo"]["score"] < 60:
        detail_bits = []
        if not m["schema_types"]:
            detail_bits.append("sin datos estructurados")
        if not s["llms_txt"]:
            detail_bits.append("sin guia para IA (llms.txt)")
        if not m["has_sameas"]:
            detail_bits.append("marca poco definida como entidad")
        improve.append({"title": "La IA aun no te entiende bien",
                        "detail": "Preparacion para buscadores con IA mejorable: " +
                                  (", ".join(detail_bits) if detail_bits else "faltan senales GEO") + ".",
                        "severity": "alto"})
    else:
        good.append("Tu web tiene buenas senales para los buscadores con IA.")

    if m["schema_types"]:
        good.append("Usas datos estructurados: ayudan a Google y a la IA a entenderte.")

    res.categories["_psi"] = psi_note


def result_to_dict(res: Result) -> dict:
    d = res.__dict__.copy()
    return d


def _grade(overall: int) -> str:
    return ("A" if overall >= 85 else "B" if overall >= 70 else
            "C" if overall >= 55 else "D" if overall >= 40 else "E")


def apply_analytics(data: dict, detected: dict | None) -> dict:
    """Reconcilia la analitica con lo detectado por el navegador real (mas fiable
    que el HTML estatico) y reescribe el hallazgo correspondiente."""
    if not detected:
        return data
    sig = data.setdefault("signals", {})
    static = sig.get("analytics") or {}
    # el render manda si detecto algo; si no, conserva lo estatico
    merged = detected if detected.get("has_any") else (static if static.get("has_any") else detected)
    # une herramientas de ambas fuentes
    tools = list(dict.fromkeys((merged.get("tools") or []) + (static.get("tools") or [])))
    merged = {**merged, "tools": tools, "has_any": bool(tools) or merged.get("has_any")}
    sig["analytics"] = merged

    imp = data.setdefault("findings_improve", [])
    good = data.setdefault("findings_good", [])
    imp[:] = [f for f in imp if not (isinstance(f, dict) and
              str(f.get("title", "")).startswith(("Tu analitica esta duplicada", "No detectamos analitica")))]
    good[:] = [g for g in good if not (isinstance(g, str) and g.startswith("Tienes analitica instalada"))]

    if merged.get("duplicated"):
        imp.insert(0, {"title": "Tu analitica esta duplicada",
                       "detail": "Detectamos medicion repetida (" + "; ".join(merged.get("dup_notes", [])) +
                                 "). Eso infla visitas y conversiones y ensucia tus decisiones. Hay que dejar una sola.",
                       "severity": "medio"})
    elif not merged.get("has_any"):
        imp.append({"title": "No detectamos analitica web",
                    "detail": "Sin Google Analytics 4 ni Tag Manager no sabes que paginas te traen clientes "
                              "ni de donde llegan. Es lo primero para poder mejorar con datos.",
                    "severity": "medio"})
    else:
        good.insert(0, "Tienes analitica instalada (" + ", ".join(tools[:3]) + ").")
    return data


def apply_ai_to_result(data: dict, ai: dict | None) -> dict:
    """Integra la consulta real a la IA en un result-dict ya calculado (se usa en
    segundo plano tras el resultado rapido). Ajusta la nota GEO, la global y los
    hallazgos, y adjunta geo_ai."""
    data["geo_ai"] = ai
    if not (ai and ai.get("available") and not ai.get("error")):
        return data

    cats = data["categories"]
    geo = cats.get("geo", {})
    if ai.get("ai_score") is not None and geo:
        # las senales tecnicas reales (que varian por sitio) pesan 60%; la prueba a
        # la IA (reconocimiento + recomendacion) 40%. Asi la nota discrimina de verdad.
        geo["score"] = round(0.6 * geo["score"] + 0.4 * ai["ai_score"])
        geo["ai"] = True
        overall = round(0.35 * cats["tecnico"]["score"] +
                        0.35 * cats["onpage"]["score"] +
                        0.30 * geo["score"])
        data["score"] = overall
        data["grade"] = _grade(overall)

    good = data.setdefault("findings_good", [])
    improve = data.setdefault("findings_improve", [])
    if ai.get("knows_brand"):
        good.insert(0, "Cuando preguntan por tu marca a la IA, sabe quien eres y te describe bien.")
    else:
        improve.insert(0, {
            "title": "La IA no sabe quien eres",
            "detail": "Le preguntamos a la IA por tu empresa y no tiene informacion fiable de ti. "
                      "Cada vez mas clientes preguntan a la IA antes de decidir, y hoy no te encuentran.",
            "severity": "alto"})
    if ai.get("recommended") is True:
        good.insert(0, "Cuando piden tu servicio a la IA, te incluye entre las opciones recomendadas.")
    elif ai.get("recommended") is False:
        improve.insert(0, {
            "title": "Cuando piden tu servicio, la IA recomienda a otros",
            "detail": "Al pedirle recomendaciones de tu sector, la IA nombra a otras empresas antes que a la tuya: "
                      "pierdes a los clientes que aun no te conocen.",
            "severity": "alto"})
    return data
