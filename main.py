from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import json
import re
import os
import random
import asyncio
import math
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
    all_products = []
    seen_ids = set()
    pages_to_fetch = [0, 1, 2]  # 3 страницы = ~36 товаров

    async with httpx.AsyncClient(
        proxy=get_random_proxy(),
        timeout=25.0,
        follow_redirects=True
    ) as client:
        for page in pages_to_fetch:
            if len(all_products) >= 36:
                break

            url = f"https://kaspi.kz/shop/search/?text={query}&page={page}"

            try:
                await asyncio.sleep(random.uniform(1.5, 3.0))
                r = await client.get(url, headers=get_headers())

                if r.status_code != 200:
                    print(f"Kaspi вернул статус {r.status_code} на странице {page}")
                    break

                products = _extract_products_from_html(r.text, seen_ids)
                if not products:
                    print(f"Страница {page}: товары не найдены, останавливаем")
                    break

                all_products.extend(products)
                print(f"Страница {page}: +{len(products)} товаров (всего {len(all_products)})")

            except Exception as e:
                print(f"Ошибка на странице {page}: {e}")
                break

    print(f"Итого спарсили {len(all_products)} товаров для '{query}'")
    return all_products


def _extract_products_from_html(html: str, seen_ids: set) -> list[dict]:
    """
    Kaspi — SPA, все данные встроены в <script> как window.__initial_state__.
    Извлекаем JSON оттуда, это даёт реальные отзывы, рейтинги, продавцов.
    Если JSON не найден — падаем на HTML-парсинг как запасной вариант.
    """
    products = []

    # --- Способ 1: window.__initial_state__ или похожий JSON в <script> ---
    try:
        # Kaspi кладёт данные примерно так:
        # window.__initial_state__={"searchResults":{"cards":[...]}}
        # или DATA_LAYER, или __NEXT_DATA__
        patterns = [
            r'window\.__initial_state__\s*=\s*(\{.+?\});\s*(?:window|</script>)',
            r'window\.__DATA__\s*=\s*(\{.+?\});\s*(?:window|</script>)',
            r'<script id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                raw = m.group(1)
                data = json.loads(raw)
                extracted = _parse_initial_state(data, seen_ids)
                if extracted:
                    return extracted

        # Kaspi также встраивает данные как отдельный JSON-массив карточек
        # ищем "cards" или "products" массив внутри любого <script>
        cards_match = re.search(
            r'"cards"\s*:\s*(\[.+?\])\s*[,}]',
            html, re.DOTALL
        )
        if cards_match:
            cards_json = json.loads(cards_match.group(1))
            for item in cards_json:
                product = _parse_card_json(item, seen_ids)
                if product:
                    products.append(product)
            if products:
                return products

    except Exception as e:
        print(f"JSON-парсинг не удался: {e}")

    # --- Способ 2: HTML data-атрибуты (запасной) ---
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Kaspi кладёт данные в data-атрибуты на карточках
        cards = (
            soup.select("div[data-product-id]") or
            soup.select("div[data-id]") or
            soup.select(".item-card") or
            soup.select(".product-card")
        )

        for card in cards:
            try:
                prod_id = card.get("data-product-id") or card.get("data-id") or ""
                if prod_id and prod_id in seen_ids:
                    continue

                name_el = (
                    card.select_one("a.item-card__name-link") or
                    card.select_one(".item-card__name a") or
                    card.select_one("[data-product-name]") or
                    card.select_one("a[href*='/p/']")
                )
                name = name_el.get_text(strip=True) if name_el else ""

                price_el = (
                    card.select_one(".item-card__prices-price") or
                    card.select_one("[data-price]") or
                    card.select_one(".price__value")
                )
                price_text = price_el.get_text(strip=True) if price_el else "0"
                price = int("".join(filter(str.isdigit, price_text))) if price_text else 0

                # Отзывы — Kaspi пишет "(1 234)" рядом со звёздами
                reviews_el = (
                    card.select_one(".item-card__rating-count") or
                    card.select_one(".rating__reviews-count") or
                    card.select_one("[data-reviews-quantity]")
                )
                if reviews_el:
                    rev_text = "".join(filter(str.isdigit, reviews_el.get_text()))
                    reviews = int(rev_text) if rev_text else 0
                else:
                    # data-атрибут напрямую
                    reviews = int(card.get("data-reviews-quantity", 0) or 0)

                # Рейтинг
                rating_el = card.select_one("[data-rating]")
                if rating_el:
                    try:
                        rating = float(rating_el.get("data-rating", 0))
                    except:
                        rating = 0.0
                else:
                    star_el = card.select_one(".rating__star-count")
                    try:
                        rating = float(star_el.get_text(strip=True)) if star_el else 0.0
                    except:
                        rating = 0.0

                seller_el = (
                    card.select_one(".item-card__seller-name") or
                    card.select_one(".merchants-name") or
                    card.select_one("[data-seller-name]")
                )
                seller = seller_el.get_text(strip=True) if seller_el else "Неизвестно"

                link_el = card.select_one("a[href*='/p/']") or card.select_one("a")
                href = link_el.get("href", "") if link_el else ""
                link = f"https://kaspi.kz{href}" if href.startswith("/") else href

                if name and price > 0:
                    if prod_id:
                        seen_ids.add(prod_id)
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

    except Exception as e:
        print(f"HTML-парсинг не удался: {e}")

    return products


def _parse_initial_state(data: dict, seen_ids: set) -> list[dict]:
    """Рекурсивно ищем массив карточек в window.__initial_state__"""
    products = []

    def find_cards(obj, depth=0):
        if depth > 6 or not isinstance(obj, (dict, list)):
            return
        if isinstance(obj, list):
            # Если элементы похожи на карточки товаров
            if obj and isinstance(obj[0], dict) and any(
                k in obj[0] for k in ("id", "productId", "reviewsQuantity", "unitPrice")
            ):
                for item in obj:
                    p = _parse_card_json(item, seen_ids)
                    if p:
                        products.append(p)
                return
            for item in obj:
                find_cards(item, depth + 1)
        elif isinstance(obj, dict):
            for v in obj.values():
                find_cards(v, depth + 1)

    find_cards(data)
    return products


def _parse_card_json(item: dict, seen_ids: set) -> dict | None:
    """Парсим одну карточку из JSON (window.__initial_state__ или data-атрибуты)"""
    try:
        prod_id = str(item.get("id") or item.get("productId") or item.get("skuId") or "")
        if prod_id and prod_id in seen_ids:
            return None

        name = (
            item.get("name") or item.get("title") or
            item.get("productName") or item.get("fullName") or ""
        )

        # Цена может быть в разных полях
        price = int(
            item.get("unitPrice") or item.get("price") or
            item.get("minPrice") or item.get("priceMin") or 0
        )

        # Отзывы — ключевое поле которое раньше терялось
        reviews = int(
            item.get("reviewsQuantity") or item.get("reviewCount") or
            item.get("reviews") or item.get("ratingsCount") or 0
        )

        # Рейтинг
        try:
            rating = float(
                item.get("rating") or item.get("averageRating") or
                item.get("reviewRating") or 0
            )
        except:
            rating = 0.0

        # Продавец
        seller = (
            item.get("shopName") or item.get("merchantName") or
            item.get("sellerName") or item.get("merchantTitle") or "Неизвестно"
        )
        if isinstance(seller, dict):
            seller = seller.get("name") or seller.get("title") or "Неизвестно"

        # Ссылка
        slug = item.get("slug") or item.get("url") or ""
        link = f"https://kaspi.kz/shop/p/{slug}" if slug and not slug.startswith("http") else slug

        if name and price > 0:
            if prod_id:
                seen_ids.add(prod_id)
            return {
                "name": name,
                "price": price,
                "reviews": reviews,
                "rating": rating,
                "seller": seller,
                "url": link,
                "inStock": True
            }
    except Exception as e:
        print(f"Ошибка _parse_card_json: {e}")
    return None

def calculate_stats(products: list[dict]) -> dict:
    """Считаем реальную статистику из спарсенных товаров"""
    if not products:
        return {}

    prices = [p["price"] for p in products if p["price"] > 0]
    reviews = [p["reviews"] for p in products]
    ratings = [p["rating"] for p in products if p.get("rating", 0) > 0]

    avg_price = int(sum(prices) / len(prices)) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0
    total_reviews = sum(reviews)
    avg_reviews = int(total_reviews / len(reviews)) if reviews else 0
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0.0

    # Разброс цен — показатель насыщенности ниши
    price_spread_pct = int((max_price - min_price) / avg_price * 100) if avg_price > 0 else 0

    # --- competitionScore (0-100) ---
    # Считаем уникальных продавцов
    unique_sellers = len(set(p.get("seller", "") for p in products if p.get("seller", "") not in ("", "Неизвестно")))
    seller_count = unique_sellers if unique_sellers > 0 else len(products)

    # Логарифмическая шкала: 1 продавец → ~0, 5 → ~40, 10 → ~60, 20+ → ~85
    seller_factor = min(85, int(math.log1p(seller_count) / math.log1p(20) * 85))

    # Чем больше отзывов у топов — тем сложнее войти
    top_reviews = sorted(reviews, reverse=True)[:3]
    avg_top_reviews = sum(top_reviews) / len(top_reviews) if top_reviews else 0
    # log шкала: 10 отзывов → ~8, 100 → ~15, 1000 → ~15 (cap)
    barrier_factor = min(15, int(math.log1p(avg_top_reviews) / math.log1p(1000) * 15))

    competition_score = min(100, seller_factor + barrier_factor)

    # --- demandScore (0-100) ---
    # Логарифмическая шкала по avg_reviews:
    # 0 отз → 10, 10 → 30, 100 → 55, 500 → 75, 2000+ → 95
    if avg_reviews == 0:
        demand_score = 10
    else:
        demand_score = min(95, int(math.log1p(avg_reviews) / math.log1p(2000) * 85) + 10)

    # Поправка на рейтинг: высокий рейтинг = подтверждённый спрос (+5 макс)
    rating_bonus = int((avg_rating - 3.0) * 2.5) if avg_rating >= 3.0 else 0
    demand_score = min(100, demand_score + rating_bonus)

    # --- nicheScore (0-100) ---
    # Формула: высокий спрос + низкая конкуренция = хорошая ниша
    # Дополнительный бонус если разброс цен большой (есть место для нового предложения)
    spread_bonus = min(10, price_spread_pct // 10)
    niche_score = min(100, max(0, int(demand_score * 0.6 + (100 - competition_score) * 0.4) + spread_bonus))

    return {
        "totalFound": len(products),
        "avgPrice": avg_price,
        "minPrice": min_price,
        "maxPrice": max_price,
        "priceSpreadPct": price_spread_pct,
        "totalReviews": total_reviews,
        "avgReviews": avg_reviews,
        "avgRating": avg_rating,
        "uniqueSellers": seller_count,
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

    # Уже посчитанные скоры — Claude их не должен менять
    niche_score_calc = real_stats.get("nicheScore", 0) if real_stats else 0
    demand_score_calc = real_stats.get("demandScore", 0) if real_stats else 0
    competition_score_calc = real_stats.get("competitionScore", 0) if real_stats else 0
    avg_price_calc = real_stats.get("avgPrice", 0) if real_stats else 0
    price_spread_calc = real_stats.get("priceSpreadPct", 0) if real_stats else 0
    unique_sellers_calc = real_stats.get("uniqueSellers", 0) if real_stats else 0
    avg_rating_calc = real_stats.get("avgRating", 0) if real_stats else 0

    scores_hint = f"""
УЖЕ ПОСЧИТАННЫЕ СКОРЫ (используй именно эти значения, не меняй):
- nicheScore: {niche_score_calc}
- demandScore: {demand_score_calc}
- competitionScore: {competition_score_calc}
- avgPrice: {avg_price_calc} тенге
- priceSpreadPct: {price_spread_calc}% (разброс цен)
- uniqueSellers: {unique_sellers_calc}
- avgRating: {avg_rating_calc}
""" if real_stats else ""

    prompt = f"""Ты эксперт по маркетплейсу Kaspi.kz (Казахстан). Проанализируй нишу "{query}".

{data_section}
{scores_hint}

Твоя задача — посчитать ТОЛЬКО финансовые показатели и дать текстовый анализ.

Для marginPct: казахстанский маркетплейс, типичная маржа 15-40%. Оцени по категории товара.
Для optimalPrice: рекомендуй цену входа — чуть ниже avgPrice если конкуренция высокая, чуть выше если товар качественнее.
Для monthlyProfit: оцени реалистично (продажи 20-100 шт/мес для новичка * маржа в тенге).
Для breakEvenUnits: сколько единиц нужно продать чтобы окупить первую закупку (считай от avgPrice * 0.6 как себестоимость).
Для marginScore: оцени привлекательность маржи в этой категории (0-100).

Верни ТОЛЬКО валидный JSON без пояснений и без markdown:
{{
  "verdict": "ВОЙТИ или ОСТОРОЖНО или НЕ ВХОДИТЬ",
  "verdictText": "2-3 предложения с обоснованием на основе реальных данных",
  "nicheScore": {niche_score_calc},
  "demandScore": {demand_score_calc},
  "marginScore": 0,
  "competitionScore": {competition_score_calc},
  "optimalPrice": 0,
  "marginPct": 0,
  "monthlyProfit": 0,
  "breakEvenUnits": 0,
  "freeSegments": ["свободный сегмент"],
  "topOpportunities": ["возможность 1", "возможность 2", "возможность 3"],
  "topRisks": ["риск 1", "риск 2"],
  "keywords": ["ключевое слово 1", "ключевое слово 2", "ключевое слово 3"]
}}

nicheScore, demandScore, competitionScore — вставь значения из блока выше без изменений.
Все цены в тенге. Анализируй на основе предоставленных реальных данных."""

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
