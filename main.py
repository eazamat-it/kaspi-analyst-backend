from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import json
import os
from datetime import date

app = FastAPI(title="MarketAnalyst KZ API", version="2.0.0")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://kbdrjwvbkdbhjokyqlzy.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # service_role key (секретный)

# Лимиты по тарифам
PLAN_LIMITS = {
    "free": 999,
    "basic": 50,
    "pro": 999999,
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "ok", "version": "2.0.0"}

async def get_user_from_token(token: str) -> dict | None:
    """Получаем пользователя по JWT токену от Supabase"""
    if not token or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": SUPABASE_SERVICE_KEY
                }
            )
            if r.status_code == 200:
                return r.json()
    except:
        pass
    return None

async def get_user_plan(user_id: str) -> str:
    """Получаем тариф пользователя из таблицы profiles"""
    if not SUPABASE_SERVICE_KEY:
        return "free"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=plan",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
                }
            )
            if r.status_code == 200:
                data = r.json()
                if data and len(data) > 0:
                    return data[0].get("plan", "free")
    except:
        pass
    return "free"

async def get_requests_today(user_id: str) -> int:
    """Считаем количество запросов пользователя сегодня"""
    if not SUPABASE_SERVICE_KEY:
        return 0
    today = date.today().isoformat()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/search_history?user_id=eq.{user_id}&created_at=gte.{today}T00:00:00&select=id",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Prefer": "count=exact"
                }
            )
            if r.status_code == 200:
                count_header = r.headers.get("content-range", "")
                if "/" in count_header:
                    return int(count_header.split("/")[1])
                return len(r.json())
    except:
        pass
    return 0

async def get_guest_requests_today(ip: str) -> int:
    """Считаем запросы гостя по IP сегодня"""
    if not SUPABASE_SERVICE_KEY:
        return 0
    today = date.today().isoformat()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/guest_requests?ip=eq.{ip}&created_at=gte.{today}T00:00:00&select=id",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Prefer": "count=exact"
                }
            )
            if r.status_code == 200:
                count_header = r.headers.get("content-range", "")
                if "/" in count_header:
                    return int(count_header.split("/")[1])
                return len(r.json())
    except:
        pass
    return 0

async def save_guest_request(ip: str, query: str):
    """Сохраняем запрос гостя"""
    if not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/guest_requests",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json"
                },
                json={"ip": ip, "query": query}
            )
    except:
        pass

@app.post("/analyze")
async def analyze(body: dict, request: Request):
    query = body.get("query", "").strip()
    token = body.get("token")  # JWT токен от Supabase

    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    if not ANTHROPIC_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    # Определяем пользователя и лимит
    user = None
    if token:
        user = await get_user_from_token(token)

    if user:
        # Авторизованный пользователь
        user_id = user["id"]
        plan = await get_user_plan(user_id)
        limit = PLAN_LIMITS.get(plan, 3)
        used = await get_requests_today(user_id)

        if used >= limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "limit_exceeded",
                    "message": f"Лимит {limit} запросов в день исчерпан",
                    "plan": plan,
                    "used": used,
                    "limit": limit
                }
            )
    else:
        # Гость — проверяем по IP
        ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
        guest_limit = 3
        used = await get_guest_requests_today(ip)

        if used >= guest_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "limit_exceeded",
                    "message": "Бесплатный лимит исчерпан. Зарегистрируйтесь для продолжения.",
                    "plan": "guest",
                    "used": used,
                    "limit": guest_limit
                }
            )
        await save_guest_request(ip, query)

    # AI анализ
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
Цены в тенге. Все скоры от 0 до 100. Для разных товаров цифры должны отличаться."""

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
    return {"anthropic_key_set": bool(ANTHROPIC_KEY), "supabase_key_set": bool(SUPABASE_SERVICE_KEY), "status": "ok"}

async def analyze_single(query: str, buy_price: float = 0) -> dict:
    """Анализирует один товар — используется для batch обработки"""
    prompt = f"""Ты эксперт по маркетплейсу Kaspi.kz (Казахстан). Проанализируй нишу "{query}".
{f'Закупочная цена: {buy_price} тенге. Учти это при расчёте маржи и рекомендаций.' if buy_price else ''}

