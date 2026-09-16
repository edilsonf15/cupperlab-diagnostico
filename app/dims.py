# -*- coding: utf-8 -*-
"""
Fuente ÚNICA de las 9 dimensiones (velocidad móvil/escritorio, SEO on-page, GEO,
salud técnica, schema, seguridad, presencia local, contenido). Es un port fiel de
los modelos JS de la página (index.html) para que la PÁGINA, el PDF y el CORREO
muestren EXACTAMENTE los mismos valores. No inventa nada: lee el mismo `data`.
"""
from __future__ import annotations
import re
from i18n import L

_VAL = {"ok": 100, "warn": 55, "bad": 0}


def _score(points: list[tuple]) -> int:
    sw = sum(w for _, _, w in points)
    ss = sum(_VAL[s] * w for _, s, w in points)
    return round(ss / sw) if sw else 0


def _score_bool(points: list[tuple]) -> int:
    sw = sum(w for _, _, w in points)
    ss = sum((100 if ok else 0) * w for _, ok, w in points)
    return round(ss / sw) if sw else 0


def _rx(types, pattern) -> bool:
    rx = re.compile(pattern, re.I)
    return any(rx.search(str(t)) for t in (types or []))


# ---------------- modelos (port 1:1 de index.html) ----------------

def _geo(data) -> int | None:
    ai = data.get("geo_ai") or {}
    sig = data.get("signals") or {}
    meta = data.get("meta") or {}
    if not ai.get("available") or ai.get("limited"):
        return None  # sin datos fiables de IA (o cuota agotada): no puntuamos GEO
    rec = ai.get("recognition")
    st = []
    st.append(("know", "ok" if rec == "strong" else ("warn" if rec == "weak" else "bad"), 20))
    reco = ai.get("recommended")
    st.append(("reco", "ok" if reco is True else ("warn" if reco is None else "bad"), 20))
    srcs = ai.get("sources") or []
    ext = [s for s in srcs if not (isinstance(s, dict) and s.get("own"))]
    st.append(("sources", "ok" if len(ext) > 0 else ("warn" if len(srcs) > 0 else "bad"), 10))
    lq = sig.get("llms_quality")
    st.append(("llms", "ok" if lq == "good" else ("warn" if lq == "thin" else "bad"), 10))
    sch = meta.get("schema_types") or []
    citable = bool(meta.get("has_faq")) or _rx(sch, r"faqpage|qapage|article|howto")
    st.append(("citable", "ok" if citable else "warn", 12))
    org = _rx(sch, r"organization|localbusiness|professionalservice")
    st.append(("entity", "ok" if (org and meta.get("has_sameas")) else ("warn" if (org or meta.get("has_sameas")) else "bad"), 12))
    st.append(("consistency", "ok" if meta.get("has_contact") else "warn", 6))
    return _score(st)


def _tech(data) -> int | None:
    sig = data.get("signals") or {}
    ix = data.get("indexation") or {}
    meta = data.get("meta") or {}
    opb = (data.get("onpage") or {}).get("broken") or {}
    if sig.get("home_status") is None and not sig.get("sitemap") and not ix.get("indexed"):
        return None
    st = []
    pages = sig.get("pages_found") or 0
    idx = ix.get("indexed_estimate") if ix.get("indexed_estimate") is not None else ((ix.get("sample_count") or 0) if ix.get("indexed") else 0)
    # indexación medida con buscador proxy (no Google directo): solo puntúa si hay dato, y nunca "bad"
    has_idx = bool(ix.get("indexed") or ix.get("indexed_estimate") is not None or (ix.get("sample_count") or 0) > 0)
    if has_idx:
        st.append(("index", "warn" if (pages > 0 and idx < pages * 0.3) else "ok", 20))
    comp = sig.get("sitemap_comp") or {}
    dirty = sig.get("sitemap") and (((comp.get("etiquetas") or 0) > (comp.get("paginas") or 0)) or ((comp.get("fichas") or 0) > (comp.get("paginas") or 0)))
    st.append(("sitemap", "bad" if not sig.get("sitemap") else ("warn" if dirty else "ok"), 14))
    rob = sig.get("robots_info") or {}
    rob_bad = bool(rob.get("blocks_all") or (rob.get("blocks_render")) or (rob.get("blocks_content")))
    st.append(("robots", "warn" if not sig.get("robots") else ("bad" if rob_bad else ("warn" if not sig.get("sitemap_in_robots") else "ok")), 10))
    if meta.get("robots_noindex"):
        st.append(("indexable", "bad", 16))
    brk = opb.get("count") if opb.get("count") is not None else (sig.get("links_broken") or 0)
    brk_idx = len(ix.get("broken_indexed") or [])
    st.append(("broken", "bad" if brk_idx > 0 else ("warn" if brk > 0 else "ok"), 18))
    hs = sig.get("home_status")
    st.append(("status", "ok" if (hs is not None and 200 <= hs < 300) else "bad", 8))
    st.append(("https", "bad" if sig.get("https") is False else ("warn" if sig.get("https_forced") is False else "ok"), 20))
    st.append(("mobile", "ok" if meta.get("viewport") else "warn", 8))
    w = sig.get("www") or {}
    if "both_ok" in w:
        st.append(("www", "bad" if w.get("duplicate") else ("warn" if w.get("one_fails") else "ok"), 10))
    return _score(st)


