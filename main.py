from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import json
import os
import random
import asyncio
from datetime import date
from bs4 import BeautifulSoup

app = FastAPI(title="MarketAnalyst KZ API", version="3.0.0")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://kbdrjwvbkdbhjokyqlzy.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Лимиты по тарифам
PLAN_LIMITS = {
    "free": 3,
    "basic": 50,
    "pro": 999999,
}

# Прокси список (формат: ip:port:login:password)
PROXIES = [
    "142.111.48.253:7030:cnfrlllb:mao01084dvw0",
    "23.95.150.145:6114:cnfrlllb:mao01084dvw0",
    "45.38.107.97:6014:cnfrlllb:mao01084dvw0",
    "38.154.203.95:5863:cnfrlllb:mao01084dvw0",
    "198.23.243.226:6361:cnfrlllb:mao01084dvw0",
    "84.247.60.125:6095:cnfrlllb:mao01084dvw0",
    "104.239.107.47:5699:cnfrlllb:mao01084dvw0",
    "23.27.208.120:5830:cnfrlllb:mao01084dvw0",
    "23.229.19.94:8689:cnfrlllb:mao01084dvw0",
    "2.57.20.2:6983:cnfrlllb:mao01084dvw0",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def get_random_proxy() -> str:
    """Возвращает случайный прокси в формате httpx"""
    proxy = random.choice(PROXIES)
    ip, port, login, password = proxy.split(":")
    return f"http://{login}:{password}@{ip}:{port}"

def get_headers() -> dict:
    """Возвращает заголовки браузера"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }

async def parse_kaspi(query: str) -> list[dict]:
    """
    Парсит реальные товары с Kaspi.kz через прокси.
    Возвращает список товаров с реальными ценами и отзывами.
    """
    proxy = get_random_proxy()
    url = f"https://kaspi.kz/shop/search/?text={query}&page=0"

    try:
        await asyncio.sleep(random.uniform(2, 4))  # задержка чтобы не банили

        async with httpx.AsyncClient(
            proxy=proxy,
            timeout=20.0,
            follow_redirects=True
        ) as client:
            r = await client.get(url, headers=get_headers())

            if r.status_code != 200:
                print(f"Kaspi вернул статус {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            products = []

            # Парсим карточки товаров
            cards = soup.select(".item-card") or soup.select("[data-id]") or soup.select(".product-card")

            for card in cards[:20]:  # берём первые 20 товаров
                try:
                    # Название
                    name_el = card.select_one(".item-card__name a") or card.select_one(".product-name") or card.select_one("a[data-product-name]")
                    name = name_el.get_text(strip=True) if name_el else ""

                    # Цена
                    price_el = card.select_one(".item-card__prices-price") or card.select_one(".price") or card.select_one("[data-price]")
                    price_text = price_el.get_text(strip=True) if price_el else "0"
                    price = int("".join(filter(str.isdigit, price_text))) if price_text else 0

                    # Отзывы
                    reviews_el = card.select_one(".item-card__rating-count") or card.select_one(".reviews-count")
                    reviews_text = reviews_el.get_text(strip=True) if reviews_el else "0"
                    reviews = int("".join(filter(str.isdigit, reviews_text))) if reviews_text else 0

                    # Рейтинг
                    rating_el = card.select_one(".item-card__rating .rating__star-count") or card.select_one("[data-rating]")
                    rating = float(rating_el.get_text(strip=True)) if rating_el else 0.0

                    # Продавец
                    seller_el = card.select_one(".item-card__seller") or card.select_one(".seller-name")
                    seller = seller_el.get_text(strip=True) if seller_el else "Неизвестно"

                    # Ссылка
                    link_el = card.select_one("a[href*='/p/']") or card.select_one("a")
                    href = link_el.get("href", "") if link_el else ""
                    link = f"https://kaspi.kz{href}" if href.startswith("/") else href

                    if name and price > 0:
                        products.append({
                            "name": name,
                            "price": price,
                            "reviews": reviews,
                            "rating": rating,
                            "seller": seller,
                            "url": link,
                            "inStock": True
                        })
                except Exception as e:
                    print(f"Ошибка парсинга карточки: {e}")
                    continue

            print(f"Спарсили {len(products)} товаров для '{query}'")
            return products

    except Exception as e:
        print(f"Ошибка парсинга Kaspi: {e}")
        return []

def calculate_stats(products: list[dict]) -> dict:
    """Считаем реальную статистику из спарсенных товаров"""
    if not products:
        return {}

    prices = [p["price"] for p in products if p["price"] > 0]
    reviews = [p["reviews"] for p in products]

    avg_price = int(sum(prices) / len(prices)) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0
    total_reviews = sum(reviews)
    avg_reviews = int(total_reviews / len(reviews)) if reviews else 0

    # Считаем конкуренцию (0-100)
    competition_score = min(100, len(products) * 5)

    # Считаем спрос по отзывам (0-100)
    demand_score = min(100, int(avg_reviews / 10)) if avg_reviews else 20

    # Считаем нишевый скор
    niche_score = max(0, 100 - competition_score + demand_score) // 2

    return {
        "totalFound": len(products),
        "avgPrice": avg_price,
        "minPrice": min_price,
        "maxPrice": max_price,
        "totalReviews": total_reviews,
        "avgReviews": avg_reviews,
        "nicheScore": niche_score,
        "competitionScore": competition_score,
        "demandScore": demand_score,
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
    return {"status": "ok", "version": "3.0.0"}

async def get_user_from_token(token: str) -> dict | None:
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
                if data:
                    return data[0].get("plan", "free")
    except:
        pass
    return "free"

async def get_requests_today(user_id: str) -> int:
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
    token = body.get("token")

    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    if not ANTHROPIC_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    # Проверяем лимиты
    user = None
    if token:
        user = await get_user_from_token(token)

    if user:
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
        ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
        used = await get_guest_requests_today(ip)

        if used >= 3:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "limit_exceeded",
                    "message": "Бесплатный лимит исчерпан. Зарегистрируйтесь для продолжения.",
                    "plan": "guest",
                    "used": used,
                    "limit": 3
                }
            )
        await save_guest_request(ip, query)

    # ШАГ 1: Парсим реальные данные с Kaspi
    print(f"Парсим Kaspi для: {query}")
    real_products = await parse_kaspi(query)
    real_stats = calculate_stats(real_products)

    # ШАГ 2: Передаём реальные данные в Claude для анализа
    if real_products:
        products_text = json.dumps(real_products[:15], ensure_ascii=False)
        stats_text = json.dumps(real_stats, ensure_ascii=False)
        data_section = f"""
РЕАЛЬНЫЕ ДАННЫЕ С KASPI.KZ (только что спарсены):

Статистика ниши:
{stats_text}

Топ товаров конкурентов:
{products_text}

Используй ТОЛЬКО эти реальные данные для анализа. Не выдумывай цифры.
"""
    else:
        # Если парсинг не удался — честно говорим об этом
        data_section = """
ВНИМАНИЕ: Не удалось получить реальные данные с Kaspi.kz в данный момент.
Сделай анализ на основе общих знаний о казахстанском рынке, но обязательно укажи в verdictText что данные приблизительные.
"""

    prompt = f"""Ты эксперт по маркетплейсу Kaspi.kz (Казахстан). Проанализируй нишу "{query}".

{data_section}

Верни ТОЛЬКО валидный JSON без пояснений и без markdown:
{{
  "verdict": "ВОЙТИ или ОСТОРОЖНО или НЕ ВХОДИТЬ",
  "verdictText": "2-3 предложения с обоснованием на основе реальных данных",
  "nicheScore": 0,
  "demandScore": 0,
  "marginScore": 0,
  "competitionScore": 0,
  "optimalPrice": 0,
  "marginPct": 0,
  "monthlyProfit": 0,
  "breakEvenUnits": 0,
  "freeSegments": ["свободный сегмент"],
  "topOpportunities": ["возможность 1", "возможность 2", "возможность 3"],
  "topRisks": ["риск 1", "риск 2"],
  "keywords": ["ключевое слово 1", "ключевое слово 2", "ключевое слово 3"]
}}

Все скоры от 0 до 100. Цены в тенге. Анализируй на основе предоставленных реальных данных."""

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
                raise Exception(f"Anthropic error: {r_ai.status_code}")

            raw_text = r_ai.json()["content"][0]["text"]
            start = raw_text.find("{")
            end = raw_text.rfind("}") + 1
            ai_data = json.loads(raw_text[start:end])

            return JSONResponse({
                "stats": real_stats,
                "competitors": real_products[:10],
                "ai": ai_data,
                "fromCache": False,
                "realData": len(real_products) > 0  # флаг — реальные данные или нет
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
        "proxies_count": len(PROXIES),
        "status": "ok",
        "version": "3.0.0"
    }

@app.get("/test-proxy")
async def test_proxy():
    """Проверяем что прокси работают и Kaspi доступен"""
    proxy = get_random_proxy()
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=15.0) as client:
            r = await client.get(
                "https://kaspi.kz/shop/search/?text=термос",
                headers=get_headers()
            )
            return {
                "status": r.status_code,
                "proxy_used": proxy.split("@")[1],  # только IP без пароля
                "kaspi_accessible": r.status_code == 200
            }
    except Exception as e:
        return {"error": str(e), "kaspi_accessible": False}
