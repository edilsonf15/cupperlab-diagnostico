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
import re
from collections import deque
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.6"}

MAX_PAGES = 40          # tope de páginas a analizar a fondo
CONCURRENCY = 8
PAGE_TIMEOUT = 10.0
SITEMAP_CAP = 300       # URLs máximas a leer del sitemap para sembrar

TITLE_MIN, TITLE_MAX = 25, 65
DESC_MIN, DESC_MAX = 60, 165
THIN_WORDS = 200

_SKIP_RE = re.compile(
    r"/(wp-admin|wp-login|admin|login|signin|sign-in|logout|cart|checkout|"
    r"my-account|mi-cuenta|carrito|wp-json)(/|\.|$)", re.I)
_ASSET_RE = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|css|js|xml|ico|mp4|woff2?|avif)$", re.I)


def _norm(u: str) -> str:
    return (u or "").split("#")[0].rstrip("/").lower()


def _valid(u: str, base_net: str) -> bool:
    if not u or urlparse(u).netloc != base_net:
        return False
    if _ASSET_RE.search(u) or _SKIP_RE.search(u):
        return False
    if re.search(r"[?&](add-to-cart|replytocom)=", u, re.I):
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

    # enlaces internos (para BFS y para conteo)
    links = []
    for a in soup.find_all("a", href=True):
        h = urljoin(url, a["href"]).split("#")[0]
        if _valid(h, base_net):
            links.append(h)
    links = list(dict.fromkeys(links))

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    word_count = len(text.split())

    schema_types = []
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        for m in re.findall(r'"@type"\s*:\s*"([^"]+)"', s.get_text() or ""):
            schema_types.append(m.strip())

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
        "url_issues": url_issues,
        "int_links": len(links),
        "_links": links,
    }


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
def _aggregate(pages: list[dict]) -> dict:
    def ex(items, n=5):
        return items[:n]

    title_missing, title_bad, h1_missing, h1_multi, h2_missing = [], [], [], [], []
    desc_missing, desc_bad, og_missing = [], [], []
    thin, canon_missing, canon_other, noindex, no_schema, url_bad, img_alt = [], [], [], [], [], [], []
    titles, descs = {}, {}
    total_imgs = total_missing_alt = words_sum = 0
    hreflang_pages = 0

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
    score = max(0, min(100, round(score)))

    return {
        "issues": issues,
        "totals": {
            "pages": len(pages),
            "avg_words": round(words_sum / n),
            "img_total": total_imgs,
            "img_no_alt": total_missing_alt,
            "hreflang_pages": hreflang_pages,
        },
        "score": score,
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

            # ---- crawl por rondas (BFS) hasta MAX_PAGES
            sem = asyncio.Semaphore(CONCURRENCY)
            pages, final_seen = [], set()
            while queue and len(pages) < MAX_PAGES:
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
                    for l in p.pop("_links", []):
                        enqueue(l)
                    pages.append(p)
    except Exception:  # noqa: BLE001
        return None

    if not pages:
        return None
    model = _aggregate(pages)
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