Верни ТОЛЬКО валидный JSON без пояснений:
{{
  "verdict": "ВОЙТИ или ОСТОРОЖНО или НЕ ВХОДИТЬ",
  "verdictText": "1-2 предложения обоснования",
  "nicheScore": 0,
  "demandScore": 0,
  "marginScore": 0,
  "competitionScore": 0,
  "optimalPrice": 0,
  "marginPct": 0,
  "monthlyProfit": 0,
  "avgPrice": 0,
  "minPrice": 0,
  "maxPrice": 0,
  "recommendation": "краткая рекомендация с учётом закупочной цены"
}}

Замени нули на реальные значения для "{query}" на казахстанском рынке. Цены в тенге."""

    async with httpx.AsyncClient(timeout=45.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        if r.status_code != 200:
            raise Exception(f"AI error: {r.status_code}")

        raw = r.json()["content"][0]["text"]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])


@app.post("/analyze-batch")
async def analyze_batch(body: dict, request: Request):
    """Batch анализ списка товаров из Excel"""
    token = body.get("token")
    items = body.get("items", [])  # [{"query": "термос", "buy_price": 5000}, ...]

    if not items:
        raise HTTPException(status_code=400, detail="Items list is required")

    if len(items) > 50:
        raise HTTPException(status_code=400, detail="Максимум 50 товаров за раз")

    if not ANTHROPIC_KEY:
        raise HTTPException(status_code=500, detail="API key not set")

    # Проверяем авторизацию — batch только для зарегистрированных
    user = None
    if token:
        user = await get_user_from_token(token)

    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Batch анализ доступен только для зарегистрированных пользователей"}
        )

    # Проверяем тариф — batch только для про
    plan = await get_user_plan(user["id"])
    if plan not in ["pro", "basic"] and len(items) > 5:
        raise HTTPException(
            status_code=403,
            detail={"error": "plan_required", "message": f"Загрузка более 5 товаров доступна на тарифе Базовый и выше. У вас: {plan}"}
        )

    results = []
    for item in items:
        query = item.get("query", "").strip()
        buy_price = float(item.get("buy_price") or 0)

        if not query:
            continue

        try:
            data = await analyze_single(query, buy_price)

            # Считаем юнит-экономику если есть закупочная цена
            unit_economics = {}
            if buy_price and data.get("optimalPrice"):
                sell = data["optimalPrice"]
                commission = sell * 0.15
                tax = sell * 0.03
                net_profit = sell - buy_price - commission - tax
                margin_pct = round(net_profit / sell * 100) if sell else 0
                unit_economics = {
                    "sell_price": sell,
                    "commission": round(commission),
                    "tax": round(tax),
                    "net_profit": round(net_profit),
                    "margin_pct": margin_pct,
                    "monthly_profit_30": round(net_profit * 30),
                }

            results.append({
                "query": query,
                "buy_price": buy_price,
                "verdict": data.get("verdict", "—"),
                "verdict_text": data.get("verdictText", ""),
                "niche_score": data.get("nicheScore", 0),
                "demand_score": data.get("demandScore", 0),
                "margin_score": data.get("marginScore", 0),
                "competition_score": data.get("competitionScore", 0),
                "optimal_price": data.get("optimalPrice", 0),
                "avg_price": data.get("avgPrice", 0),
                "margin_pct": data.get("marginPct", 0),
                "monthly_profit": data.get("monthlyProfit", 0),
                "recommendation": data.get("recommendation", ""),
                "unit_economics": unit_economics,
                "error": None
            })
        except Exception as e:
            results.append({
                "query": query,
                "buy_price": buy_price,
                "error": str(e),
                "verdict": "ОШИБКА"
            })

    return JSONResponse({"results": results, "total": len(results)})
