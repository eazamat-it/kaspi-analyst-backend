from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import asyncio
import json
import os

app = FastAPI(title="KaspiAnalyst API", version="3.5.0")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

# Настройка CORS
origins = [
    "https://eazamat-it.github.io",
    "http://localhost",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://kaspi.kz",
}

KASPI_URL = "https://kaspi.kz/yml/product-view/pl/results"

async def fetch_kaspi(query, page=0):
    params = {"q": query, "i": page, "c": "750000000", "limit": 20}
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        try:
            r = await client.get(KASPI_URL, params=params)
            return r.json()
        except Exception:
            return {}

def parse_products(raw):
    products = []
    for item in raw.get("data", {}).get("cards", []):
        products.append({
            "id": str(item.get("id")),
            "name": item.get("title"),
            "price": item.get("unitPrice", 0),
            "rating": round(float(item.get("rating", 0)), 1),
            "reviews": item.get("reviewsCount", 0),
            "seller": item.get("shopInfo", {}).get("title", "Kaspi Магазин"),
            "url": f"https://kaspi.kz/shop/p/{item.get('slug')}",
            "inStock": item.get("availability") == "AVAILABLE"
        })
    return products

def compute_stats(products):
    if not products: return {"totalFound": 0, "avgPrice": 0, "totalReviews": 0}
    prices = [p["price"] for p in products if p["price"] > 0]
    reviews = [p["reviews"] for p in products]
    
    total_found = len(products)
    avg_price = int(sum(prices)/len(prices)) if prices else 0
    total_rev = sum(reviews)
    
    # Индексы для AI
    strong = len([p for p in products if p["reviews"] >= 100])
    avg_rev = int(total_rev/total_found) if total_found > 0 else 0
    comp = min(100, strong * 15 + avg_rev * 0.1)
    
    return {
        "totalFound": total_found,
        "avgPrice": avg_price,
        "minPrice": min(prices) if prices else 0,
        "maxPrice": max(prices) if prices else 0,
        "totalReviews": total_rev,
        "avgReviews": avg_rev,
        "nicheScore": max(0, int(100 - comp)),
        "competitionScore": int(comp),
        "demandScore": min(100, int(total_rev / 50))
    }

@app.post("/analyze")
async def analyze(body: dict):
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    responses = await asyncio.gather(fetch_kaspi(query, 0), fetch_kaspi(query, 1))
    all_products = []
    for r in responses:
        all_products.extend(parse_products(r))
    
    seen, unique = set(), []
    for p in all_products:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)
            
    stats = compute_stats(unique)
    top_competitors = sorted(unique, key=lambda x: x["reviews"], reverse=True)[:8]

    ai_analysis = {}
    if ANTHROPIC_KEY:
        prompt = (
            f"Проанализируй товар '{query}' на Kaspi.kz.\n"
            f"Статистика: {json.dumps(stats)}.\n"
            "Верни ТОЛЬКО JSON: { \"verdict\": \"ВОЙТИ/ОСТОРОЖНО/НЕ ВХОДИТЬ\", \"verdictText\": \"...\", "
            "\"nicheScore\": 0-100, \"demandScore\": 0-100, \"marginScore\": 0-100, \"competitionScore\": 0-100, "
            "\"optimalPrice\": число, \"marginPct\": число, \"monthlyProfit\": число, \"breakEvenUnits\": число, "
            "\"freeSegments\": [], \"topOpportunities\": [], \"topRisks\": [], \"keywords\": [] }"
        )

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
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
                    raw_text = r_ai.json()["content"][0]["text"]
                    # Очистка JSON от лишнего текста
                    start = raw_text.find("{")
                    end = raw_text.rfind("}") + 1
                    ai_analysis = json.loads(raw_text[start:end])
        except Exception as e:
            print(f"AI Error: {e}")
            ai_analysis = {"error": "AI Error"}

    return JSONResponse({
        "stats": stats,
        "competitors": top_competitors,
        "ai": ai_analysis,
        "fromCache": False
    })

@app.get("/test")
async def test():
    kaspi_result = await fetch_kaspi("термос", 0)
    return {
        "anthropic_key_set": bool(ANTHROPIC_KEY),
        "kaspi_data_count": len(kaspi_result.get("data", {}).get("cards", [])),
        "kaspi_raw_keys": list(kaspi_result.keys())
    }
