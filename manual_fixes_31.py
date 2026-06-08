"""Manual cleanup of the 31 remaining suspect rows.

Each entry sourced from the home's official Korian/Caritas/ESV website,
or via web search. No LLM, no inference — verified data only.
"""

import os
from dotenv import load_dotenv

from data_cleaner import db_connect, validate_cleaned
from fix_suspects import normalize_ort

load_dotenv()

# (api_id, telefon, email, adresse)
FIXES = [
    (13421, "02064 4780 0",         "marthahof@korian.de",                 "Marthastr. 1, 46537 Dinslaken"),
    (21211, "02822 40399900",       "emmerich@korian.de",                  "Moritz-von-Nassau-Str. 25, 46446 Emmerich am Rhein"),
    (1431,  "+49 2333 609620",      "haus.elisabeth@t-a-s.net",            "Kirchstr. 76, 58256 Ennepetal"),
    (15237, "02333 6320 0",         "ennepetal@korian.de",                 "Loher Str. 7, 58256 Ennepetal"),
    (326,   "+49 201 563020",       "info@ffc-stiftung.de",                "Steeler Str. 642-646, 45276 Essen"),
    (1078,  "+49 201 68560",        "emmausquartier@contilia.de",          "Schönebecker Str. 95, 45359 Essen"),
    (11291, "0201 81265 100",       "ernestinenhof@korian.de",             "Essener Str. 55, 45141 Essen-Stoppenberg"),
    (337,   "+49 201 563020",       "info@ffc-stiftung.de",                "Paßstraße 4, 45276 Essen"),
    (2830,  "+49 201 18575-100",    "info@sz-st-martin.de",                "Rüttenscheider Str. 277, 45131 Essen"),
    (8291,  "+49 2721 9762-0",      "infohhh@caritas-olpe.de",             "Theodor-Storm-Straße 2, 57413 Finnentrop"),
    (10293, "02234 6038 0",         "frechen@korian.de",                   "Arnikastr. 4, 50226 Frechen"),
    (1042,  "+49 209 7040",         "bthiehoff@kkel.de",                   "Ahornstr. 33, 45892 Gelsenkirchen"),
    (1026,  "+49 2043 3712-00",     "martin.debruin@caritas-gladbeck.de",  "Rentforter Straße 30, 45964 Gladbeck"),
    (34823, "02562 994930000",      "gronau@auvictum.de",                  "An der Weißen Dame 5, 48599 Gronau"),
    (10276, "05241 918 500",        "guetersloh@korian.de",                "Neuenkirchener Str. 41, 33332 Gütersloh"),
    (1112,  "+49 2984 304-0",       "josefs.haus@caritas-brilon.de",       "Aue 2, 59969 Hallenberg"),
    (2249,  "02242 930 0",          "hennef@korian.de",                    "Kurhausstraße 45, 53773 Hennef"),
    (6700,  "+49 2591 7997-60",     "clara-stift@heilig-geist-stiftung.de","Mollstraße 18, 59348 Lüdinghausen-Seppenrade"),
    (1822,  "+49 2556 9859-0",      "petra.brauckmann@caritas-steinfurt.de","Pfarrer-Böckmann-Straße 7, 48629 Metelen"),
    (1757,  "05452 55520",          "info@reha-zentrum-mettingen.de",      "Bahnhofstraße 19, 49497 Mettingen"),
    (2711,  "+49 2161 5951-0",      "kontakt-hehn@vianobis.de",            "Heiligenpesch 84, 41069 Mönchengladbach"),
    (1973,  "02153 735 0",          "breyell@korian.de",                   "Loirfeld 1, 41334 Nettetal-Breyell"),
    (10079, "02131 7039 0",         "neuss@korian.de",                     "Friedrichstr. 2-6, 41460 Neuss"),
    (11743, "+49 2865 9570-0",      "st-martin@caritas-borken.de",         "Im Mensing 15, 46348 Raesfeld"),
    (22226, "+49 5971 8660",        "marienstift@caritas-rheine.de",       "Aloysiusstraße 81-83, 48429 Rheine"),
    (2251,  "02241 494 0",          "sieglar@korian.de",                   "Rathausstr. 1, 53844 Troisdorf"),
    (2252,  "02836 91109 0",        "imhagenland@korian.de",               "Wankumer Str. 14, 47669 Wachtendonk"),
    (1560,  "02922 804 01",         "amadeus@korian.de",                   "Westuffler Weg 9, 59457 Werl"),
    (2706,  "0152 59 60 92 87",     "Szalatan@esv.de",                     "Stevelinger Str. 20, 58300 Wetter"),
    (13367, "+49 2557 9363-0",      "elisabethstift@caritas-rheine.de",    "Gnoiener Platz 4-8, 48493 Wettringen"),
    (11601, "02302 936 00 11",      "Info-HausBuschey@esv.de",             "Wengernstraße 55, 58452 Witten"),
]


def main() -> None:
    conn = db_connect()
    print("\nfinal:")
    for cl, q, n in cur.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