def _schema(data) -> int | None:
    op = data.get("onpage") or {}
    meta = data.get("meta") or {}
    types = list((op.get("schema") or {}).get("types") or [])
    for x in (meta.get("schema_types") or []):
        if x not in types:
            types.append(x)
    have = bool((op.get("schema") or {}).get("pages")) or (meta.get("schema_types") is not None)
    if not have:
        return None
    def has(p): return _rx(types, p)
    org = has(r"organization|localbusiness|professionalservice")
    website = has(r"website"); searchaction = has(r"searchaction")
    breadcrumb = has(r"breadcrumblist") or bool(((op.get("issues") or {}).get("breadcrumbs") or {}).get("present"))
    faq = has(r"faqpage|qapage"); article = has(r"article|blogposting|newsarticle")
    prodserv = has(r"product|service|offer"); review = has(r"review|aggregaterating|rating")
    sc = op.get("schema") or {}
    errs = sc.get("errors") or 0
    incompl = sc.get("incomplete") or []
    any_schema = bool((sc.get("pages_with") or 0) > 0 or len(types))
    st = [("org", org, 22), ("website", website and searchaction, 10), ("breadcrumb", breadcrumb, 12),
          ("faq", faq, 16), ("article", article, 8), ("prodserv", prodserv, 14), ("review", review, 14)]
    if any_schema:
        st.append(("valid", errs == 0 and len(incompl) == 0, 8))
    return _score_bool(st)


def _security(data) -> int | None:
    sig = data.get("signals") or {}
    sec = sig.get("security") or {}
    meta = data.get("meta") or {}
    if sec.get("score") is None and sig.get("https") is None:
        return None
    ssl = sec.get("ssl") or {}
    mixed = sec.get("mixed") or []
    miss = sec.get("headers_missing") or []
    exp = sec.get("exposed") or []
    leaks = sec.get("leaks") or []
    ck = sec.get("cookie_flags") or []
    if ssl.get("valid") is False:
        ssl_state = "bad"
    elif ssl.get("valid") is True:
        ssl_state = "warn" if (ssl.get("days_left") is not None and ssl.get("days_left") < 15) else "ok"
    else:
        ssl_state = "ok" if sig.get("https") else "bad"
    st = [("ssl", ssl_state, 20),
          ("https", "bad" if sig.get("https") is False else ("warn" if (len(mixed) or sig.get("https_forced") is False) else "ok"), 16),
          ("headers", "ok" if len(miss) == 0 else ("warn" if len(miss) <= 3 else "bad"), 16),
          ("exposed", "bad" if len(exp) else "ok", 18),
          ("leaks", "warn" if len(leaks) else "ok", 8),
          ("cookies", "warn" if len(ck) else "ok", 8),
          ("favicon", "ok" if meta.get("favicon") else "warn", 4)]
    return _score(st)


