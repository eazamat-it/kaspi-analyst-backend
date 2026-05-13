"""
KaspiAnalyst Backend v2
FastAPI + парсинг Kaspi.kz + кэш (30 мин) + история поиска (SQLite)
Деплой: Render.com бесплатно
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import asyncio
import sqlite3
import json
import time
import hashlib
import os
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime

# ── БД: SQLite (файл на диске, бесплатно) ───────────────────────────────────
DB_PATH = "kaspi_analyst.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Таблица истории поиска
    cur.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            query       TEXT NOT NULL,
            stats_json  TEXT,
            verdict     TEXT,
            searched_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # Таблица кэша результатов (TTL = 30 минут)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key         TEXT PRIMARY KEY,
            value_json  TEXT NOT NULL,
            expires_at  INTEGER NOT NULL
        )
    """)

    # Индексы
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_query ON search_history(query)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at)")

    con.commit()
    con.close()

# ── Lifespan: инициализация БД при старте ───────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="KaspiAnalyst API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Заголовки браузера ───────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://kaspi.kz/",
    "Origin": "https://kaspi.kz",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

KASPI_SEARCH_URL = "https://kaspi.kz/yml/product-view/pl/results"
CACHE_TTL = 30 * 60  # 30 минут в секундах

# ── Модели ───────────────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 20
    page: Optional[int] = 0

class AnalyzeRequest(BaseModel):
    query: str
    force_refresh: Optional[bool] = False  # принудительно сбросить кэш

# ── Кэш (SQLite) ─────────────────────────────────────────────────────────────
def cache_get(key: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    now = int(time.time())
    cur.execute("SELECT value_json FROM cache WHERE key=? AND expires_at>?", (key, now))
    row = cur.fetchone()
    con.close()
    if row:
        return json.loads(row[0])
    return None

def cache_set(key: str, value: dict, ttl: int = CACHE_TTL):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    expires_at = int(time.time()) + ttl
    cur.execute(
        "INSERT OR REPLACE INTO cache (key, value_json, expires_at) VALUES (?, ?, ?)",
        (key, json.dumps(value, ensure_ascii=False), expires_at)
    )
    # Чистим устаревшие записи
    cur.execute("DELETE FROM cache WHERE expires_at <= ?", (int(time.time()),))
    con.commit()
    con.close()

def cache_key(query: str) -> str:
    return hashlib.md5(query.lower().strip().encode()).hexdigest()

# ── История поиска (SQLite) ──────────────────────────────────────────────────
def history_add(query: str, stats: dict, verdict: str = ""):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO search_history (query, stats_json, verdict) VALUES (?, ?, ?)",
        (query, json.dumps(stats, ensure_ascii=False), verdict)
    )
    con.commit()
    con.close()

def history_get(limit: int = 20) -> list:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT id, query, verdict, searched_at FROM search_history ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    con.close()
    return [{"id": r[0], "query": r[1], "verdict": r[2], "time": r[3]} for r in rows]

def history_popular(limit: int = 10) -> list:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT query, COUNT(*) as cnt, MAX(searched_at) as last_search
        FROM search_history
        GROUP BY lower(query)
        ORDER BY cnt DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    con.close()
    return [{"query": r[0], "count": r[1], "lastSearch": r[2]} for r in rows]

