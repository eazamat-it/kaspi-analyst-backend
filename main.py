from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import asyncio
import sqlite3
import json
import time
import hashlib

app = FastAPI(title="KaspiAnalyst API", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.options("/{rest_of_path:path}")
async def preflight(rest_of_path: str):
    return JSONResponse(content={}, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://kaspi.kz/",
    "Origin": "https://kaspi.kz",
}

KASPI_URL = "https://kaspi.kz/yml/product-view/pl/results"
DB_PATH = "kaspi.db"
CACHE_TTL = 30 * 60

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS cache (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at INTEGER NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL, verdict TEXT,
        searched_at TEXT DEFAULT (datetime('now','localtime')))""")
    con.commit()
    con.close()

init_db()

def cache_get(key):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT value FROM cache WHERE key=? AND expires_at>?", (key, int(time.time())))
    row = cur.fetchone()
    con.close()
    return json.loads(row[0]) if row else None

def cache_set(key, value):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
        (key, json.dumps(value, ensure_ascii=False), int(time.time()) + CACHE_TTL))
    con.execute("DELETE FROM cache WHERE expires_at <= ?", (int(time.time()),))
    con.commit()
    con.close()

def history_add(query, verdict=""):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT INTO history (query, verdict) VALUES (?, ?)", (query, verdict))
    con.commit()
    con.close()

def history_get(limit=20):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, query, verdict, searched_at FROM history ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    con.close()
    return [{"id": r[0], "query": r[1], "verdict": r[2], "time": r[3]} for r in rows]

async def fetch_kaspi(query, page=0):
    params = {"q": query, "i": page, "c": "750000000", "limit": 20, "ui": "d", "sk": "0"}
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        try:
            r = await client.get(KASPI_URL, params=params)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

def parse_products(raw):
    products = []
    for item in raw.get("data", {}).get("cards", []):
        try:
            products.append({
                "id":            str(item.get("id", "")),
                "name":          item.get("title", "Без названия"),
                "price":         item.get("unitPrice", 0),
                "rating":        round(float(item.get("rating", 0)), 1),
                "reviews":       item.get("reviewsCount", 0),
                "seller":        item.get("shopInfo", {}).get("title", "Неизвестно"),
                "url":           f"https://kaspi.kz/shop/p/{item.get('slug', '')}",
                "image":         (item.get("previewImages") or [None])[0],
                "inStock":       item.get("availability") == "AVAILABLE",
                "kaspiDelivery": bool(item.get("kaspiDelivery", False)),
            })
        except Exception:
            continue
    return products

def compute_stats(products):
    if not products:
        return {"totalFound": 0, "avgPrice": 0, "minPrice": 0, "maxPrice": 0,
                "avgRating": 0, "totalReviews": 0, "avgReviews": 0,
                "weakSellers": 0, "strongSellers": 0, "nicheScore": 50,
                "demandScore": 0, "competitionScore": 50,
                "priceRanges": {"<5K": 0, "5-15K": 0, "15-35K": 0, "35-70K": 0, ">70K": 0}}
    prices  = [p["price"]  for p in products if p["price"] > 0]
    ratings = [p["rating"] for p in products if p["rating"] > 0]
    reviews = [p["reviews"] for p in products]
    total_reviews  = sum(reviews)
    avg_price      = int(sum(prices)  / len(prices))  if prices  else 0
    avg_rating     = round(sum(ratings) / len(ratings), 1) if ratings else 0
    avg_reviews    = int(total_reviews / len(reviews)) if reviews else 0
    weak_sellers   = len([p for p in products if p["reviews"] < 10])
    strong_sellers = len([p for p in products if p["reviews"] >= 100])
    competition    = min(100, strong_sellers * 15 + avg_reviews * 0.1)
    return {
        "totalFound":       len(products),
        "avgPrice":         avg_price,
        "minPrice":         min(prices) if prices else 0,
        "maxPrice":         max(prices) if prices else 0,
        "avgRating":        avg_rating,
        "totalReviews":     total_reviews,
        "avgReviews":       avg_reviews,
        "weakSellers":      weak_sellers,
        "strongSellers":    strong_sellers,
        "nicheScore":       max(0, int(100 - competition)),
        "demandScore":      min(100, int(total_reviews / 50)),
        "competitionScore": int(competition),
        "priceRanges": {
            "<5K":    len([p for p in products if p["price"] < 5000]),
            "5-15K":  len([p for p in products if 5000  <= p["price"] < 15000]),
            "15-35K": len([p for p in products if 15000 <= p["price"] < 35000]),
            "35-70K": len([p for p in products if 35000 <= p["price"] < 70000]),
            ">70K":   len([p for p in products if p["price"] >= 70000]),
        },
    }

@app.get("/")
async def root():
    return JSONResponse({"status": "ok", "version": "3.1.0"},
        headers={"Access-Control-Allow-Origin": "*"})

@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy", "timestamp": int(time.time())},
        headers={"Access-Control-Allow-Origin": "*"})

@app.post("/analyze")
async def analyze(body: dict):
    query         = body.get("query", "").strip()
    force_refresh = body.get("force_refresh", False)
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    key = hashlib.md5(query.lower().encode()).hexdigest()
    if not force_refresh:
        cached = cache_get(key)
        if cached:
            cached["fromCache"] = True
            return JSONResponse(cached, headers={"Access-Control-Allow-Origin": "*"})
    results = await asyncio.gather(fetch_kaspi(query, 0), fetch_kaspi(query, 1), return_exceptions=True)
    all_products = []
    for r in results:
        if not isinstance(r, Exception):
            all_products.extend(parse_products(r))
    seen, unique = set(), []
    for p in all_products:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)
    stats = compute_stats(unique)
    top8  = sorted(unique, key=lambda x: x["reviews"], reverse=True)[:8]
    response = {
        "query": query, "competitors": top8, "stats": stats,
        "fromCache": False, "cachedUntil": int(time.time()) + CACHE_TTL,
        "timestamp": int(time.time()),
    }
    cache_set(key, response)
    history_add(query)
    return JSONResponse(response, headers={"Access-Control-Allow-Origin": "*"})

@app.post("/search")
async def search(body: dict):
    query = body.get("query", "").strip()
    page  = int(body.get("page", 0))
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    raw = await fetch_kaspi(query, page)
    products = parse_products(raw)
    return JSONResponse({"query": query, "products": products, "stats": compute_stats(products)},
        headers={"Access-Control-Allow-Origin": "*"})

@app.get("/history")
async def get_history(limit: int = 20):
    return JSONResponse({"history": history_get(limit)},
        headers={"Access-Control-Allow-Origin": "*"})

@app.delete("/history")
async def clear_history():
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM history")
    con.commit()
    con.close()
    return JSONResponse({"cleared": True}, headers={"Access-Control-Allow-Origin": "*"})