def _local(data) -> int | None:
    ai = data.get("geo_ai") or {}
    meta = data.get("meta") or {}
    op = data.get("onpage") or {}
    if not ai.get("available") and meta.get("has_address") is None and meta.get("has_phone") is None:
        return None
    types = (op.get("schema") or {}).get("types") or meta.get("schema_types") or []
    lb = _rx(types, r"localbusiness|professionalservice")
    gbp = ai.get("gbp")
    rev = ai.get("gbp_reviews_n")  # None = no se pudo leer (distinto de 0)
    has_test = bool(meta.get("has_testimonials"))
    # Reputación: reseñas reales en la ficha (mejor señal) > testimonios visibles en la web
    # (señal parcial) > ficha sin reseñas legibles > nada. None (no legible) no baja a "bad".
    if gbp is True and isinstance(rev, int) and rev > 0:
        rev_st = "ok"
    elif has_test:
        rev_st = "warn"   # la web muestra opiniones aunque no haya reseñas GBP verificadas
    elif gbp:
        rev_st = "warn"
    else:
        rev_st = "bad"
    st = [("gbp", "ok" if gbp is True else ("bad" if gbp is False else "warn"), 24),
          ("reviews", rev_st, 22),
          ("nap", "ok" if (meta.get("has_address") and meta.get("has_phone")) else ("warn" if (meta.get("has_address") or meta.get("has_phone")) else "bad"), 16),
          ("map", "ok" if (meta.get("has_map") or meta.get("has_geo")) else "warn", 12),
          ("lb", "ok" if lb else "warn", 16),
          ("hours", "ok" if meta.get("has_hours") else "warn", 10)]
    if gbp is True:
        st.append(("categoria", "ok" if ai.get("gbp_category") else "warn", 10))
    return _score(st)


def _content(data) -> int | None:
    c = (data.get("onpage") or {}).get("content")
    ca = data.get("content_ai") or {}
    meta = data.get("meta") or {}
    if not c:
        return None
    st = []
    aw = c.get("avg_words") or 0
    st.append(("depth", "ok" if aw >= 600 else ("warn" if aw >= 300 else "bad"), 20))
    dup = (c.get("duplicates") or {}).get("count", 0)
    st.append(("unique", "bad" if dup > 0 else "ok", 18))
    dated = c.get("dated_pages") or 0
    fr = (c.get("fresh_pages") or 0) / dated if dated else 0
    st.append(("fresh", "warn" if dated == 0 else ("ok" if fr >= 0.5 else "warn"), 14))
    coh = c.get("coherence_pct") or 0
    st.append(("coherence", "ok" if coh >= 70 else ("warn" if coh >= 40 else "bad"), 14))
    trust = (1 if c.get("about_page") else 0) + (1 if meta.get("has_contact") else 0) + (1 if c.get("author") else 0)
    st.append(("trust", "ok" if trust >= 2 else ("warn" if trust == 1 else "bad"), 16))
    kws = ca.get("keywords") or (data.get("geo_ai") or {}).get("keywords") or []
    if kws:
        terms = " ".join(c.get("top_terms") or []).lower()
        cov = any(any(len(w) > 3 and w in terms for w in str(k).lower().split()) for k in kws)
        st.append(("keywords", "ok" if cov else "warn", 12))
    st.append(("structure", "ok" if (c.get("clusters") or 0) >= 1 else "warn", 8))
    return _score(st)


def compute(data: dict) -> list[dict]:
    """Devuelve las dimensiones presentes, en el MISMO orden que la página:
    velocidad(móvil, escritorio), SEO on-page, GEO, técnica, schema, seguridad,
    presencia local, contenido. Cada una: {key, grp, name, score}."""
    out = []
    pf = data.get("perf2") or {}
    m, d = pf.get("mobile"), pf.get("desktop")
    if m and m.get("score") is not None:
        out.append({"key": "perf_mobile", "grp": "perf", "name": L("Velocidad en móvil", "Mobile speed"), "score": m.get("score") or 0})
    if d and d.get("score") is not None:
        out.append({"key": "perf_desktop", "grp": "perf", "name": L("Velocidad en escritorio", "Desktop speed"), "score": d.get("score") or 0})
    op = data.get("onpage") or {}
    if op.get("issues") is not None:
        out.append({"key": "onpage", "grp": "seo", "name": L("SEO on-page (Google)", "On-page SEO (Google)"), "score": (op.get("score") or 0)})
    def add(key, grp, name, val):
        if val is not None:
            out.append({"key": key, "grp": grp, "name": name, "score": val})
    add("geo", "geo", L("GEO · Visibilidad en la IA", "GEO · Visibility in AI"), _geo(data))
    add("tech", "tech", L("Salud técnica / Indexación y rastreo", "Technical health / Indexing & crawling"), _tech(data))
    add("schema", "schema", L("Datos estructurados (Schema)", "Structured data (Schema)"), _schema(data))
    add("security", "sec", L("Seguridad", "Security"), _security(data))
    add("local", "local", L("Presencia local y reputación", "Local presence & reputation"), _local(data))
    add("content", "content", L("Contenido y relevancia", "Content & relevance"), _content(data))
    return out
