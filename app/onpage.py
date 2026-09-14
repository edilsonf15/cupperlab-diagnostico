"""
onpage — Auditoría SEO on-page AVANZADA (multi-página).

Rastrea varias páginas reales del sitio (no solo la home) y revisa, por página:
títulos, meta descripciones, H1/encabezados, contenido (thin), imágenes sin ALT,
canonical, indexabilidad (noindex), URLs amigables y datos estructurados. Luego
agrega los problemas del sitio con CONTEO real y EJEMPLOS de URLs concretas, y da
una nota 0-100.

Todo se mide en vivo; nada se inventa. Español neutro, sin guion largo.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.6"}

MAX_PAGES = 12
CONCURRENCY = 6
PAGE_TIMEOUT = 10.0

# Umbrales
TITLE_MIN, TITLE_MAX = 25, 65
DESC_MIN, DESC_MAX = 60, 165
THIN_WORDS = 200


# --------------------------------------------------------------------------- #
# Parseo de una página
# --------------------------------------------------------------------------- #
def _norm(u: str) -> str:
    return (u or "").split("#")[0].rstrip("/").lower()


def _parse_page(url: str, html: str, status: int, base_net: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    def meta(name=None, prop=None):
        if name:
            t = soup.find("meta", attrs={"name": re.compile("^" + name + "$", re.I)})
        else:
            t = soup.find("meta", attrs={"property": re.compile("^" + prop + "$", re.I)})
        return (t.get("content") or "").strip() if t else ""

    desc = meta(name="description")
    robots = meta(name="robots").lower()
    noindex = "noindex" in robots

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = soup.find_all("h2")
    h3s = soup.find_all("h3")

    can_tag = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    canonical = (can_tag.get("href") or "").strip() if can_tag else ""

    # imágenes (excluye data: y svg inline sin src)
    imgs = soup.find_all("img")
    img_total = 0
    img_no_alt = 0
    for im in imgs:
        src = im.get("src") or im.get("data-src") or ""
        if not src or src.startswith("data:"):
            continue
        img_total += 1
        if not (im.get("alt") or "").strip():
            img_no_alt += 1

    # texto visible
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    word_count = len(text.split())

    # schema
    schema_types: list[str] = []
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        for m in re.findall(r'"@type"\s*:\s*"([^"]+)"', s.get_text() or ""):
            schema_types.append(m.strip())

    lang = ""
    htmltag = soup.find("html")
    if htmltag:
        lang = (htmltag.get("lang") or "").strip().lower()

    path = urlparse(url).path
    query = urlparse(url).query
    url_issues = []
    if len(url) > 100:
        url_issues.append("larga")
    if query:
        url_issues.append("parámetros")
    if re.search(r"[A-Z]", path):
        url_issues.append("mayúsculas")
    if "_" in path:
        url_issues.append("guiones bajos")
    if re.search(r"%[0-9a-fA-F]{2}", path):
        url_issues.append("caracteres raros")

    can_self = None
    if canonical:
        can_self = _norm(urljoin(url, canonical)) == _norm(url)

    return {
        "url": url,
        "status": status,
        "title": title,
        "title_len": len(title),
        "desc": desc,
        "desc_len": len(desc),
        "h1_count": len(h1s),
        "h1_first": h1s[0] if h1s else "",
        "h2_count": len(h2s),
        "h3_count": len(h3s),
        "canonical": canonical,
        "canonical_self": can_self,
        "noindex": noindex,
        "img_total": img_total,
        "img_no_alt": img_no_alt,
        "word_count": word_count,
        "schema_types": sorted(set(t.lower() for t in schema_types)),
        "lang": lang,
        "url_issues": url_issues,
    }


# --------------------------------------------------------------------------- #
# Rastreo
# --------------------------------------------------------------------------- #
def _pick_candidates(home_url: str, home_html: str, extra: list[str] | None) -> list[str]:
    base_net = urlparse(home_url).netloc
    home_norm = _norm(home_url)
    out = [home_url]
    seen = {home_norm}

    def add(u: str):
        n = _norm(u)
        if n and n not in seen and urlparse(u).netloc == base_net:
            if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|css|js|xml|ico|mp4|woff2?)$", u, re.I):
                return
            # páginas privadas o de sistema: fuera del análisis SEO
            if re.search(r"/(wp-admin|wp-login|admin|login|signin|sign-in|logout|"
                         r"cart|checkout|my-account|mi-cuenta|carrito|wp-json)(/|\.|$)", u, re.I):
                return
            if re.search(r"[?&](add-to-cart|replytocom)=", u, re.I):
                return
            seen.add(n)
            out.append(u)

    if home_html:
        soup = BeautifulSoup(home_html, "html.parser")
        for a in soup.find_all("a", href=True):
            add(urljoin(home_url, a["href"]).split("#")[0])
    for u in (extra or []):
        add(u)
    return out[:MAX_PAGES]


async def _fetch(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore, base_net: str) -> dict | None:
    async with sem:
        try:
            r = await client.get(url, timeout=PAGE_TIMEOUT, follow_redirects=True)
            ct = (r.headers.get("content-type") or "").lower()
            if "html" not in ct:
                return None
            return _parse_page(str(r.url), r.text, r.status_code, base_net)
        except Exception:  # noqa: BLE001
            return None


# --------------------------------------------------------------------------- #
# Agregación de problemas
# --------------------------------------------------------------------------- #
def _aggregate(pages: list[dict]) -> dict:
    def ex(items, n=4):
        return items[:n]

    title_missing, title_bad, h1_missing, h1_multi = [], [], [], []
    desc_missing, desc_bad = [], []
    thin, canon_missing, canon_other, noindex, no_schema, url_bad, img_alt = [], [], [], [], [], [], []
    titles: dict[str, list[str]] = {}
    descs: dict[str, list[str]] = {}
    total_imgs = 0
    total_missing_alt = 0
    words_sum = 0

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
        if p["h1_count"] == 0:
            h1_missing.append(u)
        elif p["h1_count"] > 1:
            h1_multi.append({"url": u, "n": p["h1_count"]})
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
        total_imgs += p["img_total"]
        total_missing_alt += p["img_no_alt"]
        words_sum += p["word_count"]

    dup_titles = [{"value": t, "urls": us} for t, us in titles.items() if len(us) > 1]
    dup_descs = [{"value": d, "urls": us} for d, us in descs.items() if len(us) > 1]

    n = len(pages) or 1
    issues = {
        "title_missing": {"count": len(title_missing), "examples": ex(title_missing)},
        "title_dup": {"count": len(dup_titles), "groups": ex(dup_titles, 3)},
        "title_bad_len": {"count": len(title_bad), "examples": ex(title_bad)},
        "desc_missing": {"count": len(desc_missing), "examples": ex(desc_missing)},
        "desc_dup": {"count": len(dup_descs), "groups": ex(dup_descs, 3)},
        "desc_bad_len": {"count": len(desc_bad), "examples": ex(desc_bad)},
        "h1_missing": {"count": len(h1_missing), "examples": ex(h1_missing)},
        "h1_multiple": {"count": len(h1_multi), "examples": ex(h1_multi)},
        "thin": {"count": len(thin), "examples": ex(thin)},
        "canonical_missing": {"count": len(canon_missing), "examples": ex(canon_missing)},
        "canonical_other": {"count": len(canon_other), "examples": ex(canon_other)},
        "noindex": {"count": len(noindex), "examples": ex(noindex)},
        "no_schema": {"count": len(no_schema), "examples": ex(no_schema)},
        "url_unfriendly": {"count": len(url_bad), "examples": ex(url_bad)},
        "img_no_alt": {"pages": len(img_alt), "total": total_missing_alt,
                       "total_imgs": total_imgs, "examples": ex(img_alt)},
    }

    # ---- Nota 0-100: parte de 100 y descuenta por proporción de páginas afectadas
    def frac(c):
        return c / n

    score = 100.0
    score -= 26 * frac(len(title_missing))              # título ausente: grave
    score -= 10 * frac(len(title_bad))
    score -= 8 * min(1.0, len(dup_titles) / n)          # duplicados
    score -= 16 * frac(len(desc_missing))
    score -= 6 * frac(len(desc_bad))
    score -= 6 * min(1.0, len(dup_descs) / n)
    score -= 18 * frac(len(h1_missing))                 # sin H1: grave
    score -= 6 * frac(len(h1_multi))
    score -= 12 * frac(len(thin))
    score -= 10 * frac(len(canon_missing))
    score -= 8 * frac(len(canon_other))
    score -= 20 * frac(len(noindex))                    # noindex accidental: muy grave
    score -= 10 * (total_missing_alt / total_imgs if total_imgs else 0)
    score -= 5 * frac(len(url_bad))
    score = max(0, min(100, round(score)))

    return {
        "issues": issues,
        "totals": {
            "pages": len(pages),
            "avg_words": round(words_sum / n),
            "img_total": total_imgs,
            "img_no_alt": total_missing_alt,
        },
        "score": score,
    }


# --------------------------------------------------------------------------- #
# Entrada principal
# --------------------------------------------------------------------------- #
async def audit(url: str, home_html: str | None = None,
                extra_urls: list[str] | None = None) -> dict | None:
    """Auditoría on-page multi-página. Devuelve modelo con issues + score, o None."""
    base_net = urlparse(url).netloc
    try:
        async with httpx.AsyncClient(headers=_HEADERS, verify=False,
                                     limits=httpx.Limits(max_connections=CONCURRENCY + 2)) as client:
            if home_html is None:
                try:
                    r = await client.get(url, timeout=PAGE_TIMEOUT, follow_redirects=True)
                    home_html = r.text
                    url = str(r.url)
                    base_net = urlparse(url).netloc
                except Exception:  # noqa: BLE001
                    return None
            candidates = _pick_candidates(url, home_html, extra_urls)
            sem = asyncio.Semaphore(CONCURRENCY)
            results = await asyncio.gather(*(_fetch(client, u, sem, base_net) for u in candidates))
    except Exception:  # noqa: BLE001
        return None

    # dedupe por URL FINAL (dos candidatos pueden redirigir a la misma página)
    pages = []
    seen_final = set()
    for p in results:
        if not p:
            continue
        key = _norm(p["url"])
        if key in seen_final:
            continue
        seen_final.add(key)
        pages.append(p)
    if not pages:
        return None

    model = _aggregate(pages)
    model["pages"] = pages
    model["engine"] = "onpage"
    return model


# --------------------------------------------------------------------------- #
# Prueba directa:  python onpage.py https://dominio.com
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    import sys

    _url = sys.argv[1] if len(sys.argv) > 1 else "https://cupperlab.com"
    _m = asyncio.run(audit(_url))
    if _m:
        _m_light = {k: v for k, v in _m.items() if k != "pages"}
        print(json.dumps(_m_light, indent=2, ensure_ascii=False))
        print("\nPáginas analizadas:", len(_m["pages"]))
        for _p in _m["pages"]:
            print(f'  {_p["status"]} · {_p["word_count"]}w · T{_p["title_len"]} · H1x{_p["h1_count"]} · {_p["url"]}')
    else:
        print("None")
