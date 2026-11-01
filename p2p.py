"""Поиск выгодных P2P-связок — версия на Python для фонового опроса ботом.

Логика специально зеркалит webapp/p2p-kzt.html (та же идея: лучшая цена
покупки/продажи с учётом лимитов объявления и остатка монет), чтобы бот и
страница в Telegram показывали одно и то же.
"""
import json
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json', 'Accept': 'application/json'}


def _post(url, body, timeout=12):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method='POST', headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(url, timeout=12):
    req = urllib.request.Request(url, method='GET', headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _ad(price, mn, mx, qty, who):
    return {'price': price, 'min': mn, 'max': mx, 'qty': qty, 'who': who}


def _binance(asset):
    def page(trade_type):
        out = []
        for p in (1, 2):
            r = _post('https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search', {
                'fiat': 'KZT', 'page': p, 'rows': 20, 'tradeType': trade_type, 'asset': asset,
                'countries': [], 'proMerchantAds': False, 'shieldMerchantAds': False,
                'publisherType': None, 'payTypes': [], 'classifies': ['mass', 'profession'],
            })
            for x in r.get('data') or []:
                adv = x['adv']
                out.append(_ad(float(adv['price']), float(adv['minSingleTransAmount']),
                                float(adv.get('dynamicMaxSingleTransAmount') or adv['maxSingleTransAmount']),
                                float(adv['tradableQuantity']), x['advertiser']['nickName']))
        return out
    return page('BUY'), page('SELL')


def _bybit(asset):
    def page(side):
        out = []
        for p in (1, 2):
            r = _post('https://api2.bybit.com/fiat/otc/item/online', {
                'userId': '', 'tokenId': asset, 'currencyId': 'KZT', 'payment': [],
                'side': side, 'size': '20', 'page': str(p), 'amount': '', 'authMaker': False, 'canTrade': False,
            })
            for x in ((r.get('result') or {}).get('items') or []):
                out.append(_ad(float(x['price']), float(x['minAmount']), float(x['maxAmount']),
                                float(x['lastQuantity']), x['nickName']))
        return out
    return page('1'), page('0')


def _okx(asset):
    def page(side):
        url = (f'https://www.okx.com/v3/c2c/tradingOrders/books?side={side}&quoteCurrency=KZT'
               f'&baseCurrency={asset}&paymentMethod=all&userType=all&showTrade=false&showFollow=false'
               f'&showAlreadyTraded=false&isAbleFilter=false&receivingAds=false&urlId=0')
        r = _get(url)
        rows = ((r.get('data') or {}).get(side)) or []
        return [_ad(float(x['price']), float(x['quoteMinAmountPerOrder']), float(x['quoteMaxAmountPerOrder']),
                     float(x['availableAmount']), x['nickName']) for x in rows]
    return page('sell'), page('buy')


FETCHERS = {'binance': _binance, 'bybit': _bybit, 'okx': _okx}


def load_books(asset):
    """Возвращает {exchange: {'asks': [...], 'bids': [...]}} — только биржи, с которых
    удалось получить объявления обеих сторон."""
    books = {}
    for name, fn in FETCHERS.items():
        try:
            asks, bids = fn(asset)
            med = lambda l: sorted(x['price'] for x in l)[len(l) // 2] if l else 0
            if med(asks) < med(bids):
                asks, bids = bids, asks
            if asks and bids:
                books[name] = {'asks': sorted(asks, key=lambda x: x['price']),
                                'bids': sorted(bids, key=lambda x: -x['price'])}
        except Exception:
            continue
    return books


def _best_ask(ads, amount):
    for x in ads:
        if x['min'] <= amount <= x['max'] and x['qty'] * x['price'] >= amount:
            return x
    return None


def _best_bid(ads, qty):
    for x in ads:
        revenue = qty * x['price']
        if x['min'] <= revenue <= x['max'] and x['qty'] >= qty:
            return x
    return None


def best_route(books, amount, fee):
    """Лучшая связка на сумму `amount` тенге с комиссией перевода `fee` монет.
    Возвращает dict с полями i, j, profit, pct или None, если ничего не подходит."""
    best = None
    for i, bi in books.items():
        a = _best_ask(bi['asks'], amount)
        if not a:
            continue
        qty = amount / a['price']
        for j, bj in books.items():
            q = qty - (0 if i == j else fee)
            if q <= 0:
                continue
            b = _best_bid(bj['bids'], q)
            if not b:
                continue
            out = q * b['price']
            profit = out - amount
            if best is None or profit > best['profit']:
                best = {'i': i, 'j': j, 'profit': profit, 'pct': (out / amount - 1) * 100,
                        'buy_price': a['price'], 'sell_price': b['price']}
    return best
