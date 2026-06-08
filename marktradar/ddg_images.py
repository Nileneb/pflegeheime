"""DuckDuckGo-Bildersuche als Meshy-Input — echte Gebäudefotos, legal & token-frei.
Liefert oft Fotos direkt von der Träger-Website (z.B. bergische-diakonie.de). Meshy
kann die öffentlichen Bild-URLs direkt verarbeiten (image_url), kein Upload nötig."""
import json
import re
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"


def _get(url, ref=None):
    h = {"User-Agent": UA, "Accept": "text/html,application/json"}
    if ref:
        h["Referer"] = ref
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15) as r:
        return r.read().decode("utf-8", "ignore")


def search(query, n=24, min_px=400):
    """Bild-Treffer für `query` → Liste {image, thumbnail, width, height, source, title}.
    Filtert grob nach Mindestkantenlänge (deklarierte Maße)."""
    html = _get("https://duckduckgo.com/?q=" + urllib.parse.quote(query) + "&iax=images&ia=images")
    m = re.search(r"vqd=[\"']?([\d-]+)", html)
    if not m:
        raise RuntimeError("DDG vqd-Token nicht gefunden")
    vqd = m.group(1)
    url = ("https://duckduckgo.com/i.js?l=de-de&o=json&q=" + urllib.parse.quote(query)
           + "&vqd=" + vqd + "&f=,,,&p=1")
    data = json.loads(_get(url, ref="https://duckduckgo.com/"))
    out = []
    for r in data.get("results", []):
        w, h = r.get("width") or 0, r.get("height") or 0
        if min(w, h) < min_px:
            continue
        out.append({"image": r.get("image"), "thumbnail": r.get("thumbnail"),
                    "width": w, "height": h, "source": r.get("url"), "title": r.get("title")})
        if len(out) >= n:
            break
    return out