# ── Парсинг Kaspi ────────────────────────────────────────────────────────────
async def fetch_kaspi_products(query: str, limit: int = 20, page: int = 0) -> dict:
    params = {
        "q": query,
        "i": page,
        "c": "750000000",   # Алматы
        "limit": limit,
        "ui": "d",
        "sk": "0",
    }
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        try:
            r = await client.get(KASPI_SEARCH_URL, params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Kaspi ошибка {e.response.status_code}")
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Kaspi не отвечает")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

def parse_products(raw: dict) -> list[dict]:
    products = []
    for item in raw.get("data", {}).get("cards", []):
        try:
            products.append({
                "id":           item.get("id", ""),
                "name":         item.get("title", "Без названия"),
                "price":        item.get("unitPrice", 0),
                "rating":       round(item.get("rating", 0), 1),
                "reviews":      item.get("reviewsCount", 0),
                "seller":       item.get("shopInfo", {}).get("title", "Неизвестно"),
                "url":          f"https://kaspi.kz/shop/p/{item.get('slug', '')}",
                "image":        (item.get("previewImages") or [None])[0],
                "inStock":      item.get("availability") == "AVAILABLE",
                "kaspiDelivery": item.get("kaspiDelivery", False),
            })
        except Exception:
            continue
    return products

def compute_stats(products: list[dict]) -> dict:
    if not products:
        return {}

    prices  = [p["price"]  for p in products if p["price"] > 0]
    ratings = [p["rating"] for p in products if p["rating"] > 0]
    reviews = [p["reviews"] for p in products]

    avg_price    = int(sum(prices)  / len(prices))  if prices  else 0
    avg_rating   = round(sum(ratings) / len(ratings), 1) if ratings else 0
    total_reviews = sum(reviews)
    avg_reviews  = int(total_reviews / len(reviews)) if reviews else 0

    weak_sellers   = len([p for p in products if p["reviews"] < 10])
    strong_sellers = len([p for p in products if p["reviews"] >= 100])

    competition = min(100, strong_sellers * 15 + avg_reviews * 0.1)
    niche_score = max(0, int(100 - competition))
    demand_score = min(100, int(total_reviews / 50))

    price_ranges = {
        "<5K":   len([p for p in products if p["price"] < 5000]),
        "5-15K": len([p for p in products if 5000  <= p["price"] < 15000]),
        "15-35K":len([p for p in products if 15000 <= p["price"] < 35000]),
        "35-70K":len([p for p in products if 35000 <= p["price"] < 70000]),
        ">70K":  len([p for p in products if p["price"] >= 70000]),
    }

    return {
        "totalFound":      len(products),
        "avgPrice":        avg_price,
        "minPrice":        min(prices) if prices else 0,
        "maxPrice":        max(prices) if prices else 0,
        "avgRating":       avg_rating,
        "totalReviews":    total_reviews,
        "avgReviews":      avg_reviews,
        "weakSellers":     weak_sellers,
        "strongSellers":   strong_sellers,
        "nicheScore":      niche_score,
        "demandScore":     demand_score,
        "competitionScore":int(competition),
        "priceRanges":     price_ranges,
    }

# ── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "KaspiAnalyst API", "version": "2.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": int(time.time())}


@app.post("/analyze")
async def analyze_niche(req: AnalyzeRequest):
    """
    Полный анализ ниши с кэшем (30 мин) и сохранением в историю.
    """
    q   = req.query.strip()
    key = cache_key(q)

    # 1. Проверяем кэш
    if not req.force_refresh:
        cached = cache_get(key)
        if cached:
            cached["fromCache"] = True
            return cached

    # 2. Параллельно грузим 2 страницы Kaspi
    results = await asyncio.gather(
        fetch_kaspi_products(q, limit=20, page=0),
        fetch_kaspi_products(q, limit=20, page=1),
        return_exceptions=True,
    )

    all_products = []
    for r in results:
        if not isinstance(r, Exception):
            all_products.extend(parse_products(r))

    # 3. Дедупликация
    seen, unique = set(), []
    for p in all_products:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    stats = compute_stats(unique)
    top8  = sorted(unique, key=lambda x: x["reviews"], reverse=True)[:8]

    response = {
        "query":       q,
        "competitors": top8,
        "stats":       stats,
        "fromCache":   False,
        "cachedUntil": int(time.time()) + CACHE_TTL,
        "timestamp":   int(time.time()),
    }

    # 4. Сохраняем в кэш и историю
    cache_set(key, response)
    history_add(q, stats)

    return response


@app.post("/search")
async def search_products(req: SearchRequest):
    """Простой поиск товаров (без кэша, без истории)."""
    raw      = await fetch_kaspi_products(req.query, req.limit, req.page)
    products = parse_products(raw)
    stats    = compute_stats(products)
    return {
        "query":    req.query,
        "products": products,
        "stats":    stats,
        "total":    raw.get("data", {}).get("total", 0),
    }


@app.get("/history")
async def get_history(limit: int = 20):
    """Последние поиски пользователей."""
    return {"history": history_get(limit)}


@app.get("/history/popular")
async def get_popular(limit: int = 10):
    """Самые популярные запросы."""
    return {"popular": history_popular(limit)}


@app.delete("/history")
async def clear_history():
    """Очистить историю поиска."""
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM search_history")
    con.commit()
    con.close()
    return {"cleared": True}


@app.get("/cache/stats")
async def cache_stats():
    """Статистика кэша."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM cache WHERE expires_at > ?", (int(time.time()),))
    active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM search_history")
    total_searches = cur.fetchone()[0]
    con.close()
    return {"activeCacheEntries": active, "totalSearches": total_searches, "cacheTtlMinutes": CACHE_TTL // 60}
