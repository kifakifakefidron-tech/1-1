"""18.10: «сдам … +КУ» у одного объекта портянки не делает арендой продажу рядом (Артём: «11699т.р — цена продажи»)."""
from app import db, ingest, parser

SALE = """🔔 Дмитрий
🔥🔥🔥 ГМР 3 к.кв 83.7 кв.м² +два балкона 17/19 этаж
ул. Невкипелого 10 ЖК "Поющие фонтаны"
#продам #недвижимость #квартиры #Краснодар
Состояние с хорошим ремонтом(заходи и живи) всё остаётся.
В ДКП ВСЯ СУММА
📍📍11699т.р
🔔 Дмитрий"""
RENT = "Сдам 1к квартиру ЮМР, ул. Ставропольская 200, 40 м², 5/9 этаж\nЦена 30 т.р + КУ"


def test_sale_next_to_rent_stays_sale():
    _, objs = parser.parse(SALE + "\n\n" + RENT, use_llm=False)
    assert [(o.deal, o.price) for o in objs] == [("sale", 11_699_000), ("rent", 30_000)]
    assert (objs[0].street, objs[0].house) == ("Невкипелого", "10")


def test_rent_header_still_applies():
    _, objs = parser.parse("Сдаю, всё +КУ:\n1к ЮМР 40 м² 5/9 цена 30 т.р\n\n2к ФМР 60 м² 3/9 цена 45 т.р", use_llm=False)
    assert {o.deal for o in objs} == {"rent"}


def test_fix_rent_leak_moves_old_rows():
    conn = db.connect(":memory:")
    conn.execute("""INSERT INTO listings (type, deal, rooms, area, price, title, fragment, first_seen, last_seen, seen_count,
                    is_active, source, search_text) VALUES ('flat','rent',3,83.7,11699000,'x',?,1,1,1,1,'chat','')""", (SALE,))
    conn.commit()
    assert ingest.fix_rent_leak(conn)["moved"] == 1
    assert conn.execute("SELECT deal FROM listings").fetchone()[0] == "sale"
