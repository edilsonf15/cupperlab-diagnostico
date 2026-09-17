"""
places — Identidad del negocio y ficha de Google con DATOS (Places API New).

Es la única fuente de: ficha sí/no, nº de reseñas, valoración, categoría de Google,
dirección/ciudad/país de la sede. Nada de esto se le pregunta ya a la IA.

Por qué es fiable: solo damos por buena una ficha si su `websiteUri` casa con el
dominio analizado (o, en su defecto, el nombre normalizado coincide de forma clara y
la ciudad detectada también). Así una homónima en otra ciudad no cuenta.

Contrato de salida (como todos los módulos v2):
  {status: ok|failed|skipped, source: "places_api", elapsed_ms, note,
   data: {found: True|False|None, name, category_type, category_label, address,
          city, country_code, country, reviews, rating, maps_url, website, query}}

  found=True  -> ficha verificada de ESTE negocio
  found=False -> Google devolvió fichas para la búsqueda y ninguna es este negocio
  found=None  -> no se pudo concluir (sin clave, error, sin resultados)

Coste: Text Search con campos "Pro" (rating, userRatingCount, website...) ~0,03 €/análisis.
Habilitar "Places API (New)" en el proyecto GCP y restringir la clave a esa API.
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from difflib import SequenceMatcher

import httpx

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.id,places.displayName,places.rating,places.userRatingCount,"
          "places.primaryType,places.primaryTypeDisplayName,places.websiteUri,"
          "places.googleMapsUri,places.formattedAddress,places.addressComponents,"
          "places.businessStatus")
TIMEOUT = 8.0

# Códigos de país -> nombre (para mostrar). El resto se deja en código ISO.
_COUNTRY = {
    "ES": "España", "CO": "Colombia", "MX": "México", "AR": "Argentina", "CL": "Chile",
    "PE": "Perú", "EC": "Ecuador", "UY": "Uruguay", "PY": "Paraguay", "BO": "Bolivia",
    "VE": "Venezuela", "PA": "Panamá", "CR": "Costa Rica", "GT": "Guatemala", "SV": "El Salvador",
    "HN": "Honduras", "NI": "Nicaragua", "DO": "República Dominicana", "PR": "Puerto Rico",
    "US": "Estados Unidos", "PT": "Portugal", "BR": "Brasil", "FR": "Francia", "IT": "Italia",
    "DE": "Alemania", "GB": "Reino Unido", "IE": "Irlanda", "NL": "Países Bajos", "BE": "Bélgica",
    "CH": "Suiza", "AD": "Andorra", "CA": "Canadá", "AU": "Australia",
}


def country_name(cc: str) -> str:
    return _COUNTRY.get((cc or "").upper(), (cc or "").upper())


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(s\.?l\.?u?|s\.?a\.?s?|ltda|srl|inc|llc|gmbh|c\.?a\.?)\b\.?", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _host(u: str) -> str:
    h = re.sub(r"^https?://", "", (u or "").strip().lower()).split("/")[0].split(":")[0]
    return re.sub(r"^www\.", "", h)


def name_similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb or (len(na) >= 5 and (na in nb or nb in na)):
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _components(p: dict) -> tuple[str, str]:
    """(ciudad, código de país) desde addressComponents."""
    city, cc = "", ""
    for c in p.get("addressComponents") or []:
        types = c.get("types") or []
        if "country" in types:
            cc = (c.get("shortText") or "").upper()
        if not city and ("locality" in types or "postal_town" in types):
            city = c.get("longText") or c.get("shortText") or ""
    if not city:
        for c in p.get("addressComponents") or []:
            if "administrative_area_level_2" in (c.get("types") or []):
                city = c.get("longText") or ""
                break
    return city, cc


def _key() -> str:
    return (os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
            or os.getenv("GOOGLE_PSI_API_KEY", "").strip())


async def resolve(brand: str, domain: str, city_hint: str = "", country_hint_cc: str = "",
                  lang: str = "es") -> dict:
    """Busca la ficha de ESTE negocio. Ver contrato en la cabecera."""
    t0 = time.monotonic()
    out = {"status": "skipped", "source": "places_api", "elapsed_ms": 0, "note": "",
           "data": {"found": None, "query": ""}}
    key = _key()
    if not key:
        out["note"] = "Sin clave de Places API"
        return out
    dom = _host(domain)
    if not brand and not dom:
        return out
    brand = (brand or dom.split(".")[0]).strip()

    queries = []
    if city_hint:
        queries.append(f"{brand} {city_hint}")
    queries.append(brand)
    if dom and dom.split(".")[0] not in _norm(brand):
        queries.append(dom.split(".")[0])
    queries = list(dict.fromkeys(q for q in queries if q))[:3]

    saw_results = False
    best = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            for q in queries:
                body = {"textQuery": q, "languageCode": lang, "maxResultCount": 8}
                if country_hint_cc:
                    body["regionCode"] = country_hint_cc.upper()
                r = await c.post(PLACES_URL, json=body,
                                 headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS})
                if r.status_code != 200:
                    out["status"] = "failed"
                    out["note"] = f"Places API HTTP {r.status_code}"
                    try:
                        msg = (r.json().get("error") or {}).get("message", "")
                        if msg:
                            out["note"] += f": {msg[:120]}"
                    except Exception:  # noqa: BLE001
                        pass
                    if r.status_code in (403, 429):
                        break  # sin cuota / API deshabilitada: no insistir
                    continue
                places = r.json().get("places") or []
                if places:
                    saw_results = True
                for p in places:
                    name = (p.get("displayName") or {}).get("text", "")
                    web = _host(p.get("websiteUri") or "")
                    same_web = bool(dom and web and (web == dom or web.endswith("." + dom) or dom.endswith("." + web)))
                    sim = name_similarity(brand, name)
                    city, cc = _components(p)
                    same_city = bool(city_hint and city and _norm(city_hint) in _norm(city))
                    score = (3 if same_web else 0) + sim + (0.5 if same_city else 0)
                    accept = same_web or (sim >= 0.9 and (same_city or not city_hint)) or (sim >= 0.8 and same_city)
                    if accept and (best is None or score > best[0]):
                        best = (score, p, name, web, city, cc, q)
                if best and best[0] >= 3:
                    break  # ficha con web coincidente: no hace falta seguir
    except Exception as exc:  # noqa: BLE001
        out["status"] = "failed"
        out["note"] = f"Places API: {type(exc).__name__}"
        out["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
        return out

    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    if best:
        _, p, name, web, city, cc, q = best
        ptd = p.get("primaryTypeDisplayName")
        label = ptd.get("text", "") if isinstance(ptd, dict) else (ptd or "")
        out["status"] = "ok"
        out["data"] = {
            "found": True, "query": q, "name": name,
            "category_type": p.get("primaryType") or "",
            "category_label": label,
            "address": p.get("formattedAddress", ""),
            "city": city, "country_code": cc, "country": country_name(cc),
            "reviews": int(p.get("userRatingCount") or 0),
            "rating": p.get("rating"),
            "maps_url": p.get("googleMapsUri", ""),
            "website": web,
            "business_status": p.get("businessStatus", ""),
            "matched_by": "web" if best[0] >= 3 else "nombre",
        }
        return out
    if out["status"] == "failed":
        return out
    out["status"] = "ok"
    out["data"] = {"found": False if saw_results else None, "query": queries[0]}
    out["note"] = ("Google devolvió fichas para esa búsqueda pero ninguna corresponde a este dominio"
                   if saw_results else "Google no devolvió ninguna ficha para esa búsqueda")
    return out


# Traducción de primaryType (Google) a categoría en lenguaje de cliente. Cubre los
# tipos más frecuentes; el resto usa primaryTypeDisplayName (ya localizado por Google).
_TYPE_ES = {
    "furniture_store": "tienda de muebles", "home_goods_store": "tienda de decoración",
    "interior_designer": "estudio de interiorismo", "clothing_store": "tienda de ropa",
    "shoe_store": "zapatería", "jewelry_store": "joyería", "lawyer": "abogados",
    "law_firm": "despacho de abogados", "dentist": "clínica dental", "dental_clinic": "clínica dental",
    "doctor": "consulta médica", "physiotherapist": "fisioterapia", "acupuncturist": "acupuntura",
    "beauty_salon": "centro de estética", "hair_salon": "peluquería", "spa": "spa",
    "gym": "gimnasio", "restaurant": "restaurante", "cafe": "cafetería", "bakery": "panadería",
    "hotel": "hotel", "real_estate_agency": "inmobiliaria", "marketing_agency": "agencia de marketing",
    "advertising_agency": "agencia de publicidad", "web_designer": "diseño web",
    "software_company": "empresa de software", "accounting": "asesoría contable",
    "insurance_agency": "correduría de seguros", "car_repair": "taller mecánico",
    "car_dealer": "concesionario", "veterinary_care": "veterinario", "pet_store": "tienda de mascotas",
    "school": "academia", "university": "universidad", "travel_agency": "agencia de viajes",
    "electrician": "electricista", "plumber": "fontanero", "general_contractor": "empresa de reformas",
    "florist": "floristería", "supermarket": "supermercado", "pharmacy": "farmacia",
    "catering_service": "catering", "event_venue": "espacio para eventos", "photographer": "fotógrafo",
    "consultant": "consultoría", "wholesaler": "distribuidor", "manufacturer": "fabricante",
    "store": "tienda", "shopping_mall": "centro comercial", "moving_company": "empresa de mudanzas",
    "cleaning_service": "empresa de limpieza", "security_service": "empresa de seguridad",
}
_TYPE_EN = {k: v for k, v in {
    "furniture_store": "furniture store", "home_goods_store": "home decor store",
    "interior_designer": "interior design studio", "clothing_store": "clothing store",
    "shoe_store": "shoe store", "jewelry_store": "jewelry store", "lawyer": "lawyers",
    "law_firm": "law firm", "dentist": "dental clinic", "dental_clinic": "dental clinic",
    "doctor": "medical practice", "physiotherapist": "physiotherapy", "acupuncturist": "acupuncture",
    "beauty_salon": "beauty salon", "hair_salon": "hair salon", "spa": "spa", "gym": "gym",
    "restaurant": "restaurant", "cafe": "cafe", "bakery": "bakery", "hotel": "hotel",
    "real_estate_agency": "real estate agency", "marketing_agency": "marketing agency",
    "advertising_agency": "advertising agency", "web_designer": "web design",
    "software_company": "software company", "accounting": "accounting firm",
    "insurance_agency": "insurance agency", "car_repair": "auto repair shop",
    "car_dealer": "car dealer", "veterinary_care": "veterinarian", "pet_store": "pet store",
    "school": "school", "university": "university", "travel_agency": "travel agency",
    "electrician": "electrician", "plumber": "plumber", "general_contractor": "renovation company",
    "florist": "florist", "supermarket": "supermarket", "pharmacy": "pharmacy",
    "catering_service": "catering", "event_venue": "event venue", "photographer": "photographer",
    "consultant": "consultancy", "wholesaler": "distributor", "manufacturer": "manufacturer",
    "store": "store", "moving_company": "moving company", "cleaning_service": "cleaning service",
    "security_service": "security company",
}.items()}


def category_label(places_data: dict | None, lang: str = "es") -> str:
    """Categoría en lenguaje de cliente a partir de la ficha (vacío si no hay ficha)."""
    d = places_data or {}
    t = (d.get("category_type") or "").lower()
    table = _TYPE_EN if lang == "en" else _TYPE_ES
    if t in table:
        return table[t]
    return (d.get("category_label") or "").lower()
