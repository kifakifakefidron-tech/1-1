"""Новости РБК Недвижимость: только заголовок, первая фраза и ссылка на rbc.ru."""
from app import widgets

XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:rbc_news="https://www.rbc.ru" version="2.0"><channel>
<item><title><![CDATA[Ставки по ипотеке снизились]]></title><link>https://realty.rbc.ru/news/1</link>
<rbc_news:anons><![CDATA[Банки снизили ставки на вторичное жильё. Это вторая фраза, которую мы не берём.]]></rbc_news:anons>
<rbc_news:tag><![CDATA[Ипотека]]></rbc_news:tag><rbc_news:newsDate_timestamp>200</rbc_news:newsDate_timestamp>
<rbc_news:full-text><![CDATA[ПОЛНЫЙ ТЕКСТ НЕ БЕРЁМ]]></rbc_news:full-text></item>
<item><title>Чужая ссылка</title><link>https://evil.example/x</link><rbc_news:newsDate_timestamp>300</rbc_news:newsDate_timestamp></item>
<item><title>Старая</title><link>https://realty.rbc.ru/news/0</link><pubDate>Tue, 13 Oct 2026 09:45:42 +0300</pubDate>
<description>Короткий анонс без точки в конце для проверки обрезки текста</description></item>
</channel></rss>""".encode()


def test_parse_rbc_keeps_only_headline_lead_link():
    items = widgets.parse_rbc(XML)
    assert [x["url"] for x in items] == ["https://realty.rbc.ru/news/0", "https://realty.rbc.ru/news/1"]
    first = items[1]
    assert first["title"] == "Ставки по ипотеке снизились" and first["tag"] == "Ипотека"
    assert first["lead"] == "Банки снизили ставки на вторичное жильё."
    assert "ПОЛНЫЙ" not in str(items)
    assert items[0]["ts"] == 1791873942


def test_sort_news_tabs():
    """19.10: «Краснодар» — 93.ru (недвижимость) и упоминания края; «Законы и рынок» — законы/ипотека/налоги; шум 93.ru — нет."""
    it = lambda src, title, url, tag="": {"src": src, "title": title, "url": url, "tag": tag, "lead": "", "ts": 1}  # noqa: E731
    res = widgets.sort_news([
        it("93.ru", "Брал квартиру, а получил суды", "https://93.ru/text/realty/2026/1/", "Недвижимость"),
        it("93.ru", "Убили в квартире бойфренда", "https://93.ru/text/incidents/2026/2/", "Происшествия"),
        it("93.ru", "Не заправляйте кровать сразу", "https://93.ru/text/home/2026/3/", "Мой дом"),
        it("РБК Недвижимость", "Госдума уточнила налог при мене жилья", "https://realty.rbc.ru/news/1"),
        it("РБК Недвижимость", "В Краснодаре выросли цены на новостройки", "https://realty.rbc.ru/news/2"),
    ])
    assert [x["title"][:12] for x in res["local"]] == ["Брал квартир", "В Краснодаре"]
    assert [x["title"][:12] for x in res["laws"]] == ["Госдума уточ"]
