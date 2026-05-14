from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import json
import os

app = FastAPI(title="KaspiAnalyst API", version="3.5.0")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://eazamat-it.github.io", "http://localhost", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "ok", "key_set": bool(ANTHROPIC_KEY)}

@app.post("/analyze")
async def analyze(body: dict):
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    if not ANTHROPIC_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    prompt = f"""Ты эксперт по маркетплейсу Kaspi.kz (Казахстан). Проанализируй нишу "{query}".

Верни ТОЛЬКО валидный JSON, без пояснений, без markdown, без ```json:
{{
  "verdict": "ВОЙТИ",
  "verdictText": "2-3 предложения с обоснованием",
  "nicheScore": 75,
  "demandScore": 80,
  "marginScore": 60,
  "competitionScore": 45,
  "optimalPrice": 15000,
  "marginPct": 35,
  "monthlyProfit": 250000,
  "breakEvenUnits": 20,
  "freeSegments": ["сегмент 1", "сегмент 2"],
  "topOpportunities": ["возможность 1", "возможность 2", "возможность 3"],
  "topRisks": ["риск 1", "риск 2"],
  "keywords": ["ключ 1", "ключ 2", "ключ 3", "ключ 4"],
  "stats": {{
    "totalFound": 120,
    "avgPrice": 14500,
    "minPrice": 5000,
    "maxPrice": 45000,
    "totalReviews": 8500,
    "avgReviews": 70,
    "nicheScore": 75,
    "competitionScore": 45,
    "demandScore": 80
  }},
  "competitors": [
    {{"name": "Название товара", "price": 12000, "rating": 4.7, "reviews": 320, "seller": "Магазин", "url": "https://kaspi.kz/shop/p/example-1", "inStock": true}},
    {{"name": "Название товара 2", "price": 15500, "rating": 4.5, "reviews": 180, "seller": "Магазин 2", "url": "https://kaspi.kz/shop/p/example-2", "inStock": true}},
    {{"name": "Название товара 3", "price": 9900, "rating": 4.3, "reviews": 95, "seller": "Магазин 3", "url": "https://kaspi.kz/shop/p/example-3", "inStock": true}},
    {{"name": "Название товара 4", "price": 18000, "rating": 4.8, "reviews": 450, "seller": "Магазин 4", "url": "https://kaspi.kz/shop/p/example-4", "inStock": true}},
    {{"name": "Название товара 5", "price": 11000, "rating": 4.2, "reviews": 60, "seller": "Магазин 5", "url": "https://kaspi.kz/shop/p/example-5", "inStock": false}}
  ]
}}

Важно: verdict может быть только "ВОЙТИ", "ОСТОРОЖНО" или "НЕ ВХОДИТЬ". Все цены в тенге. Данные должны быть реалистичными для казахстанского рынка."""

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
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )

            if r_ai.status_code != 200:
                raise Exception(f"Anthropic error: {r_ai.status_code} {r_ai.text}")

            raw_text = r_ai.json()["content"][0]["text"]
            start = raw_text.find("{")
            end = raw_text.rfind("}") + 1
            data = json.loads(raw_text[start:end])

            stats = data.pop("stats", {})
            competitors = data.pop("competitors", [])

            return JSONResponse({
                "stats": stats,
                "competitors": competitors,
                "ai": data,
                "fromCache": False
            })

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON parse error: {e}")
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/test")
async def test():
    return {
        "anthropic_key_set": bool(ANTHROPIC_KEY),
        "status": "ok"
    }
