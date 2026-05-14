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

Верни ТОЛЬКО валидный JSON, без пояснений, без markdown, без ```json:
{{
  "verdict": "ВОЙТИ или ОСТОРОЖНО или НЕ ВХОДИТЬ — выбери исходя из реального анализа ниши {query}",
  "verdictText": "2-3 предложения с обоснованием специфичным для товара {query}",
  "nicheScore": <реальная оценка привлекательности ниши {query} от 0 до 100>,
  "demandScore": <реальный спрос на {query} в Казахстане от 0 до 100>,
  "marginScore": <реальная маржинальность {query} от 0 до 100>,
  "competitionScore": <реальный уровень конкуренции {query} от 0 до 100>,
  "optimalPrice": <оптимальная цена {query} в тенге>,
  "marginPct": <процент маржи для {query}>,
  "monthlyProfit": <ожидаемая прибыль в месяц в тенге>,
  "breakEvenUnits": <количество единиц для окупаемости>,
  "freeSegments": ["свободный сегмент специфичный для {query}"],
  "topOpportunities": ["возможность 1 для {query}", "возможность 2", "возможность 3"],
  "topRisks": ["риск 1 для {query}", "риск 2"],
  "keywords": ["ключевое слово 1 для {query}", "ключевое слово 2", "ключевое слово 3"],
  "stats": {{
    "totalFound": <реальное примерное количество товаров {query} на Kaspi>,
    "avgPrice": <средняя цена {query} в тенге>,
    "minPrice": <минимальная цена {query}>,
    "maxPrice": <максимальная цена {query}>,
    "totalReviews": <общее количество отзывов>,
    "avgReviews": <среднее количество отзывов>,
    "nicheScore": <то же что выше>,
    "competitionScore": <то же что выше>,
    "demandScore": <то же что выше>
  }},
  "competitors": [
    {{"name": "реальное название товара {query}", "price": <цена>, "rating": <рейтинг>, "reviews": <отзывы>, "seller": "название магазина", "url": "https://kaspi.kz/shop/p/example", "inStock": true}}
  ]
}}

Важно: все данные должны быть УНИКАЛЬНЫМИ и РЕАЛИСТИЧНЫМИ именно для товара "{query}" на казахстанском рынке. Не используй одинаковые цифры для разных товаров."""

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
        "status": "ok"
    }
