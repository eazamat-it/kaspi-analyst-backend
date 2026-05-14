from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import asyncio
import sqlite3
import json
import time
import hashlib
import os

app = FastAPI(title="KaspiAnalyst API", version="3.2.0")

# Читаем ключ Anthropic из переменных окружения Render
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://kaspi.kz",
}

KASPI_URL = "https://kaspi.kz/yml/product-view/pl/results"
DB_PATH = "kaspi.db"

# --- DB & CACHE LOGIC ---
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires_at INTEGER)")
    con.commit()
    con.close()

init_db()

async def fetch_kaspi(query, page=0):
    params = {"q": query, "i": page, "c": "750000000", "limit": 20}
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        try:
            r = await client.get(KASPI_URL, params=params)
            return r.json()
        except: return {}

def parse_products(raw):
    products = []
    for item in raw.get("data", {}).get("cards", []):
        products.append({
            "id": str(item.get("id")),
            "name": item.get("title"),
            "price": item.get("unitPrice", 0),
            "rating": round(float(item.get("rating", 0)), 1),
            "reviews": item.get("reviewsCount", 0),
            "seller": item.get("shopInfo", {}).get("title"),
            "url": f"https://kaspi.kz/shop/p/{item.get('slug')}",
            "inStock": item.get("availability") == "AVAILABLE"
        })
    return products

def compute_stats(products):
    if not products: return {"totalFound":0, "avgPrice":0}
    prices = [p["price"] for p in products if p["price"] > 0]
    reviews = [p["reviews"] for p in products]
    avg_price = int(sum(prices)/len(prices)) if prices else 0
    total_rev = sum(reviews)
    avg_rev = int(total_rev/len(products))
    weak = len([p for p in products if p["reviews"] < 10])
    strong = len([p for p in products if p["reviews"] >= 100])
    comp = min(100, strong * 15 + avg_rev * 0.1)
    
    return {
        "totalFound": len(products),
        "avgPrice": avg_price,
        "minPrice": min(prices) if prices else 0,
        "maxPrice": max(prices) if prices else 0,
        "totalReviews": total_rev,
        "avgReviews": avg_rev,
        "weakSellers": weak,
        "strongSellers": strong,
        "nicheScore": max(0, int(100 - comp)),
        "competitionScore": int(comp),
        "demandScore": min(100, int(total_rev / 50)),
        "priceRanges": {"<5K": 0, "5-15K": 0, "15-35K": 0, "35-70K": 0, ">70K": 0} # Для упрощения
    }

@app.post("/analyze")
async def analyze(body: dict):
    query = body.get("query", "").strip()
    if not query: raise HTTPException(status_code=400)

    # 1. Данные Kaspi
    res = await asyncio.gather(fetch_kaspi(query, 0), fetch_kaspi(query, 1))
    prods = []
    for r in res: prods.extend(parse_products(r))
    
    seen, unique = set(), []
    for p in prods:
        if p["id"] not in seen:
            seen.add(p["id"]); unique.append(p)
            
    stats = compute_stats(unique)
    top = sorted(unique, key=lambda x: x["reviews"], reverse=True)[:8]

    # 2. Запрос к AI (Claude)
    ai_final = {}
    if ANTHROPIC_KEY:
        prompt = f"Ниша: {query}. Статистика: {stats}. Ответь JSON по шаблону: nicheScore, competitionScore, demandScore, marginScore, verdict, verdictText, optimalPrice, marginPct, monthlyProfit, breakEvenUnits, priceTrend, priceTrendText, seasonality, freeSegments[], topOpportunities[], topRisks[], keywords[], entryStrategy, reviewTip, pricingTip"
        
        async with httpx.AsyncClient(timeout=40.0) as client:
            r_ai = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-3-haiku-20240307",
                    "max_tokens": 1000,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            if r_ai.status_code == 200:
                raw = r_ai.json()["content"][0]["text"]
                ai_final = json.loads(raw.replace("```json", "").replace("```", "").strip())

    return JSONResponse({
        "stats": stats,
        "competitors": top,
        "ai": ai_final,
        "fromCache": False
    })
