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
