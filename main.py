from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import json
import os

app = FastAPI(title="MarketAnalyst KZ API", version="1.0.0")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://kbdrjwvbkdbhjokyqlzy.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

Верни ТОЛЬКО валидный JSON, без пояснений, без markdown:
{{
  "verdict": "ВОЙТИ или ОСТОРОЖНО или НЕ ВХОДИТЬ — выбери исходя из реального анализа ниши {query}",
  "verdictText": "2-3 предложения с обоснованием специфичным для товара {query}",
  "nicheScore": 0,
  "demandScore": 0,
  "marginScore": 0,
  "competitionScore": 0,
  "optimalPrice": 0,
  "marginPct": 0,
  "monthlyProfit": 0,
  "breakEvenUnits": 0,
  "freeSegments": ["свободный сегмент для {query}"],
  "topOpportunities": ["возможность 1 для {query}", "возможность 2", "возможность 3"],
  "topRisks": ["риск 1 для {query}", "риск 2"],
  "keywords": ["ключевое слово 1", "ключевое слово 2", "ключевое слово 3"],
  "stats": {{
    "totalFound": 0,
    "avgPrice": 0,
    "minPrice": 0,
    "maxPrice": 0,
    "totalReviews": 0,
    "avgReviews": 0,
    "nicheScore": 0,
    "competitionScore": 0,
    "demandScore": 0
  }},
  "competitors": [
    {{"name": "название товара", "price": 0, "rating": 4.5, "reviews": 0, "seller": "магазин", "url": "https://kaspi.kz/shop/p/example", "inStock": true}}
  ]
}}

Замени все нули на РЕАЛЬНЫЕ и УНИКАЛЬНЫЕ значения для товара "{query}" на казахстанском рынке.
Цены в тенге. nicheScore, demandScore, marginScore, competitionScore — числа от 0 до 100.
Для разных товаров цифры должны отличаться."""

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
                    "model": "claude-haiku-4-5-20251001",
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
        "supabase_key_set": bool(SUPABASE_SERVICE_KEY),
        "status": "ok"
    }
