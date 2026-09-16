"""
Banco de pruebas del motor v2: corre el pipeline real contra dominios con verdades
conocidas y comprueba aserciones. Es lo que evita el "a veces funciona, a veces no".

Uso (raíz del repo): python bench/run.py [--only dominio] [--no-cache]
Sale con código 1 si algún caso falla. Guarda cada corrida en la tabla bench_runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))

import yaml  # noqa: E402

import main  # noqa: E402
import store  # noqa: E402
import geo_ai  # noqa: E402


def _norm(s):
    return geo_ai._places._norm(str(s or ""))


def check(case: dict, r: dict, elapsed: float, max_seconds: float) -> list[str]:
    f: list[str] = []
    exp = case.get("expect") or {}
    idt = r.get("identity") or {}
    ai = r.get("geo_ai") or {}
    if elapsed > max_seconds:
        f.append(f"tiempo {elapsed:.0f}s > {max_seconds}s")
    if "country" in exp and _norm(idt.get("country")) != _norm(exp["country"]):
        f.append(f"país {idt.get('country')!r} != {exp['country']!r}")
    if "city" in exp and _norm(exp["city"]) not in _norm(idt.get("city")):
        f.append(f"ciudad {idt.get('city')!r} no contiene {exp['city']!r}")
    if "scope" in exp and idt.get("scope") != exp["scope"]:
        f.append(f"ámbito {idt.get('scope')!r} != {exp['scope']!r}")
    if "gbp" in exp and ai.get("gbp") is not None and ai.get("gbp") != exp["gbp"]:
        f.append(f"ficha {ai.get('gbp')} != {exp['gbp']}")
    if "category_contains" in exp and _norm(exp["category_contains"]) not in _norm(idt.get("category") or ai.get("sector")):
        f.append(f"categoría {idt.get('category') or ai.get('sector')!r} no contiene {exp['category_contains']!r}")
    if "blocked" in exp and bool((r.get("meta") or {}).get("blocked")) != exp["blocked"]:
        f.append("blocked no coincide")
    # Invariantes del motor (siempre)
    keys = {d["key"] for d in r.get("dims") or []} | {x["key"] for x in r.get("not_measured") or []}
    for k in ("tech", "schema", "security", "local"):
        if k not in keys:
            f.append(f"dimensión {k} ni medida ni declarada como no medida")
    for d in r.get("dims") or []:
        if not isinstance(d.get("score"), (int, float)):
            f.append(f"dims[{d.get('key')}] sin score numérico")
    for x in r.get("not_measured") or []:
        if not x.get("note"):
            f.append(f"not_measured[{x['key']}] sin motivo")
    brand = _norm(idt.get("brand"))
    snippet_tokens = geo_ai._tokens((idt.get("snippet") or ""))
    for q in ai.get("category_queries") or []:
        if brand and brand in _norm(q):
            f.append(f"búsqueda contiene la marca: {q!r}")
        qt = geo_ai._tokens(q)
        if snippet_tokens and len(qt & snippet_tokens) >= max(4, int(len(qt) * 0.7)):
            f.append(f"búsqueda parece el eslogan de la web: {q!r}")
    if ai.get("status") == "ok":
        for c in ai.get("competitors") or []:
            if not c.get("cited_by"):
                f.append(f"competidor sin fuente: {c.get('name')}")
    if ai.get("gbp") is True and ai.get("gbp_source") != "places_api":
        f.append("ficha=True sin venir de Places API")
    return f


async def run_case(case: dict, max_seconds: float, use_cache: bool) -> tuple[bool, float, dict, list[str]]:
    dom = case["domain"]
    url = dom if dom.startswith("http") else f"https://{dom}"
    lang = case.get("lang", "es")
    if not use_cache:
        store.cache_put(store.cache_key(main._domain_key(url), lang), {}, None)  # invalida
        store._db().execute("DELETE FROM cache WHERE key=?", (store.cache_key(main._domain_key(url), lang),))
    jid = f"bench-{int(time.time() * 1000)}"
    store.job_create(jid, url, lang, "bench")
    t = time.time()
    try:
        await asyncio.wait_for(main._pipeline(jid, url, lang), timeout=main.HARD_TIMEOUT + 15)
    except Exception as exc:  # noqa: BLE001
        return False, time.time() - t, {}, [f"excepción: {type(exc).__name__}: {exc}"]
    el = time.time() - t
    j = store.job_get(jid) or {}
    r = j.get("result") or {}
    if not r:
        return False, el, {}, [f"sin resultado: {j.get('error')}"]
    fails = check(case, r, el, max_seconds)
    return not fails, el, r, fails


async def amain(only: str | None, use_cache: bool):
    cfg = yaml.safe_load(open(Path(__file__).resolve().parent / "domains.yaml", encoding="utf-8"))
    cases = [c for c in cfg["cases"] if not only or only in c["domain"]]
    ok_all = True
    for c in cases:
        ok, el, r, fails = await run_case(c, float(cfg.get("max_seconds", 125)), use_cache)
        store.bench_add(c["domain"], ok, el, {k: r.get(k) for k in ("identity", "dims", "not_measured", "score")}, fails)
        idt = r.get("identity") or {}
        ai = r.get("geo_ai") or {}
        print(f"{'OK ' if ok else 'FAIL'} {c['domain']:<28} {el:5.1f}s  país={idt.get('country')!s:<10} ciudad={idt.get('city')!s:<12} "
              f"ámbito={idt.get('scope')!s:<6} cat={str(idt.get('category') or ai.get('sector'))[:28]!s:<28} "
              f"ficha={ai.get('gbp')!s:<5} dims={len(r.get('dims') or [])} nm={[x['key'] for x in r.get('not_measured') or []]}")
        for q in ai.get("category_queries") or []:
            print(f"      · {q}")
        for fl in fails:
            print(f"      ✗ {fl}")
        ok_all &= ok
    print("\nRESULTADO:", "TODO OK" if ok_all else "HAY FALLOS")
    return 0 if ok_all else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(amain(a.only, not a.no_cache)))
