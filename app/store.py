"""
store — persistencia ligera en SQLite (fichero en el volumen ./data).

Sustituye a los diccionarios en memoria de main.py (_jobs, _RESULT_CACHE, _hits,
leads.jsonl). Qué gana el negocio con esto:
  - Un deploy o un reinicio a mitad de análisis NO pierde el job ni la caché.
  - La caché por dominio dura 24 h (RESULT_CACHE_TTL) y sobrevive reinicios: el
    mismo dominio no vuelve a gastar IA ni APIs.
  - Los leads quedan en una tabla consultable (y siguen escribiéndose en JSONL).
  - Si algún día hace falta un 2º worker, comparten el mismo fichero.

Todo es síncrono y minúsculo (una fila por operación); se llama desde asyncio sin
bloquear de forma apreciable. Un lock protege la conexión compartida.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

# La clave de caché incluye la versión del motor: al desplegar una versión nueva
# los resultados viejos dejan de servirse (sin tener que borrar nada a mano).
# _CACHE_REV es un salto de versión DEL CÓDIGO: subirlo invalida TODA la caché sin
# depender de la variable de entorno (arranca todos los análisis limpios).
_CACHE_REV = "r5-2026-09-17"
ENGINE_VERSION = os.getenv("ENGINE_VERSION", "v2.1")
CACHE_TTL = float(os.getenv("RESULT_CACHE_TTL", str(24 * 3600)))
JOB_TTL = float(os.getenv("JOB_TTL", str(6 * 3600)))

_DB_PATH = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "app.db"
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, ts REAL, progress REAL, stage TEXT, done INTEGER,
            error TEXT, result TEXT, url TEXT, lang TEXT);
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, ts REAL, result TEXT, ai TEXT);
        CREATE TABLE IF NOT EXISTS hits (ip TEXT, ts REAL);
        CREATE INDEX IF NOT EXISTS hits_ip ON hits(ip, ts);
        CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, data TEXT);
        CREATE TABLE IF NOT EXISTS bench_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, domain TEXT, ok INTEGER,
            elapsed REAL, result TEXT, failures TEXT);
        """)
    return _conn


# ------------------------------------------------------------------ jobs
def job_create(job_id: str, url: str, lang: str, stage: str) -> None:
    with _lock:
        _db().execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                      (job_id, time.time(), 3, stage, 0, None, None, url, lang))


def job_get(job_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT progress, stage, done, error, result FROM jobs WHERE id=?",
                            (job_id,)).fetchone()
    if not row:
        return None
    return {"progress": row[0], "stage": row[1], "done": bool(row[2]), "error": row[3],
            "result": json.loads(row[4]) if row[4] else None}


def job_progress(job_id: str, progress: float, stage: str | None = None) -> None:
    """El progreso solo sube (nunca retrocede en pantalla)."""
    with _lock:
        if stage is None:
            _db().execute("UPDATE jobs SET progress=MAX(progress,?) WHERE id=?", (progress, job_id))
        else:
            _db().execute("UPDATE jobs SET progress=MAX(progress,?), stage=? WHERE id=?",
                          (progress, stage, job_id))


def job_finish(job_id: str, result: dict | None, error: str | None = None) -> None:
    with _lock:
        _db().execute("UPDATE jobs SET done=1, progress=100, stage=?, error=?, result=? WHERE id=?",
                      ("Listo" if not error else "Error", error,
                       json.dumps(result, ensure_ascii=False) if result is not None else None, job_id))


def job_is_done(job_id: str) -> bool:
    with _lock:
        row = _db().execute("SELECT done FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row[0])


# ----------------------------------------------------------------- cache
def cache_key(domain: str, lang: str) -> str:
    return f"{ENGINE_VERSION}|{_CACHE_REV}|{domain.lower()}|{lang}"


def cache_get(key: str) -> tuple[dict, dict | None] | None:
    with _lock:
        row = _db().execute("SELECT ts, result, ai FROM cache WHERE key=?", (key,)).fetchone()
    if not row or (time.time() - row[0]) > CACHE_TTL:
        return None
    return json.loads(row[1]), (json.loads(row[2]) if row[2] else None)


def cache_put(key: str, result: dict, ai: dict | None) -> None:
    with _lock:
        _db().execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
                      (key, time.time(), json.dumps(result, ensure_ascii=False),
                       json.dumps(ai, ensure_ascii=False) if ai is not None else None))


# ------------------------------------------------------------- rate limit
def rate_ok(ip: str, limit: int, window_s: int = 3600) -> bool:
    now = time.time()
    with _lock:
        db = _db()
        db.execute("DELETE FROM hits WHERE ts < ?", (now - window_s,))
        n = db.execute("SELECT COUNT(*) FROM hits WHERE ip=?", (ip,)).fetchone()[0]
        if n >= limit:
            return False
        db.execute("INSERT INTO hits VALUES (?,?)", (ip, now))
    return True


# ----------------------------------------------------------------- leads
def lead_add(lead: dict) -> None:
    with _lock:
        _db().execute("INSERT INTO leads (ts, data) VALUES (?,?)",
                      (time.time(), json.dumps(lead, ensure_ascii=False)))


# ----------------------------------------------------------------- bench
def bench_add(domain: str, ok: bool, elapsed: float, result: dict, failures: list) -> None:
    with _lock:
        _db().execute("INSERT INTO bench_runs (ts, domain, ok, elapsed, result, failures) VALUES (?,?,?,?,?,?)",
                      (time.time(), domain, int(ok), elapsed, json.dumps(result, ensure_ascii=False),
                       json.dumps(failures, ensure_ascii=False)))


# -------------------------------------------------------------- limpieza
def cleanup() -> None:
    """Borra jobs viejos y caché caducada. Se llama al arrancar y cada N análisis."""
    now = time.time()
    with _lock:
        db = _db()
        db.execute("DELETE FROM jobs WHERE ts < ?", (now - JOB_TTL,))
        db.execute("DELETE FROM cache WHERE ts < ?", (now - CACHE_TTL,))
        db.execute("DELETE FROM hits WHERE ts < ?", (now - 3600,))
