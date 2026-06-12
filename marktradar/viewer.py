"""Live-Viewer für den Pflege-Marktradar — schlanker stdlib-HTTP-Service an pflege.db.

Home = Signal-Dashboard (Δ-Zähler, Feed, Event-/Quellen-Status), Tabs für
Entitäten-Graph und Timeline. SSE (/events) pusht bei DB-Änderung → der Client
lädt neu, sodass man sieht, was sich an den Daten ändert. Ein 'als gesehen'-
Watermark (meta.last_seen) markiert neue Meldungen.

    python -m marktradar.viewer            # → http://localhost:8765
    PFLEGE_VIEWER_PORT=9000 python -m marktradar.viewer
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from marktradar import db, entities, feeds, geo, hashtags, organigram, query, sources

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_LANGGEO_CACHE = None
_LANGGEO_LOCK = threading.Lock()


def _langgeo():
    """Countries-GeoJSON + ISO_A2→lang mapping for the soft language overlay.

    Cached after first read — the geojson is ~820 KB, never re-read per request.
    Kept out of /api/hashtags so that response stays lean.

    Raises RuntimeError on I/O failure so the caller can return a proper error
    response instead of dropping the connection.
    """
    global _LANGGEO_CACHE
    if _LANGGEO_CACHE is not None:
        return _LANGGEO_CACHE
    with _LANGGEO_LOCK:
        if _LANGGEO_CACHE is not None:  # another thread populated it while we waited
            return _LANGGEO_CACHE
        try:
            with open(os.path.join(_DATA_DIR, "ne_110m_admin_0_countries.geojson"), encoding="utf-8") as f:
                gj = json.load(f)
            with open(os.path.join(_DATA_DIR, "country_language.json"), encoding="utf-8") as f:
                cl = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("langgeo: failed to load data files: %s", exc)
            raise RuntimeError(f"langgeo data unavailable: {exc}") from exc
        _LANGGEO_CACHE = {"geojson": gj, "country_language": cl}
    return _LANGGEO_CACHE


DB_PATH = os.getenv("PFLEGE_DB", db.DEFAULT_DB)
PORT = int(os.getenv("PFLEGE_VIEWER_PORT", "8765"))
HOST = os.getenv("PFLEGE_VIEWER_HOST", "127.0.0.1")  # Container: 0.0.0.0 für nginx


def _post_expiry_days():
    """Age (days) after which globe markers visually fade out (0 = disabled).

    Purely visual — sources stay in the DB. Injected into the page so the client
    can compute per-marker age-decay. Bad values fall back to the default.
    """
    try:
        return max(0, int(os.getenv("PFLEGE_POST_EXPIRY_DAYS", "21")))
    except (TypeError, ValueError):
        return 21


def _render_index():
    """INDEX_HTML with the server-side config injected (post-expiry for age-decay)."""
    cfg = f"<script>window.POST_EXPIRY_DAYS={_post_expiry_days()};</script>"
    return INDEX_HTML.replace("<!--CONFIG-->", cfg, 1)


def _db():
    conn = db.connect(DB_PATH)
    db.bootstrap(conn)
    return conn


def _json(handler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _xml(handler, text, code=200, ctype="application/rss+xml; charset=utf-8"):
    body = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # WHY: stdlib-Default spammt stderr pro Request
        pass

    def do_HEAD(self):
        # WHY: Feed-Reader/Crawler (Feedly) prüfen den Feed per HEAD VOR dem GET;
        # BaseHTTPRequestHandler ohne do_HEAD → 501 → "dead feed". 200 + Content-Type, kein Body.
        u = urlparse(self.path)
        ctype = ("application/rss+xml; charset=utf-8" if u.path in ("/feed.xml", "/rss")
                 else "text/html; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            body = _render_index().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")  # iframe immer frisch
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/events":
            return self._sse()
        conn = _db()
        try:
            if u.path in ("/feed.xml", "/rss"):
                _xml(self, feeds.rss(conn, int(q.get("limit", ["50"])[0]),
                                     q.get("tag", [None])[0]))
            elif u.path == "/api/overview":
                _json(self, query.overview(conn))
            elif u.path == "/api/feed":
                since = query.get_meta(conn, "last_seen", "1970-01-01")
                _json(self, query.feed(conn, int(q.get("limit", ["40"])[0]), since))
            elif u.path == "/api/graph":
                _json(self, query.graph_data(conn))
            elif u.path == "/api/discourse":
                _json(self, query.discourse(conn))
            elif u.path == "/api/positions":
                _json(self, query.positions(conn, q.get("topic", [None])[0],
                                            int(q.get("limit", ["120"])[0])))
            elif u.path == "/api/discourse_topics":
                _json(self, query.discourse_topics(conn))
            elif u.path == "/api/discourse_topic":
                _json(self, query.discourse_topic(conn, q.get("topic", [""])[0]))
            elif u.path == "/api/timeline":
                name = q.get("name", [""])[0]
                et = q.get("event_type", [None])[0]
                _json(self, query.timeline(conn, name, int(q.get("limit", ["40"])[0]), et))
            elif u.path == "/api/entity":
                _json(self, query.get_entity(conn, q.get("name", [""])[0]) or {})
            elif u.path == "/api/org":
                traeger = q.get("traeger", [organigram.DEFAULT_TRAEGER])[0]
                _json(self, {
                    "stats": organigram.stats(conn, traeger),
                    "tree": organigram.tree(conn, traeger),
                    "persons": organigram.persons(conn, traeger=traeger),
                })
            elif u.path == "/api/sources":
                _json(self, {"sources": sources.stats(
                    conn, int(q.get("recent_days", ["30"])[0]))})
            elif u.path == "/api/event_types":
                _json(self, {"event_types": entities.list_event_types(conn),
                             "topics": entities.list_topics(conn)})
            elif u.path == "/api/entities/pending":
                from marktradar import ner
                _json(self, {"pending": ner.pending_review(conn)})
            elif u.path == "/api/hashtags":
                _json(self, hashtags.map_data(conn))
            elif u.path == "/api/watch":
                from marktradar import watch
                _json(self, {"accounts": watch.list_accounts(conn)})
            elif u.path == "/api/langgeo":
                try:
                    _json(self, _langgeo())
                except RuntimeError as exc:
                    _json(self, {"error": str(exc)}, 503)
            elif u.path == "/api/hashtags/_debug_extract":
                _json(self, hashtags.debug_extract(conn, int(q.get("n", ["3"])[0])))
            elif u.path == "/api/org/scene":
                _json(self, geo.scene(conn, int(q.get("id", ["0"])[0]),
                                      int(q.get("radius", ["320"])[0]),
                                      refresh=q.get("refresh", ["0"])[0] in ("1", "true")))
            else:
                _json(self, {"error": "not found"}, 404)
        finally:
            conn.close()

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_POST(self):
        path = urlparse(self.path).path
        conn = _db()
        try:
            if path == "/api/mark_seen":
                query.set_meta(conn, "last_seen", datetime.now(timezone.utc).isoformat())
                _json(self, {"ok": True})
            elif path == "/api/hashtag/add":
                b = self._body()
                _json(self, hashtags.add(conn, b.get("term", ""), b.get("color", "#5b8def")))
            elif path == "/api/hashtag/update":
                b = self._body()
                _json(self, hashtags.update(conn, int(b["id"]), b.get("term"),
                                            b.get("color"), b.get("active")))
            elif path == "/api/hashtag/delete":
                b = self._body()
                _json(self, hashtags.delete(conn, int(b["id"])))
            elif path == "/api/watch/add":
                from marktradar import watch
                b = self._body()
                _json(self, watch.add(conn, b["platform"], b["handle"],
                                      entity_id=b.get("entity_id"), label=b.get("label")))
            elif path == "/api/watch/remove":
                from marktradar import watch
                _json(self, watch.remove(conn, int(self._body()["id"])))
            elif path == "/api/watch/toggle":
                from marktradar import watch
                b = self._body()
                _json(self, watch.set_active(conn, int(b["id"]), bool(b.get("active"))))
            elif path == "/api/watch/refresh":
                from marktradar import watch
                _json(self, watch.refresh(conn))
            elif path == "/api/hashtags/refresh":
                b = self._body()
                src = tuple(b["sources"]) if b.get("sources") else ("mastodon", "bluesky", "news")
                _json(self, hashtags.refresh(conn, src, int(b.get("limit", 15)),
                                             only_id=b.get("id")))
            elif path == "/api/hashtags/tag":
                b = self._body()
                _json(self, hashtags.tag_articles(
                    conn, None, auto_create=bool(b.get("auto_create", True)),
                    limit=int(b.get("limit", 400))))
            elif path == "/api/sources/add":
                b = self._body()
                url = (b.get("url") or "").strip()
                if not url:
                    url = "archive://" + re.sub(r"[^a-z0-9]+", "-",
                                                b["name"].lower()).strip("-")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO sources(name,type,url,tier,region,enabled)"
                    " VALUES (?,?,?,?,?,1)",
                    (b["name"], b.get("type", "rss"), url,
                     int(b.get("tier", 1)), b.get("region", "DE")))
                conn.commit()
                _json(self, {"added": bool(cur.rowcount), "name": b["name"]})
            elif path == "/api/sources/toggle":
                b = self._body()
                conn.execute("UPDATE sources SET enabled=? WHERE id=?",
                             (1 if b["enabled"] else 0, int(b["id"])))
                conn.commit()
                _json(self, {"ok": True})
            elif path == "/api/sources/discover":
                from marktradar import discovery
                b = self._body()
                _json(self, discovery.discover_sources(conn, b.get("seed_domains"),
                                                       int(b.get("limit", 10))))
            elif path == "/api/sources/approve":
                from marktradar import discovery
                _json(self, discovery.approve_source(conn, int(self._body()["id"])))
            elif path == "/api/event_types/add":
                b = self._body()
                _json(self, entities.add_event_type(conn, b.get("name", ""),
                                                    b.get("pattern", ""),
                                                    b.get("description")))
            elif path == "/api/entities/extract":
                from marktradar import ner
                b = self._body()
                _json(self, ner.extract_entities(conn, int(b.get("since_days", 7)),
                                                 int(b.get("limit", 50))))
            elif path == "/api/entities/review":
                from marktradar import ner
                b = self._body()
                _json(self, ner.review_entity(conn, int(b["id"]), bool(b.get("accept"))))
            elif path == "/api/entities/mirror_heime":
                _json(self, {"mirrored": entities.mirror_heime(conn)})
            elif path == "/api/sources/seed":
                # idempotent (INSERT OR IGNORE): zieht neue Seed-Quellen in die
                # bestehende Prod-DB (entrypoint-Seed läuft nur bei leerer DB).
                _json(self, {"added": sources.seed(conn), "total": conn.execute(
                    "SELECT count(*) c FROM sources").fetchone()["c"]})
            elif path == "/api/geocode_org":
                b = self._body()
                _json(self, geo.geocode_units(conn, b.get("traeger", organigram.DEFAULT_TRAEGER),
                                              b.get("limit")))
            else:
                _json(self, {"error": "not found"}, 404)
        finally:
            conn.close()

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_mtime = 0.0
        last_beat = 0.0
        try:
            while True:
                try:
                    m = os.path.getmtime(DB_PATH)
                except OSError:
                    m = 0.0
                now = time.time()
                if m != last_mtime:
                    last_mtime = m
                    self.wfile.write(b"event: change\ndata: {}\n\n")
                    self.wfile.flush()
                elif now - last_beat > 15:
                    last_beat = now
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                time.sleep(1.5)
        except (BrokenPipeError, ConnectionResetError):
            return


INDEX_HTML = r"""<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Pflege-Marktradar</title>
<style>
*{margin:0;box-sizing:border-box}
:root{--bg:#0a0e14;--pan:#0f1622;--ln:#1c2535;--mut:#6b7689;--fg:#c8d0dc;--accent:#5b8def}
body{background:var(--bg);color:var(--fg);font:13px/1.5 'SF Mono',ui-monospace,Menlo,monospace;padding:18px}
a{color:inherit;text-decoration:none}
.hd{display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--ln);padding-bottom:12px;margin-bottom:14px;flex-wrap:wrap}
.hd h1{font-size:17px;letter-spacing:3px;color:#fff;font-weight:600}
.hd .sub{color:var(--accent);letter-spacing:2px;font-size:11px}
.live{display:flex;align-items:center;gap:6px;color:var(--mut);font-size:11px}
.live .pulse{width:8px;height:8px;border-radius:50%;background:#2ecc71;box-shadow:0 0 0 0 #2ecc7188;animation:p 2s infinite}
@keyframes p{0%{box-shadow:0 0 0 0 #2ecc7188}70%{box-shadow:0 0 0 7px #2ecc7100}100%{box-shadow:0 0 0 0 #2ecc7100}}
.spacer{margin-left:auto}
.newbadge{background:#13351f;color:#2ecc71;border:1px solid #1c4d33;border-radius:14px;padding:4px 11px;font-size:11px}
.btn{background:var(--pan);border:1px solid var(--ln);color:var(--fg);border-radius:6px;padding:5px 11px;font:inherit;font-size:11px;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.tabs{display:flex;gap:6px;margin-bottom:14px}
.tab{padding:6px 14px;border:1px solid var(--ln);border-radius:6px;color:var(--mut);cursor:pointer;font-size:12px;letter-spacing:1px}
.tab.on{background:var(--accent);color:#06142e;border-color:var(--accent);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
.tile{background:var(--pan);border:1px solid var(--ln);border-radius:8px;padding:13px 15px}
.tl{font-size:10px;letter-spacing:2px;color:var(--mut)}
.tv{font-size:27px;color:#fff;font-weight:600}
.td{font-size:11px;color:#2ecc71}
.grid{display:grid;grid-template-columns:1.7fr 1fr;gap:14px}
.panel{background:var(--pan);border:1px solid var(--ln);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.ph{font-size:10px;letter-spacing:2px;color:var(--mut);margin-bottom:10px;display:flex;justify-content:space-between}
.ph-r{display:flex;align-items:center;gap:10px}.ph-r .btn{padding:2px 8px;letter-spacing:1px}
.row{display:grid;grid-template-columns:74px 96px 1fr;gap:8px;padding:6px 0;border-bottom:1px solid #131c2a;align-items:center}
.row .ti{grid-column:3;color:#e6ebf2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .meta{grid-column:3;font-size:10px;color:var(--mut)}
.fhash{color:#5b8def;font-size:10px;margin-left:4px}
.qhint{font-size:11px;color:var(--mut);margin-bottom:12px;max-width:760px;line-height:1.5}
.qtable{width:100%;border-collapse:collapse;font-size:12px}
.qtable th{text-align:left;color:var(--mut);font-size:10px;letter-spacing:1px;padding:6px 8px;border-bottom:1px solid var(--ln);font-weight:500}
.qtable td{padding:6px 8px;border-bottom:1px solid #131c2a;white-space:nowrap}
.qtable td.num{text-align:right;font-variant-numeric:tabular-nums}
.qtable tr:hover td{background:#101a2c}
.qrank{color:var(--mut);width:30px}
.qbar{display:inline-block;height:6px;border-radius:3px;background:#2ecc71;vertical-align:middle}
.qbartrk{display:inline-block;width:90px;height:6px;border-radius:3px;background:#16202f;vertical-align:middle;margin-right:6px}
.qoff td{opacity:.45}
.qfilters{display:flex;gap:6px;margin-bottom:12px}
.qfilter{padding:4px 12px;border:1px solid var(--ln);border-radius:5px;color:var(--mut);cursor:pointer;font-size:11px;letter-spacing:.5px}
.qfilter.on{background:var(--accent);color:#06142e;border-color:var(--accent);font-weight:600}
.qadd{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.qadd input,.qadd select{background:#0c121c;border:1px solid var(--ln);color:var(--fg);border-radius:5px;padding:5px 8px;font:inherit;font-size:11px}
.qadd input{width:180px}
.qadd select{min-width:80px}
.qtypebadge{font-size:8px;border:1px solid;border-radius:3px;padding:1px 4px;letter-spacing:1px;text-transform:uppercase}
.qtog{background:none;border:1px solid var(--ln);border-radius:4px;color:var(--mut);cursor:pointer;font-size:10px;padding:2px 6px}
.qtog:hover{border-color:#fff;color:#fff}
.qsyshint{font-size:10px;color:var(--mut);margin-bottom:10px;padding:6px 8px;border:1px solid #1e2733;border-radius:5px;background:#080e18}
.row.fresh{background:#0d1f15;border-left:2px solid #2ecc71;padding-left:6px;margin-left:-8px}
.dt{color:var(--mut);font-size:11px}
.ev{font-size:9px;border:1px solid;border-radius:3px;padding:2px 4px;text-align:center;letter-spacing:1px}
.bar,.lr{display:grid;grid-template-columns:92px 1fr 30px;gap:8px;align-items:center;margin:7px 0;font-size:11px}
.bl,.ln{color:#aeb8c6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.btrack,.ltrack{height:8px;background:#131c2a;border-radius:4px;overflow:hidden}
.bfill,.lfill{display:block;height:100%}.lfill{background:var(--accent)}
.bn,.lc{text-align:right;color:#fff}
.src{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:11px;color:#aeb8c6}
.dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto}.sr{margin-left:auto;color:var(--mut);font-size:10px}
.hide{display:none}
.gwrap{display:grid;grid-template-columns:1fr 300px;gap:14px}
svg text{font-family:inherit}
.node{cursor:pointer}
.en{font:600 19px Georgia,serif;color:#fff;margin-bottom:2px}.et{color:var(--accent);font-size:12px;margin-bottom:12px}
.tlitem{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid #16202e}
.tlitem .d{width:7px;height:7px;border-radius:50%;margin-top:5px;flex:0 0 auto}
.tlf{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.muted{color:var(--mut);font-size:11px}
.positions{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.pcard{background:#0c121c;border:1px solid var(--ln);border-radius:8px;padding:8px}
.pcard h4{font-size:11px;color:#aeb8c6;text-align:center;margin:2px 0 0;font-weight:600}
.lgbar{display:flex;gap:16px;margin-top:12px}
.lg{display:flex;align-items:center;gap:6px;font-size:11px;color:#aeb8c6}
.sw{width:11px;height:11px;border-radius:3px}
.tr{padding:9px 0;border-bottom:1px solid #131c2a}
.trh{display:flex;align-items:center;gap:10px}
.tt{color:#fff;font-weight:600}.tn{color:var(--mut);font-size:11px}
.spark{margin-left:auto;display:flex;gap:2px;align-items:flex-end;height:24px}
.sb{width:5px;background:var(--accent);border-radius:1px;display:block}
.trm{font-size:11px;color:#8a93a3;margin-top:3px}.tre{font-size:11px;color:var(--accent);margin-top:2px}
.tax{position:relative;margin-top:12px;width:100%}
.tax-line{position:absolute;left:0;right:0;bottom:20px;height:2px;background:#2a3550}
.tax-line:after{content:'▶';position:absolute;right:-3px;top:-7px;color:#46527a;font-size:11px}
.tax-tick{position:absolute;bottom:2px;transform:translateX(-50%);font-size:9px;color:#5b6577;white-space:nowrap}
.tax-tick:before{content:'';position:absolute;bottom:16px;left:50%;width:1px;height:7px;background:#2a3550}
.tax-item{position:absolute;display:flex;align-items:center;gap:4px;font-size:10px;color:#cdd8e0;white-space:nowrap;max-width:170px;overflow:hidden;text-overflow:ellipsis;text-decoration:none;padding:1px 3px;border-radius:3px}
.tax-item .d{width:9px;height:9px;border-radius:50%;flex:0 0 auto;border:1px solid #0a0e14}
.tax-item:hover{z-index:30;color:#fff;background:#0f1622;box-shadow:0 0 0 1px #2a3550;max-width:none}
.topnav{display:flex;align-items:center;gap:14px;margin:6px 0 12px}
.navbtn{background:var(--pan);border:1px solid var(--ln);color:#fff;border-radius:6px;width:30px;height:30px;font-size:16px;cursor:pointer}
.navbtn:hover{border-color:var(--accent);background:#13203a}
.tname{font:600 16px Georgia,serif;color:#fff;min-width:230px}
.vchart{position:relative;width:100%;margin-top:8px}
.vbase{position:absolute;left:0;right:0;height:2px;background:#46527a}
.vbase:after{content:'▶';position:absolute;right:-3px;top:-7px;color:#46527a;font-size:11px}
.vlbl{position:absolute;left:0;font-size:9px;letter-spacing:1px;font-weight:600;background:var(--pan);padding-right:4px}
.orgroot{display:inline-flex;align-items:center;gap:8px;font-weight:700;color:#fff;font-size:15px;border:1px solid var(--ln);border-radius:8px;padding:9px 16px;margin-bottom:14px}
.orgsek{border:1px solid var(--ln);border-left:4px solid var(--mut);border-radius:8px;margin-bottom:10px;overflow:hidden}
.orgsek>summary{list-style:none;cursor:pointer;padding:10px 14px;display:flex;align-items:center;gap:9px;font-weight:600;color:#fff;font-size:13px}
.orgsek>summary::-webkit-details-marker{display:none}
.orgsek>summary .cnt{margin-left:auto;color:var(--mut);font-size:10px;letter-spacing:1px;font-weight:400}
.orgsek>summary .car{color:var(--mut);transition:transform .15s}
.orgsek[open]>summary .car{transform:rotate(90deg)}
.orgber{border-top:1px solid #131c2a;padding:8px 14px 8px 26px}
.orgber>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;color:#dbe2ec}
.orgber>summary::-webkit-details-marker{display:none}
.orgber>summary .cnt{margin-left:auto;color:var(--mut);font-size:10px}
.orgleaves{display:flex;flex-wrap:wrap;gap:6px;padding:8px 0 2px 22px}
.orgleaf{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:#cdd8e0;background:#0c121c;border:1px solid var(--ln);border-radius:5px;padding:3px 9px}
.orgleaf .pin{width:7px;height:7px;border-radius:2px;flex:0 0 auto}
.orgpers{font-size:10px;color:#f0a830;margin-left:4px}
.orgppl{margin:6px 0 0 22px;font-size:11px;color:#aeb8c6;display:flex;flex-wrap:wrap;gap:10px}
.orgppl b{color:#fff;font-weight:600}
.globewrap{display:grid;grid-template-columns:1fr 330px;gap:14px;align-items:start}
#globecanvas{width:100%;height:620px;border-radius:8px;overflow:hidden;background:radial-gradient(circle at 50% 38%,#10203f,#05080f);cursor:grab}
#globecanvas:active{cursor:grabbing}
#globewrap{position:relative}
#langlegend{position:absolute;left:10px;bottom:10px;width:158px;background:#0b1220e6;border:1px solid var(--ln);border-radius:7px;font-size:10px;backdrop-filter:blur(3px);z-index:5}
#langlegend.hide{display:none}
.lglh{display:flex;align-items:center;justify-content:space-between;padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut);letter-spacing:.5px}
.lglx{background:none;border:none;color:var(--mut);cursor:pointer;font-size:12px;line-height:1;padding:0 2px}
#langlegbody{max-height:260px;overflow-y:auto;padding:4px 6px;display:grid;grid-template-columns:1fr 1fr;gap:1px 8px}
#langlegbody.collapsed{display:none}
.lgrow{display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lgsw{width:10px;height:10px;border-radius:2px;flex:0 0 auto;border:1px solid #ffffff22}
.htadd{display:flex;gap:6px;margin-bottom:10px}
.htin{flex:1;background:#0c121c;border:1px solid var(--ln);color:var(--fg);border-radius:5px;padding:5px 8px;font:inherit;font-size:11px}
input[type=color]{width:30px;height:28px;border:1px solid var(--ln);border-radius:5px;background:#0c121c;padding:1px;cursor:pointer}
.htrow{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #131c2a;font-size:11px}
.htrow .sw{width:12px;height:12px;border-radius:3px;flex:0 0 auto;cursor:pointer}
.htrow .swc{width:16px;height:16px;border:1px solid var(--ln);border-radius:3px;background:none;padding:0;cursor:pointer;flex:0 0 auto}
.htrow .nm{color:#e6ebf2;cursor:pointer}.htrow.off .nm{color:var(--mut);text-decoration:line-through}
.htrow .ct{margin-left:auto;color:var(--mut);font-size:10px}
.htrow .geo{color:#2ecc71;font-size:9px}
.htrow .trw{color:#f0a830;font-size:9px;margin-left:2px}
.trrow{display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid #131c2a;font-size:11px}
.trrow .trplus{color:var(--mut)}.trrow .trn{margin-left:auto;color:#fff;font-size:10px}
.trrow .trtag{font-weight:600}
.htrow .tog,.htrow .x{cursor:pointer;font-size:13px;padding:0 2px}.htrow .x{color:#ff6b6b}
.htsrc{display:block;padding:6px 0;border-bottom:1px solid #131c2a;font-size:11px}
.htsrc .sm{color:var(--mut);font-size:10px}
.htsrc a{color:#cdd8e0}.htsrc a:hover{color:#fff}
.srcbadge{font-size:8px;border:1px solid;border-radius:3px;padding:1px 4px;margin-right:5px;letter-spacing:1px}
.trustbadge{font-size:8px;border:1px solid;border-radius:3px;padding:1px 4px;margin-right:4px;white-space:nowrap}
.tierbadge{font-size:9px;margin-right:5px;opacity:.85}
.actorrow{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin:0 0 8px;padding-bottom:7px;border-bottom:1px solid #1e2733}
.actorlbl{font-size:8px;letter-spacing:1px;color:#7a8aa0;margin-right:3px}
.actorchip{font-size:9px;border:1px solid;border-radius:9px;padding:1px 6px;white-space:nowrap;opacity:.92}
.actorchip .an{font-size:8px;opacity:.7;margin-left:3px}
.orgmodes{display:flex;gap:6px;margin:4px 0 14px}
.flowwrap{overflow:auto;padding:6px 0 16px;max-height:74vh}
#org3d{height:620px;border-radius:8px;overflow:hidden;background:radial-gradient(circle at 50% 38%,#10203f,#05080f);cursor:grab}
.flegend{display:flex;gap:14px;margin-top:8px;flex-wrap:wrap;font-size:10px;color:var(--mut)}
#scene3d{position:fixed;inset:0;z-index:500;background:#05080f}
#sccanvas{position:absolute;inset:0}
.schud{position:absolute;top:0;left:0;right:0;display:flex;align-items:center;gap:14px;padding:12px 16px;background:linear-gradient(#05080fcc,#05080f00);pointer-events:none;z-index:3}
.schud>*{pointer-events:auto}
.schd{display:flex;flex-direction:column}
.schd #scname{color:#fff;font-weight:600;font-size:14px}
.scsub{color:var(--mut);font-size:10px}
.scstyles{display:flex;gap:6px;margin-left:8px}
.scbtn{padding:5px 11px;border:1px solid var(--ln);border-radius:6px;color:var(--mut);cursor:pointer;font-size:11px;background:#0f1622cc}
.scbtn.on{background:var(--accent);color:#06142e;border-color:var(--accent);font-weight:600}
.schint{margin-left:auto;color:var(--mut);font-size:11px}
.sclock{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:#0f1622dd;border:1px solid var(--accent);color:#fff;padding:14px 26px;border-radius:10px;font-size:15px;cursor:pointer;z-index:4}
.scload{position:absolute;bottom:20px;left:50%;transform:translateX(-50%);color:var(--mut);font-size:12px;z-index:4}
.schide{display:none}
#org3dtip{position:fixed;display:none;z-index:600;background:#0f1622ee;border:1px solid var(--ln);border-radius:6px;padding:5px 9px;font-size:11px;color:#fff;pointer-events:none;max-width:240px}
#globetip{position:fixed;display:none;z-index:600;background:#0b1220ee;border:1px solid var(--ln);border-radius:7px;padding:6px 11px;font-size:12px;color:#fff;pointer-events:none;max-width:280px;backdrop-filter:blur(4px);line-height:1.5}
</style></head><body>
<div class=hd>
  <h1>PFLEGE·MARKTRADAR</h1><span class=sub>LIVE VIEWER</span>
  <span class=live><span class=pulse></span><span id=conn>verbinde…</span></span>
  <span class=spacer></span>
  <span class=newbadge id=newbadge>– neu</span>
  <button class=btn id=markseen>als gesehen markieren</button>
</div>
<div class=tabs>
  <div class="tab on" data-t=dash>DASHBOARD</div>
  <div class=tab data-t=graph>ENTITÄTEN-GRAPH</div>
  <div class=tab data-t=diskurs>DISKURS</div>
  <div class=tab data-t=timeline>TIMELINE</div>
  <div class=tab data-t=globe>HASHTAG-GLOBUS</div>
  <div class=tab data-t=quellen>QUELLEN</div>
  <div class=tab data-t=org>ORGANIGRAMM</div>
</div>

<section id=dash>
  <div class=tiles id=tiles></div>
  <div class=grid>
    <div class=panel><div class=ph><span>SIGNAL-FEED · NEUESTE MELDUNGEN</span><span id=feednew></span></div><div id=feed></div></div>
    <div>
      <div class=panel><div class=ph><span>EVENT-TYPEN</span></div><div id=events></div></div>
      <div class=panel><div class=ph><span>TOP-ENTITÄTEN</span></div><div id=ents></div></div>
      <div class=panel><div class=ph><span>QUELLEN-STATUS</span></div><div id=sources></div></div>
    </div>
  </div>
</section>

<section id=graph class=hide>
  <div class=gwrap>
    <div class=panel><div class=ph><span>NETZWERK · ENTITÄTEN ↔ MARKTGESCHEHEN</span><span class=muted>Klick = Timeline</span></div><div id=graphsvg></div></div>
    <div class=panel><div class=ph><span>AUSGEWÄHLT</span></div><div id=gdetail class=muted>Knoten anklicken…</div></div>
  </div>
</section>

<section id=diskurs class=hide>
  <div class=panel>
    <div class=ph><span>DISKURS · POSITIONEN ÜBER ZEIT</span><span class=muted>x = Zeit · y = pro ▲ / contra ▼ · Farbe = Position · Klick = Quelle</span></div>
    <div class=topnav>
      <button class=navbtn id=tprev>‹</button>
      <span class=tname id=tname>…</span>
      <button class=navbtn id=tnext>›</button>
      <span class=muted id=dtnote></span>
    </div>
    <div class=lgbar id=dtlegend></div>
    <div id=dtchart class=muted>lädt…</div>
  </div>
</section>

<section id=timeline class=hide>
  <div class=panel>
    <div class=ph><span>ZEITSTRAHL · MELDUNGEN (Farbe = Event-Typ · Klick = Quelle)</span></div>
    <div class=tlf id=tlfilter></div>
    <div id=tltax class=muted>lädt…</div>
  </div>
</section>

<section id=globe class=hide>
  <div class=globewrap>
    <div class=panel gpanel>
      <div class=ph><span>HASHTAG-GLOBUS · ECHTE POSTS (Mastodon · Bluesky · News) · Drag = drehen · Klick Punkt = Quelle</span><span class=ph-r><button class=btn id=langtog title="Sprach-Overlay ein/aus">🗣 Sprachen: an</button><button class=btn id=arctog title="Ko-Vorkommen-Arcs ein/aus">⌇ Arcs: an</button><span class=muted id=globestat></span></span></div>
      <div id=globewrap>
        <div id=globecanvas></div>
        <div id=langlegend class=hide><div class=lglh><span>Sprachen</span><button class=lglx id=lglx title="ein-/ausklappen">▾</button></div><div id=langlegbody></div></div>
      </div>
    </div>
    <div>
      <div class=panel>
        <div class=ph><span>HASHTAGS</span><button class=btn id=htrefresh>↻ Quellen abrufen</button></div>
        <div class=htadd><input id=htterm placeholder="neuer-hashtag" class=htin><input id=htcolor type=color value="#5b8def"><button class=btn id=htaddbtn>+ </button></div>
        <div id=htlist class=muted>lädt…</div>
      </div>
      <div class=panel>
        <div class=ph><span>WATCHER · AKTEUR-ACCOUNTS</span><button class=btn id=watchrefresh>↻ Posts holen</button></div>
        <div class=htadd>
          <select id=watchplat class=htin style="flex:0 0 auto"><option value=mastodon>mastodon</option><option value=bluesky>bluesky</option></select>
          <input id=watchhandle placeholder="@handle" class=htin>
          <button class=btn id=watchaddbtn>+ </button>
        </div>
        <div id=watchlist class=muted>—</div>
      </div>
      <div class=panel>
        <div class=ph><span id=archdr>TRENDS · KOMBINATIONEN (News-Ko-Vorkommen)</span><span class=muted id=arcnote></span></div>
        <div id=httrends class=muted>—</div>
      </div>
      <div class=panel>
        <div class=ph><span id=htsrc_title>QUELLEN · echte Links</span></div>
        <div id=htsources class=muted>Hashtag oben anklicken…</div>
      </div>
    </div>
  </div>
</section>

<section id=quellen class=hide>
  <div class=panel>
    <div class=ph><span>QUELLEN-RANKING · dynamische Güte (Wilson-Lower-Bound auf Relevanz-Yield)</span><span class=muted id=qstat></span></div>
    <div class=qhint>Score = untere Konfidenzgrenze des Relevanz-Anteils. Stabil relevante Quellen mit Volumen steigen; kleine/noisy Quellen sinken in den Bodensatz. Kein Auto-Disable — nur Ranking.</div>
    <div class=qsyshint>RSS = Ingest-Pipeline (Artikel, LLM-Klassifizierung) · Archiv = hausinterne Dokumente via MCP · Mastodon/Bluesky/News = Hashtag-Globus (separate Aggregation)</div>
    <div class=qfilters>
      <span class="qfilter on" data-f=all onclick="setQFilter('all')">ALLE</span>
      <span class=qfilter data-f=rss onclick="setQFilter('rss')">RSS</span>
      <span class=qfilter data-f=archive onclick="setQFilter('archive')">ARCHIV</span>
      <span class=qfilter data-f=discovered onclick="setQFilter('discovered')">ENTDECKT</span>
    </div>
    <div class=qadd>
      <input id=qaname placeholder="Name der Quelle">
      <input id=qaurl placeholder="https://… (leer = auto bei Archiv)">
      <select id=qatype><option value=rss>rss</option><option value=archive>archiv</option></select>
      <select id=qatier><option value=1>Tier 1</option><option value=2>Tier 2</option><option value=3>Tier 3</option></select>
      <select id=qaregion><option>DE</option><option>NRW</option><option>BY</option><option>BW</option><option>NI</option><option>SH</option><option>EU</option><option>INT</option></select>
      <button class=btn id=qaaddbtn onclick="addSource()">+ Quelle</button>
      <button class=btn id=qdiscbtn onclick="discoverSources()" title="Scannt meistzitierte Artikel-Outlink-Domains nach RSS-Feeds; Funde landen als ENTDECKT in Quarantäne">🔍 Discover</button>
    </div>
    <table class=qtable id=qtable></table>
  </div>
  <div class=panel style="margin-top:14px">
    <div class=ph><span>EVENT-TYPEN · DB-getriebene Taxonomie</span><span class=muted id=etstat></span></div>
    <div class=qhint>Klassifiziert Meldungen per Regex (case-insensitive). 'auto' = LLM-Vorschlag in Quarantäne (enabled=0). Neue Typen greifen beim nächsten Ingest/Classify-Lauf.</div>
    <div class=qadd>
      <input id=etname placeholder="name (z.B. datenleck)" style="max-width:180px">
      <input id=etpattern placeholder="pattern (Regex, z.B. datenleck|ransomware)">
      <button class=btn id=etaddbtn onclick="addEventType()">+ Event-Typ</button>
    </div>
    <table class=qtable id=ettable></table>
  </div>
  <div class=panel style="margin-top:14px">
    <div class=ph><span>NER-ENTITÄTEN · Review-Quarantäne</span><span class=muted id=nerstat></span></div>
    <div class=qhint>Vom LLM aus dem Newsstrom extrahierte Entitäten — bestätigen (✓) oder verwerfen (✕). Extraktion via Button oder MCP-Tool extract_entities.</div>
    <div class=qadd>
      <button class=btn id=nerextract onclick="extractNer()">⚡ Extrahieren (letzte 7 Tage)</button>
      <button class=btn id=mirrorheime onclick="mirrorHeime()" title="Spiegelt das Pflegeheim-Register als Entitäten (type=einrichtung)">⌂ Heime spiegeln</button>
    </div>
    <table class=qtable id=nertable></table>
  </div>
</section>

<section id=org class=hide>
  <div class=panel>
    <div class=ph><span id=orgname>ORGANIGRAMM</span><span class=muted id=orgstats></span></div>
    <div class=orgmodes>
      <span class="tab on" data-om=flow onclick="setOrgMode('flow')">⬇ FLUSSDIAGRAMM</span>
      <span class="tab" data-om=d3 onclick="setOrgMode('d3')">◍ 3D-GRAPH</span>
    </div>
    <div id=orgflow class=muted>lädt…</div>
    <div id=org3d class=hide></div>
    <div class=muted style="margin-top:8px;font-size:10px">Tipp: Klick auf eine <b style="color:#67d98b">Einrichtung</b> (Blatt-Knoten) → begehbare 3D-Welt (WASD)</div>
  </div>
</section>

<div id=scene3d class=hide>
  <div id=sccanvas></div>
  <div class=schud>
    <div class=schd><span id=scname>Einrichtung</span><span class=scsub id=scsub></span></div>
    <div class=scstyles>
      <span class="scbtn on" data-st=real onclick="setSceneStyle('real')">1 · Real</span>
      <span class=scbtn data-st=neon onclick="setSceneStyle('neon')">2 · Neon</span>
      <span class=scbtn data-st=toon onclick="setSceneStyle('toon')">3 · Toon</span>
      <span class=scbtn data-st=blueprint onclick="setSceneStyle('blueprint')">4 · Blueprint</span>
    </div>
    <span class=schint>WASD · Maus · Shift rennen · ESC raus</span>
    <button class=btn id=scexit>✕ schließen</button>
  </div>
  <div id=sclock class=sclock>▶ Klick zum Loslaufen</div>
  <div id=scload class=scload>lädt OSM-Gebäude…</div>
</div>

<div id=globetip></div>
<!--CONFIG-->
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/PointerLockControls.js"></script>
<script>
const EVC={insolvenz:'#ff4d4d',politik:'#5b8def',expansion:'#2ecc71',personalie:'#f0a830',produkt:'#9b6dff',auszeichnung:'#26c6da',schliessung:'#ff7043'};
const evc=t=>EVC[t]||'#7a8290';
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const $=id=>document.getElementById(id);
let curEntity=null, curEvent=null;

async function j(u,o){const r=await fetch(u,o);return r.json();}

function renderOverview(o){
  const s=o.stats;
  $('tiles').innerHTML=[
    ['HEIME',s.heime,''],['MELDUNGEN',s.artikel, o.neu_24h?('+'+o.neu_24h+' / 24h'):''],
    ['RELEVANT',s.artikel_relevant,''],['QUELLEN',s.quellen_aktiv+'/'+s.quellen,'live']
  ].map(t=>`<div class=tile><div class=tl>${t[0]}</div><div class=tv>${t[1]}</div><div class=td>${t[2]}</div></div>`).join('');
  const nb=$('newbadge'); nb.textContent=o.neu_seit_zuletzt+' neu seit zuletzt';
  nb.style.opacity=o.neu_seit_zuletzt?1:.4;
  $('feednew').innerHTML=o.neu_seit_zuletzt?`<span style="color:#2ecc71">${o.neu_seit_zuletzt} neu</span>`:'';
  const emax=Math.max(1,...o.events.map(e=>e.n));
  $('events').innerHTML=o.events.map(e=>`<div class=bar><span class=bl>${esc(e.event_type)}</span><span class=btrack><span class=bfill style="width:${100*e.n/emax}%;background:${evc(e.event_type)}"></span></span><span class=bn>${e.n}</span></div>`).join('')||'<span class=muted>–</span>';
  const lmax=Math.max(1,...o.entities.map(e=>e.articles));
  $('ents').innerHTML=o.entities.filter(e=>e.articles>0).slice(0,9).map(e=>`<div class=lr><span class=ln>${esc(e.name)}</span><span class=ltrack><span class=lfill style="width:${100*e.articles/lmax}%"></span></span><span class=lc>${e.articles}</span></div>`).join('');
  $('sources').innerHTML=o.sources.map(s=>`<div class=src><span class=dot style="background:${s.enabled?'#2ecc71':'#ff4d4d'}"></span><span>${esc(s.name).slice(0,30)}</span><span class=sr>${esc(s.region||'')}</span></div>`).join('');
}
function renderFeed(items){
  $('feed').innerHTML=items.map(a=>{const c=evc(a.event_type);const et=(a.event_type||'·').toUpperCase();
    const ent=a.entities?` · ${esc(a.entities)}`:'';
    const tags=(a.hashtags||'').split(',').filter(Boolean).slice(0,4).map(h=>`<span class=fhash>#${esc(h)}</span>`).join('');
    return `<div class="row${a.is_new?' fresh':''}"><span class=dt>${esc((a.published||'').slice(0,10))}</span><span class=ev style="color:${c};border-color:${c}">${et}</span>`+
    `<a class=ti href="${esc(a.link||'#')}" target=_blank>${esc((a.title||'').slice(0,90))}</a><span class=meta>${esc(a.source_domain||'')}${ent} ${tags}</span></div>`;}).join('');
}
function renderGraph(g){
  const nodes=g.nodes.filter(n=>n.articles>0).slice(0,9);
  const W=640,H=470,cx=300,cy=235,R=170,emax=Math.max(1,...nodes.map(n=>n.articles));
  let edges='',circ='';
  nodes.forEach((n,i)=>{const ang=-Math.PI/2+2*Math.PI*i/nodes.length;const x=cx+R*Math.cos(ang),y=cy+R*Math.sin(ang);
    const rad=15+24*(n.articles/emax);
    edges+=`<path d="M${cx} ${cy} Q${(cx+x)/2+(y-cy)*.12} ${(cy+y)/2-(x-cx)*.12} ${x} ${y}" stroke="#34406a" fill="none" stroke-width="1.6" />`;
    circ+=`<g class=node onclick="selEntity('${esc(n.name).replace(/'/g,'')}')"><circle cx="${x}" cy="${y}" r="${rad}" fill="#1c2c4d" stroke="#6f9bff" stroke-width="2" /><text x="${x}" y="${y}" text-anchor=middle dy=4 fill=#fff font-size=13 font-weight=600>${n.articles}</text><text x="${x}" y="${y+rad+15}" text-anchor=middle fill=#aeb8c6 font-size=11>${esc(n.name).slice(0,16)}</text></g>`;});
  $('graphsvg').innerHTML=`<svg viewBox="0 0 ${W} ${H}" width=100% height=470>${edges}${circ}<circle cx=${cx} cy=${cy} r=44 fill=#0f1622 stroke=#6f9bff stroke-width=2.5 /><text x=${cx} y=${cy-4} text-anchor=middle fill=#fff font-size=13 font-weight=700>${esc(g.hub.label)}</text><text x=${cx} y=${cy+12} text-anchor=middle fill=#5b8def font-size=10>${g.hub.count} Mldg.</text></svg>`;
}
async function selEntity(name){
  curEntity=name;
  const e=await j('api/entity?name='+encodeURIComponent(name));
  if(!e||!e.name){$('gdetail').innerHTML='<span class=muted>keine Daten</span>';return;}
  $('gdetail').innerHTML=`<div class=en>${esc(e.name)}</div><div class=et>${esc(e.type)} · ${e.article_count} Meldungen</div>`+
    (e.recent||[]).map(a=>`<div class=tlitem><span class=d style="background:${evc(a.event_type)}"></span><div><div>${esc((a.title||'').slice(0,54))}</div><div class=muted>${esc((a.published||'').slice(0,10))} · ${esc(a.event_type||'—')}</div></div></div>`).join('');
}
// ── Zeitstrahl-Helper: Items nach Datum verorten, lane-gestapelt, klickbar ──
function renderTimeAxis(el, items, colorFn, labelFn){
  items=(items||[]).map(i=>({i,t:Date.parse(i.published)})).filter(o=>!isNaN(o.t)).sort((a,b)=>a.t-b.t);
  if(!items.length){el.className='muted';el.style.height='';el.textContent='keine datierten Einträge';return;}
  el.className='tax';
  let min=items[0].t,max=items[items.length-1].t; if(min===max){min-=864e5;max+=864e5;}
  const span=max-min,rowH=20,top=4,gap=11,laneX=[];let maxLane=0;
  const markers=items.map(({i,t})=>{
    const x=2+95*(t-min)/span;let lane=0;while(lane<laneX.length&&x-laneX[lane]<gap)lane++;
    laneX[lane]=x;if(lane>maxLane)maxLane=lane;const y=top+lane*rowH;
    return `<a class=tax-item href="${esc(i.link||'#')}" target=_blank style="left:${x}%;top:${y}px" title="${esc((i.published||'').slice(0,10))} · ${esc(i.title||'')}"><span class=d style="background:${colorFn(i)}"></span>${esc(labelFn(i))}</a>`;
  }).join('');
  let ticks='';const y0=new Date(min).getUTCFullYear(),y1=new Date(max).getUTCFullYear();
  for(let y=y0;y<=y1;y++)for(let m=0;m<12;m++){const tm=Date.UTC(y,m,1);if(tm<min||tm>max)continue;
    const x=2+95*(tm-min)/span;ticks+=`<span class=tax-tick style="left:${x}%">${String(m+1).padStart(2,'0')}/${String(y).slice(2)}</span>`;}
  el.style.height=(top+(maxLane+1)*rowH+30)+'px';
  el.innerHTML=`<div class=tax-line></div>${ticks}${markers}`;
}
async function renderTimeline(){
  const types=['','insolvenz','politik','expansion','personalie','produkt','auszeichnung','schliessung'];
  $('tlfilter').innerHTML=types.map(t=>`<span class=tab style="padding:4px 10px${curEvent===t?';background:'+evc(t||'politik')+';color:#06142e':''}" onclick="curEvent='${t}';loadTimeline()">${t?t:'alle Events'}</span>`).join('');
  loadTimeline();
}
async function loadTimeline(){
  let items=await j('api/feed?limit=120');
  if(curEvent) items=items.filter(a=>a.event_type===curEvent);
  renderTimeAxis($('tltax'), items, a=>evc(a.event_type), a=>(a.source_domain||'').replace('www.','').slice(0,22));
}
// ── DISKURS: eine Themen-Seite, Pro/Contra-Zeitachse, Farbe = Position ──
function renderValenceChart(el, items){
  items=(items||[]).map(i=>({i,t:Date.parse(i.published)})).filter(o=>!isNaN(o.t)).sort((a,b)=>a.t-b.t);
  if(!items.length){el.className='muted';el.style.height='';el.textContent='noch keine Positionen (Stance-Analyse läuft)';return;}
  el.className='vchart';
  let min=items[0].t,max=items[items.length-1].t; if(min===max){min-=864e5;max+=864e5;}
  const span=max-min,rowH=19,gap=11,proX=[],conX=[],neuX=[];let proMax=0,conMax=0;
  const placed=items.map(({i,t})=>{const x=2+95*(t-min)/span;
    const v=i.valence==='pro'?'pro':i.valence==='contra'?'contra':'neutral';
    const lanes=v==='pro'?proX:v==='contra'?conX:neuX;let lane=0;while(lane<lanes.length&&x-lanes[lane]<gap)lane++;lanes[lane]=x;
    if(v==='pro'&&lane>proMax)proMax=lane;if(v==='contra'&&lane>conMax)conMax=lane;return {i,x,v,lane};});
  const topPad=18,center=topPad+(proMax+1)*rowH;
  const markers=placed.map(({i,x,v,lane})=>{
    const y=v==='pro'?center-(lane+1)*rowH:v==='contra'?center+(lane+1)*rowH:center-9;
    return `<a class=tax-item href="${esc(i.link||'#')}" target=_blank style="left:${x}%;top:${y}px" title="${esc((i.published||'').slice(0,10))} · ${esc(i.position||'')} (${esc(i.valence||'')}) · ${esc(i.title||'')}"><span class=d style="background:${i.color||'#7a8290'}"></span>${esc(i.entity)}</a>`;
  }).join('');
  let ticks='';const y0=new Date(min).getUTCFullYear(),y1=new Date(max).getUTCFullYear();
  for(let y=y0;y<=y1;y++)for(let m=0;m<12;m++){const tm=Date.UTC(y,m,1);if(tm<min||tm>max)continue;
    const x=2+95*(tm-min)/span;ticks+=`<span class=tax-tick style="left:${x}%;bottom:2px">${String(m+1).padStart(2,'0')}/${String(y).slice(2)}</span>`;}
  el.style.height=(center+(conMax+1)*rowH+28)+'px';
  el.innerHTML=`<div class=vbase style="top:${center}px"></div><span class=vlbl style="top:${center-15}px;color:#2ecc71">PRO ▲</span><span class=vlbl style="top:${center+5}px;color:#ff6b6b">CONTRA ▼</span>${ticks}${markers}`;
}
let DTOPICS=[],dtIdx=0;
async function loadDiscourseTopic(){
  if(!DTOPICS.length){DTOPICS=await j('api/discourse_topics');}
  if(!DTOPICS.length){$('dtchart').className='muted';$('dtchart').textContent='Positionen werden synthetisiert…';return;}
  dtIdx=((dtIdx%DTOPICS.length)+DTOPICS.length)%DTOPICS.length;
  const topic=DTOPICS[dtIdx];
  $('tname').textContent=topic+'  ('+(dtIdx+1)+'/'+DTOPICS.length+')';
  const d=await j('api/discourse_topic?topic='+encodeURIComponent(topic));
  $('dtnote').textContent=d.note||'';
  $('dtlegend').innerHTML=(d.legend||[]).map(p=>`<span class=lg><span class=sw style="background:${p.color}"></span>${esc(p.label)} <span style="color:${p.valence==='pro'?'#2ecc71':p.valence==='contra'?'#ff6b6b':'#7a8290'}">(${esc(p.valence)})</span></span>`).join('')||'<span class=muted>Positionen werden synthetisiert…</span>';
  renderValenceChart($('dtchart'), d.items);
}
// ── three.js Helfer (Globus + Org-3D teilen sich Renderer-Erzeugung) ──
function makeRenderer(el){el.innerHTML='';const r=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});
  r.setPixelRatio(Math.min(2,devicePixelRatio));r.setSize(el.clientWidth,el.clientHeight||600);el.appendChild(r.domElement);return r;}
function ll2v(lat,lon,r){const phi=(90-lat)*Math.PI/180,th=(lon+180)*Math.PI/180;
  return new THREE.Vector3(-r*Math.sin(phi)*Math.cos(th),r*Math.cos(phi),r*Math.sin(phi)*Math.sin(th));}
function disposeScene(h){const tp=document.getElementById('org3dtip');if(tp)tp.style.display='none';
  const gt=document.getElementById('globetip');if(gt)gt.style.display='none';
  if(h){cancelAnimationFrame(h.raf);
    if(h._mmHandler&&h.renderer)h.renderer.domElement.removeEventListener('mousemove',h._mmHandler);
    if(h._mlHandler&&h.renderer)h.renderer.domElement.removeEventListener('mouseleave',h._mlHandler);
    if(h.arcs)h.arcs.forEach(o=>{o.tube.geometry.dispose();o.tubeMat.dispose();o.glow.geometry.dispose();o.glowMat.dispose();});
    if(h.cloudGeo)h.cloudGeo.dispose();if(h.cloudMat)h.cloudMat.dispose();
    if(h.langTex)h.langTex.dispose();if(h.langMat)h.langMat.dispose();if(h.langGeo)h.langGeo.dispose();
    if(h.renderer){h.renderer.dispose();const d=h.renderer.domElement;if(d&&d.parentNode)d.parentNode.removeChild(d);}}}

// ── HASHTAG-GLOBUS ──
const SRCC={mastodon:'#6364ff',bluesky:'#1185fe',news:'#f0a830'};
const TRUSTC={institution:'#2ecc71',press:'#f0a830',social:'#7a8aa0'};
const TRUSTL={institution:'⚖ Amt',press:'📰 Presse',social:'💬 Social'};
const TIERL=['DE','EU','INT'];        // lang_tier 0/1/2 → Lesbarkeits-Marker (Text, Flaggen rendern unzuverlässig)
const TIERC=['#67d98b','#7fb0ff','#7a8aa0'];
function trustBadge(s){const t=s.trust||'social';const ti=Math.min(s.lang_tier==null?2:s.lang_tier,2);  // WHY: lang_tier 0 (DE) ist falsy → kein ||
  return `<span class=trustbadge style="color:${TRUSTC[t]};border-color:${TRUSTC[t]}">${TRUSTL[t]}</span>`+
    `<span class=tierbadge style="color:${TIERC[ti]}" title="Sprach-Tier ${s.lang_tier} (0=DE, 1=EU/westlich, 2=fern)">${TIERL[ti]}</span>`;}
let HTDATA=null, GLOBE=null, ARCS_ON=true, LANG_ON=true, LANGGEO=null;
// Age-decay: marker brightness fades linearly with age, gone after EXPIRY days (0 = off).
// Server-injected via window.POST_EXPIRY_DAYS. Sources stay in the DB — this is purely visual.
const POST_EXPIRY_DAYS=(typeof window.POST_EXPIRY_DAYS==='number')?window.POST_EXPIRY_DAYS:21;
function ageDecay(published){
  if(POST_EXPIRY_DAYS<=0)return 1;                       // disabled → no fade
  const t=Date.parse(published);
  if(isNaN(t))return 1;                                  // missing/invalid → treat as fresh
  const ageDays=(Date.now()-t)/86400000;
  return Math.max(0,1-ageDays/POST_EXPIRY_DAYS);
}
// Radiales Glow-Sprite (Kern→Halo→transparent) — ersetzt das alte body+halo-Mesh-Paar
// durch EINE Punkt-Textur. Modul-global, einmal gebaut, von allen Globen geteilt.
let _GLOW_TEX=null;
function glowSprite(){
  if(_GLOW_TEX)return _GLOW_TEX;
  const S=64,cv=document.createElement('canvas');cv.width=cv.height=S;
  const g=cv.getContext('2d');const grd=g.createRadialGradient(S/2,S/2,0,S/2,S/2,S/2);
  grd.addColorStop(0.0,'rgba(255,255,255,1)');
  grd.addColorStop(0.22,'rgba(255,255,255,0.85)');
  grd.addColorStop(0.55,'rgba(255,255,255,0.28)');
  grd.addColorStop(1.0,'rgba(255,255,255,0)');
  g.fillStyle=grd;g.fillRect(0,0,S,S);
  _GLOW_TEX=new THREE.CanvasTexture(cv);
  _GLOW_TEX.minFilter=THREE.LinearFilter;_GLOW_TEX.magFilter=THREE.LinearFilter;
  return _GLOW_TEX;
}

// ── Point-in-Polygon (ray-casting, lon/lat, handles Polygon+MultiPolygon+holes) ──
function pipRing(lon,lat,ring){
  // Standard ray-cast: count crossings of a horizontal ray to the right.
  let inside=false;
  for(let i=0,j=ring.length-1;i<ring.length;j=i++){
    const xi=ring[i][0],yi=ring[i][1],xj=ring[j][0],yj=ring[j][1];
    if(((yi>lat)!==(yj>lat))&&(lon<(xj-xi)*(lat-yi)/(yj-yi)+xi))inside=!inside;
  }
  return inside;
}
function pipPolygon(lon,lat,coords){
  // coords = array of rings; first = exterior, rest = holes.
  // evenodd: exterior must contain point, holes flip it back → same as checking
  // exterior AND NOT any hole.
  if(!pipRing(lon,lat,coords[0]))return false;
  for(let h=1;h<coords.length;h++){if(pipRing(lon,lat,coords[h]))return false;}
  return true;
}
function resolveCountryIso(lon,lat,features){
  for(let i=0;i<features.length;i++){
    const f=features[i];const g=f.geometry;if(!g)continue;
    const pr=f.properties||{};
    const iso=(pr.ISO_A2_EH&&pr.ISO_A2_EH!=='-99')?pr.ISO_A2_EH:pr.ISO_A2;
    if(!iso||iso==='-99')continue;
    const polys=g.type==='Polygon'?[g.coordinates]:g.type==='MultiPolygon'?g.coordinates:[];
    for(let p=0;p<polys.length;p++){if(pipPolygon(lon,lat,polys[p]))return iso;}
  }
  return null;
}
function precomputeMarkerLangs(latArr,lonArr,langsOut,n,lgData,htLangs){
  // Füllt langs[] indexparallel zur Punktwolke (statt mesh.userData.lang).
  const validLangs=new Set((htLangs||[]).map(l=>l.code));
  const features=(lgData&&lgData.geojson&&lgData.geojson.features)||[];
  const clMap=(lgData&&lgData.country_language)||{};
  const fallback=validLangs.has('en')?'en':(validLangs.size?[...validLangs][0]:'de');
  for(let i=0;i<n;i++){
    const iso=resolveCountryIso(lonArr[i],latArr[i],features);
    let lang=iso?clMap[iso]:null;
    if(!lang||!validLangs.has(lang))lang=fallback;
    langsOut[i]=lang;
  }
}
// Soft language overlay: paint country polygons by language color onto an equirect
// canvas, blur it, drape as a translucent texture on a sphere slightly above earth.
async function ensureLangGeo(){if(LANGGEO===null){try{LANGGEO=await j('api/langgeo');}catch(e){console.warn('langgeo load failed',e);LANGGEO=false;}}return LANGGEO;}
function buildLangCanvas(geo,clMap,langs){
  const W=2048,H=1024;
  const code2col={};(langs||[]).forEach(l=>{code2col[l.code]=l.color;});
  const scratch=document.createElement('canvas');scratch.width=W;scratch.height=H;
  const sc=scratch.getContext('2d');sc.clearRect(0,0,W,H);
  const X=lon=>(lon+180)/360*W, Y=lat=>(90-lat)/180*H;   // matches ll2v / SphereGeometry UVs
  function ringPath(ring){sc.beginPath();ring.forEach((pt,i)=>{const x=X(pt[0]),y=Y(pt[1]);i?sc.lineTo(x,y):sc.moveTo(x,y);});sc.closePath();}
  (geo.features||[]).forEach(f=>{
    const pr=f.properties||{};
    const iso=(pr.ISO_A2_EH&&pr.ISO_A2_EH!=='-99')?pr.ISO_A2_EH:pr.ISO_A2;  // ISO_A2_EH primary (FR/NO/XK/TW); ISO_A2 fallback for rare -99 cases
    const code=clMap[iso];const col=code&&code2col[code];
    if(!col)return;                                       // no mapping (e.g. ISO '-99') → skip
    sc.fillStyle=col;
    const g=f.geometry;if(!g)return;
    const polys=g.type==='Polygon'?[g.coordinates]:g.type==='MultiPolygon'?g.coordinates:[];
    polys.forEach(poly=>{poly.forEach(ring=>ringPath(ring));sc.fill('evenodd');});
  });
  const blurred=document.createElement('canvas');blurred.width=W;blurred.height=H;
  const bc=blurred.getContext('2d');bc.filter='blur(18px)';bc.drawImage(scratch,0,0);bc.filter='none';
  return blurred;
}
function renderLangLegend(langs){const body=$('langlegbody');if(!body)return;
  body.innerHTML=(langs||[]).map(l=>`<div class=lgrow title="${esc(l.native||'')}"><span class=lgsw style="background:${l.color}"></span>${esc(l.name)}</div>`).join('');
  $('langlegend').classList.toggle('hide',!LANG_ON);}
async function loadGlobe(){HTDATA=await j('api/hashtags');renderHtLegend();renderTrends();initGlobe(HTDATA.points);loadWatch();
  $('globestat').textContent=`${HTDATA.points.length} Geo-Punkte · ${HTDATA.total_posts} Posts`;}
function renderTrends(){const pr=(HTDATA&&HTDATA.trends)||[];$('httrends').className=pr.length?'':'muted';
  $('httrends').innerHTML=pr.map(p=>`<div class=trrow><span class=trtag style="color:${p.ca}">#${esc(p.a)}</span><span class=trplus>+</span><span class=trtag style="color:${p.cb}">#${esc(p.b)}</span><span class=trn>${p.n}×</span></div>`).join('')||'noch keine Kombinationen';}
function renderHtLegend(){const d=HTDATA;$('htlist').className='';
  $('htlist').innerHTML=d.legend.map(t=>`<div class="htrow${t.active?'':' off'}" title="News-Treffer: ${t.news||0} · Ko-Vorkommen: ${t.cooc||0} · Gewicht: ${t.weight||0}">`+
    `<input type=color class=swc value="${t.color}" onchange="setColor(${t.id},this.value)" title="Farbe ändern">`+
    `<span class=nm onclick="showHtSources(${t.id},'${esc(t.term).replace(/'/g,'')}')">#${esc(t.term)}</span>`+
    `${t.weight>0?`<span class=trw title="Trend-Gewicht">🔥${t.weight}</span>`:''}`+
    `<span class=geo>${t.geo}📍</span><span class=ct>${t.count}</span>`+
    `<span class=tog onclick="toggleHt(${t.id},${t.active?0:1})" title="aktiv/inaktiv">${t.active?'◉':'○'}</span>`+
    `<span class=x onclick="delHt(${t.id},this)" title="löschen — nochmal klick = weg">✕</span></div>`).join('')||'<span class=muted>keine Hashtags</span>';}
function actorRow(id){
  const acts=((HTDATA&&HTDATA.actors)||{})[id]||[];
  if(!acts.length)return '';
  const col=(HTDATA&&HTDATA.actor_colors)||{};
  const chips=acts.map(a=>`<span class=actorchip style="color:${col[a.type]||'#9fb0c8'};border-color:${col[a.type]||'#9fb0c8'}" title="${esc(a.type)} · ${a.n}× gemeinsam">${esc(a.name)}<span class=an>${a.n}</span></span>`).join('');
  return `<div class=actorrow><span class=actorlbl>AKTEURE</span>${chips}</div>`;}
function showHtSources(id,term){$('htsrc_title').textContent='QUELLEN · #'+term;
  const list=(HTDATA.sources[id]||[]);$('htsources').className=list.length?'':'muted';
  $('htsources').innerHTML=actorRow(id)+list.map(s=>`<a class=htsrc href="${esc(s.url)}" target=_blank>`+
    trustBadge(s)+
    `<span class=srcbadge style="color:${SRCC[s.source]||'#888'};border-color:${SRCC[s.source]||'#888'}">${esc(s.source)}</span>`+
    `${esc((s.content||'').slice(0,100))}<span class=sm> — ${esc(s.author||'')} · ${esc((s.published||'').slice(0,10))}</span></a>`).join('')
    ||'noch keine Quellen — „↻ Quellen abrufen“ klicken';}
// WHY: confirm()/prompt() werden in (eingebetteten) iframes von Browsern unterdrückt →
// Delete/Color liefen ins Leere. Daher Zwei-Klick-Delete + Inline-Color-Input, non-modal.
const POST=(u,o)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o||{})});
async function addHt(){const term=$('htterm').value.trim();if(!term)return;
  const b=$('htaddbtn');b.textContent='…';b.disabled=true;
  try{const res=await (await POST('api/hashtag/add',{term,color:$('htcolor').value})).json();
    $('htterm').value='';await loadGlobe();                       // sofort sichtbar (0 Posts)
    if(res&&res.id){showHtSources(res.id,res.term);
      POST('api/hashtags/refresh',{id:res.id,limit:15}).then(()=>loadGlobe()).catch(()=>{});}  // Quellen im Hintergrund
  }catch(e){console.warn('add failed',e);}finally{b.textContent='+';b.disabled=false;}}
function delHt(id,el){
  if(el&&el.dataset.armed){POST('api/hashtag/delete',{id}).then(loadGlobe);return;}
  if(el){el.dataset.armed='1';el.textContent='↩?';el.style.color='#fff';
    setTimeout(()=>{if(el){el.removeAttribute('data-armed');el.textContent='✕';el.style.color='';}},2500);}}
function toggleHt(id,active){POST('api/hashtag/update',{id,active:!!active}).then(loadGlobe);}
function setColor(id,c){POST('api/hashtag/update',{id,color:c}).then(loadGlobe);}
async function refreshHt(){const b=$('htrefresh');b.textContent='lädt…';b.disabled=true;
  try{await POST('api/hashtags/refresh',{limit:15});}finally{b.textContent='↻ Quellen abrufen';b.disabled=false;}loadGlobe();}
let WATCH=[];
async function loadWatch(){try{WATCH=(await j('api/watch')).accounts||[];}catch(e){WATCH=[];}renderWatch();}
function renderWatch(){const el=$('watchlist');if(!el)return;el.className=WATCH.length?'':'muted';
  el.innerHTML=WATCH.map(a=>`<div class="htrow${a.active?'':' off'}" title="${esc(a.platform)}${a.label?' · '+esc(a.label):''}">`+
    `<span class=srcbadge style="color:${SRCC[a.platform]||'#888'};border-color:${SRCC[a.platform]||'#888'}">${esc(a.platform)}</span>`+
    `<span class=nm>@${esc(a.handle)}</span>`+
    `<span class=tog onclick="toggleWatch(${a.id},${a.active?0:1})" title="aktiv/inaktiv">${a.active?'◉':'○'}</span>`+
    `<span class=x onclick="delWatch(${a.id})" title="entfernen">✕</span></div>`).join('')||'<span class=muted>keine Accounts — Handle eintragen + „+“</span>';}
async function addWatch(){const handle=$('watchhandle').value.trim();if(!handle)return;
  const b=$('watchaddbtn');b.textContent='…';b.disabled=true;
  try{await POST('api/watch/add',{platform:$('watchplat').value,handle});$('watchhandle').value='';await loadWatch();}
  catch(e){console.warn('watch add failed',e);}finally{b.textContent='+';b.disabled=false;}}
async function delWatch(id){await POST('api/watch/remove',{id});loadWatch();}
async function toggleWatch(id,active){await POST('api/watch/toggle',{id,active});loadWatch();}
async function refreshWatch(){const b=$('watchrefresh');b.textContent='lädt…';b.disabled=true;
  try{await POST('api/watch/refresh',{});}finally{b.textContent='↻ Posts holen';b.disabled=false;}loadGlobe();}
function initGlobe(points){disposeScene(GLOBE);const el=$('globecanvas');const renderer=makeRenderer(el);
  const scene=new THREE.Scene();
  const camera=new THREE.PerspectiveCamera(45,el.clientWidth/(el.clientHeight||600),0.002,100);
  camera.position.copy(ll2v(32,12,2.7));  // Startblick auf Europa/DE (Cluster)
  const controls=new THREE.OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=0.08;
  controls.enablePan=false;controls.minDistance=1.012;controls.maxDistance=6;controls.autoRotate=false;
  controls.zoomSpeed=0.8;controls.rotateSpeed=0.7;  // nah ranzoomen (DE beobachten) + feinere Drehung
  scene.add(new THREE.AmbientLight(0x4a5a7a,0.28));  // dunkel → Tag/Nacht-Kontrast deutlich
  // Sonne: Richtung = subsolarer Punkt aus aktueller UTC-Zeit (Berlin-Tageszeit), live nachgeführt.
  const sun=new THREE.DirectionalLight(0xfff1cc,1.7);scene.add(sun);scene.add(sun.target);
  const sunMesh=new THREE.Mesh(new THREE.SphereGeometry(0.2,28,28),new THREE.MeshBasicMaterial({color:0xffe680}));scene.add(sunMesh);
  const sunGlow=new THREE.Mesh(new THREE.SphereGeometry(0.2,28,28),new THREE.MeshBasicMaterial({color:0xffd24d,transparent:true,opacity:0.45,blending:THREE.AdditiveBlending,depthWrite:false}));sunMesh.add(sunGlow);sunGlow.scale.setScalar(3.4);
  function subsolar(){const n=new Date();const utcH=n.getUTCHours()+n.getUTCMinutes()/60+n.getUTCSeconds()/3600;
    const lon=15*(12-utcH);  // Längengrad, an dem die Sonne im Zenit steht
    const start=Date.UTC(n.getUTCFullYear(),0,0);const doy=(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate())-start)/864e5;
    const decl=-23.44*Math.cos((2*Math.PI/365)*(doy+10));return ll2v(decl,lon,1).normalize();}
  function updateSun(){const d=subsolar();sun.position.copy(d.clone().multiplyScalar(5));sun.target.position.set(0,0,0);
    sunMesh.position.copy(d.clone().multiplyScalar(3.4));}
  updateSun();
  // Farbige Erde (Blue Marble) + dunkles Emissive → Sonnenlicht/Tag-Nacht sichtbar.
  const mat=new THREE.MeshPhongMaterial({color:0x2a3550,emissive:0x05080f,shininess:10,transparent:true,opacity:0.99});
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(1,64,64),mat));
  new THREE.TextureLoader().load('https://unpkg.com/three-globe/example/img/earth-blue-marble.jpg',
    t=>{mat.map=t;mat.color.set(0xffffff);mat.needsUpdate=true;},undefined,()=>{});
  scene.add(new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.SphereGeometry(1.003,24,16)),
    new THREE.LineBasicMaterial({color:0x2a4a7a,transparent:true,opacity:0.13})));
  // ── Weiches Sprach-Overlay: translucent language zones above the blue-marble earth ──
  let langMesh=null,langTex=null,langMat=null,langGeo=null;
  (async function buildLangOverlay(){
    const lg=await ensureLangGeo();if(!lg||!lg.geojson)return;if(GLOBE!==H)return;  // stale (scene re-inited)
    const cv=buildLangCanvas(lg.geojson,lg.country_language||{},(HTDATA&&HTDATA.languages)||[]);
    langTex=new THREE.CanvasTexture(cv);langTex.needsUpdate=true;  // flipY default → aligns with blue-marble + ll2v
    langGeo=new THREE.SphereGeometry(1.003,96,64);
    langMat=new THREE.MeshBasicMaterial({map:langTex,transparent:true,opacity:0.22,depthWrite:false,blending:THREE.NormalBlending});
    langMesh=new THREE.Mesh(langGeo,langMat);langMesh.visible=LANG_ON;scene.add(langMesh);
    H.langMesh=langMesh;H.langTex=langTex;H.langMat=langMat;H.langGeo=langGeo;
    renderLangLegend((HTDATA&&HTDATA.languages)||[]);
  })();
  const group=new THREE.Group();scene.add(group);
  // ── GPU-gebatchte Punktwolke: EIN THREE.Points statt 2 Meshes/Punkt (skaliert auf 25k+) ──
  const buckets={};points.forEach(p=>{const k=p.term+Math.round(p.lat)+','+Math.round(p.lon);buckets[k]=(buckets[k]||0)+1;});
  let _N=0,_expired=0;const _keep=new Array(points.length);
  for(let i=0;i<points.length;i++){const d=ageDecay(points[i].published);_keep[i]=d;if(d>=0.05)_N++;else _expired++;}
  const positions=new Float32Array(_N*3),pcolors=new Float32Array(_N*3),psizes=new Float32Array(_N);
  const urls=new Array(_N),terms=new Array(_N),langs=new Array(_N);
  const ptLat=new Float32Array(_N),ptLon=new Float32Array(_N);
  const _c=new THREE.Color();let j=0,_sizeSum=0;
  for(let i=0;i<points.length;i++){const decay=_keep[i];if(decay<0.05)continue;
    const p=points[i];const k=p.term+Math.round(p.lat)+','+Math.round(p.lon);
    const inten=Math.min(1,(buckets[k]||1)/5);const w=p.weight||0;
    const base=(0.006+0.013*inten)*(0.7+1.1*w);   // Trend-Gewicht → Größe (wie zuvor)
    const bright=(0.55+0.45*w)*decay;             // Alter dämpft Helligkeit (wie zuvor)
    const pos=ll2v(p.lat,p.lon,1.012);
    positions[j*3]=pos.x;positions[j*3+1]=pos.y;positions[j*3+2]=pos.z;
    _c.set(p.color||'#5b8def').multiplyScalar(bright);
    pcolors[j*3]=_c.r;pcolors[j*3+1]=_c.g;pcolors[j*3+2]=_c.b;
    psizes[j]=base;_sizeSum+=base;
    urls[j]=p.url;terms[j]=p.term;ptLat[j]=p.lat;ptLon[j]=p.lon;j++;}
  const _avgSize=_N?_sizeSum/_N:0.01;
  const cloudGeo=new THREE.BufferGeometry();
  cloudGeo.setAttribute('position',new THREE.BufferAttribute(positions,3));
  cloudGeo.setAttribute('color',new THREE.BufferAttribute(pcolors,3));
  cloudGeo.setAttribute('size',new THREE.BufferAttribute(psizes,1));
  const cloudMat=new THREE.ShaderMaterial({
    uniforms:{uTex:{value:glowSprite()},uZoom:{value:1.0},uPx:{value:(el.clientHeight||600)}},
    vertexShader:'attribute float size;attribute vec3 color;varying vec3 vColor;uniform float uZoom;uniform float uPx;'+
      'void main(){vColor=color;vec4 mv=modelViewMatrix*vec4(position,1.0);'+
      'float att=uPx/max(0.0001,-mv.z);gl_PointSize=clamp(size*uZoom*att,1.0,64.0);'+
      'gl_Position=projectionMatrix*mv;}',
    fragmentShader:'uniform sampler2D uTex;varying vec3 vColor;'+
      'void main(){vec4 s=texture2D(uTex,gl_PointCoord);if(s.a<0.01)discard;'+
      'gl_FragColor=vec4(vColor*s.rgb,s.a);}',
    transparent:true,blending:THREE.AdditiveBlending,depthWrite:false,depthTest:true,
  });
  const cloud=new THREE.Points(cloudGeo,cloudMat);group.add(cloud);
  // Watched-Akteur-Posts: eigener Marker-Typ (Oktaeder), Farbe nach Entitäts-Typ.
  const wgeo=new THREE.OctahedronGeometry(1,0);
  ((HTDATA&&HTDATA.watched)||[]).forEach(w=>{
    if(w.lat==null||w.lon==null)return;
    const wm=new THREE.Mesh(wgeo,new THREE.MeshBasicMaterial({color:w.color||'#9fb0c8'}));
    wm.position.copy(ll2v(w.lat,w.lon,1.012));wm.scale.setScalar(0.024);
    wm.userData={url:w.url,term:w.entity||'',watch:true,lat:w.lat,lon:w.lon};group.add(wm);});
  if(_expired)console.info(`Globe: ${_expired} Marker älter als ${POST_EXPIRY_DAYS}d ausgeblendet (DB unverändert).`);
  // ── Ko-Vorkommen-Arcs: Centroid je Hashtag-Term (Mittel der Einheitsvektoren → Antimeridian-sicher) ──
  const arcGroup=new THREE.Group();scene.add(arcGroup);
  const ARCS=[];                                   // {curve,glow,tube,tubeMat,speed,phase,glowBase}
  // Shared arc geometry: lifted QuadraticBezier tube + travelling glow. nn∈0..1 = relative
  // strength (drives thickness/speed/glow). `kind` differentiates concept vs cluster arcs.
  function buildArc(A,B,col,nn,kind,i){
    const mid=A.clone().add(B).multiplyScalar(0.5);const lift=mid.length()>1e-6?1.25+0.25*Math.min(1,A.distanceTo(B)/1.6):1.35;
    const ctrl=mid.clone().normalize().multiplyScalar(lift);
    const curve=new THREE.QuadraticBezierCurve3(A.clone(),ctrl,B.clone());
    // cluster (co-occurrence) arcs sit a touch thicker + brighter so they read as the denser net,
    // without inventing a new hue that would clash with the language overlay.
    const isCluster=kind==='cluster';
    const tint=isCluster?col.clone().lerp(new THREE.Color(0xffffff),0.18):col;
    const radius=(isCluster?0.0021:0.0016)+0.0022*nn;
    const opBase=(isCluster?0.24:0.18)+0.22*nn;
    const tubeMat=new THREE.MeshBasicMaterial({color:tint,transparent:true,opacity:opBase,blending:THREE.AdditiveBlending,depthWrite:false});
    const tube=new THREE.Mesh(new THREE.TubeGeometry(curve,40,radius,6,false),tubeMat);arcGroup.add(tube);
    const glowMat=new THREE.MeshBasicMaterial({color:tint.clone().lerp(new THREE.Color(0xffffff),0.45),transparent:true,opacity:0.9,blending:THREE.AdditiveBlending,depthWrite:false});
    const glowBase=(isCluster?0.010:0.008)+0.012*nn;
    const glow=new THREE.Mesh(new THREE.SphereGeometry(1,10,10),glowMat);glow.scale.setScalar(glowBase);arcGroup.add(glow);
    ARCS.push({curve,glow,glowMat,tube,tubeMat,speed:0.22+0.5*nn,phase:i*0.37,glowBase,opBase});
  }
  (function buildArcs(){
    const _note=$('arcnote');
    const conns=(HTDATA&&HTDATA.connections)||[];
    if(conns.length){
      // ── Real cross-language/cross-region net: explicit endpoints from the backend ──
      const _hdr=$('archdr');if(_hdr)_hdr.textContent='VERBINDUNGEN (Regionsübergreifend)';
      const CAP=200;const total=conns.length;
      let list=conns.slice().sort((x,y)=>(y.n||0)-(x.n||0));
      const localCap=list.length>CAP;
      if(localCap){console.info(`Globe: ${list.length} Verbindungen, zeige nur Top ${CAP} (nach n).`);list=list.slice(0,CAP);}
      const validList=list.filter(c=>Number.isFinite(c.a_lat)&&Number.isFinite(c.a_lon)&&Number.isFinite(c.b_lat)&&Number.isFinite(c.b_lon));
      const nanSkipped=list.length-validList.length;
      if(nanSkipped)console.warn(`Globe: ${nanSkipped} Verbindung(en) ohne gültige Koordinaten übersprungen.`);
      const maxN=Math.max(1,...validList.map(c=>c.n||1));
      validList.forEach((c,i)=>{
        const A=ll2v(c.a_lat,c.a_lon,1.012),B=ll2v(c.b_lat,c.b_lon,1.012);
        buildArc(A,B,new THREE.Color(c.color||'#5b8def'),(c.n||1)/maxN,c.kind,i);
      });
      const truncated=(HTDATA&&HTDATA.connections_truncated)||0;
      if(_note){
        const nanNote=nanSkipped?` · ${nanSkipped} ohne Koordinaten verworfen`:'';
        if(truncated&&localCap)_note.textContent=`${validList.length} von ${total} Verbindungen (Cap ${CAP}; ${truncated} serverseitig verworfen${nanNote}).`;
        else if(truncated)_note.textContent=`${validList.length} Verbindungen — ${truncated} serverseitig verworfen${nanNote}.`;
        else if(localCap)_note.textContent=`Top ${CAP} von ${total} Verbindungen dargestellt (nach Häufigkeit${nanNote}).`;
        else _note.textContent=`${validList.length} Verbindungen (Konzept + Cluster über Regionen${nanNote}).`;}
    }else{
      // ── Fallback: thin data → centroid-per-term arcs from trends pairs (legacy v2 behavior) ──
      const _hdr=$('archdr');if(_hdr)_hdr.textContent='TRENDS · KOMBINATIONEN (News-Ko-Vorkommen)';
      const acc={};points.forEach(p=>{if(ageDecay(p.published)<=0)return;const v=ll2v(p.lat,p.lon,1);const a=acc[p.term]||(acc[p.term]=new THREE.Vector3());a.add(v);});
      const cen={};for(const term in acc){const v=acc[term];if(v.lengthSq()>1e-6)cen[term]=v.clone().normalize().multiplyScalar(1.012);}
      let pairs=(HTDATA&&HTDATA.trends)||[];
      const CAP=120;const total=pairs.length;
      pairs=pairs.slice().sort((x,y)=>(y.n||0)-(x.n||0));
      if(pairs.length>CAP){console.info(`Globe: ${pairs.length} Ko-Vorkommen-Paare, zeige nur Top ${CAP} (nach n).`);pairs=pairs.slice(0,CAP);}
      const drawable=pairs.filter(pr=>cen[pr.a]&&cen[pr.b]);
      const maxN=Math.max(1,...drawable.map(pr=>pr.n||1));
      drawable.forEach((pr,i)=>{
        const col=new THREE.Color(pr.ca||'#5b8def').lerp(new THREE.Color(pr.cb||'#5b8def'),0.5);
        buildArc(cen[pr.a],cen[pr.b],col,(pr.n||1)/maxN,'concept',i);
      });
      if(_note){
        const capApplied=pairs.length<total;const geoMissing=drawable.length<pairs.length;
        if(capApplied&&geoMissing)_note.textContent=`${drawable.length}/${total} Paare als Arc (Top ${CAP} nach Häufigkeit, davon ${pairs.length-drawable.length} ohne auflösbaren Geo-Centroid).`;
        else if(capApplied)_note.textContent=`Top ${CAP} von ${total} Paaren dargestellt (nach Häufigkeit).`;
        else if(geoMissing)_note.textContent=`${drawable.length}/${total} Paare als Arc (${total-drawable.length} ohne auflösbaren Geo-Centroid).`;
        else _note.textContent='';}
    }
    arcGroup.visible=ARCS_ON;
  })();
  const ray=new THREE.Raycaster(),mouse=new THREE.Vector2();
  // Watched-Marker bleiben echte Meshes (präzises Picking); die Punktwolke wird über
  // raycaster.params.Points.threshold + .index getroffen → parallele Arrays urls/terms/langs.
  const watchedMeshes=group.children.filter(o=>o.isMesh&&o.userData&&o.userData.url);
  function pickAt(ev){
    const r=renderer.domElement.getBoundingClientRect();
    mouse.x=((ev.clientX-r.left)/r.width)*2-1;mouse.y=-((ev.clientY-r.top)/r.height)*2+1;
    ray.setFromCamera(mouse,camera);
    const wh=ray.intersectObjects(watchedMeshes);
    if(wh.length)return {url:wh[0].object.userData.url,term:wh[0].object.userData.term||'',lang:'de'};
    const ph=ray.intersectObject(cloud);
    if(ph.length){const i=ph[0].index;return {url:urls[i],term:terms[i],lang:langs[i]||'de'};}
    return null;
  }
  renderer.domElement.addEventListener('click',ev=>{const hit=pickAt(ev);if(hit&&hit.url)window.open(hit.url,'_blank');});
  // ── Localized hover tooltip ──
  const gtip=document.getElementById('globetip');
  let _mmLast=0;
  const _mmHandler=ev=>{
    const now=Date.now();if(now-_mmLast<40)return;_mmLast=now;  // ~25fps throttle
    const hit=pickAt(ev);
    if(hit){
      const lang=hit.lang||'de';
      const translations=(HTDATA&&HTDATA.translations&&HTDATA.translations[hit.term])||{};
      const localized=translations[lang];
      let label;
      if(localized&&localized!==hit.term){label='<b>'+esc(localized)+'</b><span style="color:var(--mut);font-size:10px"> (#'+esc(hit.term)+')</span>';}
      else{label='<b>#'+esc(hit.term)+'</b>';}
      const langLabel=lang&&lang!=='de'?'<span style="color:var(--mut);font-size:10px"> · '+esc(lang)+'</span>':'';
      gtip.innerHTML=label+langLabel;
      // Position near cursor but keep within viewport.
      const tw=gtip.offsetWidth||160,th=gtip.offsetHeight||40;
      const vw=window.innerWidth,vh=window.innerHeight;
      let tx=ev.clientX+14,ty=ev.clientY+14;
      if(tx+tw>vw-8)tx=ev.clientX-tw-10;
      if(ty+th>vh-8)ty=ev.clientY-th-10;
      gtip.style.left=tx+'px';gtip.style.top=ty+'px';
      gtip.style.display='block';
      renderer.domElement.style.cursor='pointer';
    }else{
      gtip.style.display='none';
      renderer.domElement.style.cursor='grab';}
  };
  const _mlHandler=()=>{gtip.style.display='none';renderer.domElement.style.cursor='grab';};
  renderer.domElement.addEventListener('mousemove',_mmHandler);
  renderer.domElement.addEventListener('mouseleave',_mlHandler);
  const clock=new THREE.Clock();const H={renderer,arcGroup,arcs:ARCS,cloud,cloudGeo,cloudMat,_mmHandler,_mlHandler,raf:0};
  // Precompute language per point once langgeo is available (einmalig async, füllt langs[]).
  ensureLangGeo().then(lg=>{if(lg&&GLOBE===H)precomputeMarkerLangs(ptLat,ptLon,langs,_N,lg,(HTDATA&&HTDATA.languages)||[]);});let frame=0;
  (function loop(){H.raf=requestAnimationFrame(loop);const t=clock.getElapsedTime();
    if((frame++%120)===0)updateSun();
    // Zoom-relativ: nah dran → Punkte kleiner. EINE Uniform statt per-Punkt-Scale (25k-tauglich).
    const camDist=camera.position.length();const zoomF=Math.max(0.32,Math.min(1.25,(camDist-1.0)/1.7));
    cloudMat.uniforms.uZoom.value=zoomF;
    cloudMat.uniforms.uPx.value=(renderer.domElement.clientHeight||600);
    ray.params.Points.threshold=_avgSize*zoomF*3.0;  // Picking-Trefferradius mit Zoom skalieren
    // Arcs blinken: heller Glow wandert a→b (Geschwindigkeit ~ n), gestaffelte Phase + sanfter Opazitäts-Puls.
    if(arcGroup.visible){ARCS.forEach(o=>{
      const tt=((t*o.speed+o.phase)%1+1)%1;o.glow.position.copy(o.curve.getPoint(tt));
      const pulse=0.5+0.5*Math.sin((t*o.speed+o.phase)*6.283);
      o.glow.scale.setScalar(o.glowBase*(0.7+0.9*pulse));o.glowMat.opacity=0.45+0.55*pulse;
      o.tubeMat.opacity=o.opBase*(0.75+0.45*pulse);
    });}
    controls.update();renderer.render(scene,camera);})();
  GLOBE=H;}
function hashStr(s){let h=0;for(let i=0;i<(s||'').length;i++)h=(h*31+s.charCodeAt(i))|0;return h;}

// ── ORGANIGRAMM: 2D-Flussdiagramm (SVG) + 3D-Graph (three.js) ──
let ORGDATA=null, curOrgMode='flow', ORG3D=null;
async function loadOrg(){ORGDATA=await j('api/org');const s=ORGDATA.stats||{};
  $('orgstats').textContent=`${s.einheiten||0} Einheiten · ${s.einrichtungen||0} Einrichtungen · ${s.personen||0} Personen`;
  setOrgMode(curOrgMode);}
function setOrgMode(m){curOrgMode=m;document.querySelectorAll('.tab[data-om]').forEach(x=>x.classList.toggle('on',x.dataset.om===m));
  $('orgflow').classList.toggle('hide',m!=='flow');$('org3d').classList.toggle('hide',m!=='d3');
  if(!ORGDATA||!ORGDATA.tree[0])return;
  if(m==='flow'){disposeScene(ORG3D);ORG3D=null;renderOrgFlow(ORGDATA.tree[0]);}else renderOrg3D(ORGDATA.tree[0]);}
function layoutTree(root){let leaf=0;const XG=172,YG=96;
  (function w(n,d){const k=n.children||[];if(!k.length)n._x=leaf++;else{k.forEach(c=>w(c,d+1));n._x=(k[0]._x+k[k.length-1]._x)/2;}n._d=d;})(root,0);
  const NODES=[],EDGES=[];
  (function c(n){const px=n._x*XG+80,py=n._d*YG+30;n._px=px;n._py=py;NODES.push({n,x:px,y:py});
    (n.children||[]).forEach(ch=>{c(ch);EDGES.push([px,py,ch._px,ch._py]);});})(root);
  return {NODES,EDGES,w:leaf*XG+160,leaves:leaf};}
function renderOrgFlow(root){if(!root){$('orgflow').className='muted';$('orgflow').textContent='keine Org-Daten';return;}
  const L=layoutTree(root);const H=Math.max(...L.NODES.map(o=>o.y))+60;
  const edges=L.EDGES.map(([x1,y1,x2,y2])=>{const my=(y1+14+y2-14)/2;
    return `<path d="M${x1} ${y1+14} V${my} H${x2} V${y2-14}" stroke="#2a3550" fill="none" stroke-width="1.4"/>`;}).join('');
  const boxes=L.NODES.map(({n,x,y})=>{const col=n.color||'#46527a';const full=(n.short_name||n.name);
    const short=full.length>20?full.slice(0,19)+'…':full;const lbl=esc((n.icon?n.icon+' ':'')+short);
    const w=Math.min(160,Math.max(60,lbl.length*7+16));const bg=n.level===0?col:'#0f1622';const tc=n.level===0?'#fff':'#dbe2ec';
    const ein=n.type==='einrichtung';const clk=ein?` style="cursor:pointer" onclick="openScene3d({id:${n.id},name:'${esc(full).replace(/'/g,'')}',type:'einrichtung'})"`:'';
    return `<g${clk}><title>${esc((n.icon?n.icon+' ':'')+full)}${ein?' — Klick: 3D-Welt':''}</title><rect x="${x-w/2}" y="${y-14}" width="${w}" height="28" rx="7" fill="${bg}" stroke="${ein?'#67d98b':col}" stroke-width="${ein?2.2:1.7}"/>`+
      `<text x="${x}" y="${y+4}" text-anchor=middle fill="${tc}" font-size="11">${lbl}</text></g>`;}).join('');
  $('orgflow').className='flowwrap';
  $('orgflow').innerHTML=`<svg width="${L.w}" height="${H}" viewBox="0 0 ${L.w} ${H}" style="min-width:${L.w}px">${edges}${boxes}</svg>`;}
function renderOrg3D(root){disposeScene(ORG3D);const el=$('org3d');const renderer=makeRenderer(el);
  const scene=new THREE.Scene();const camera=new THREE.PerspectiveCamera(50,el.clientWidth/(el.clientHeight||600),0.1,3000);
  camera.position.set(0,80,440);const controls=new THREE.OrbitControls(camera,renderer.domElement);
  controls.enableDamping=true;controls.autoRotate=true;controls.autoRotateSpeed=0.5;controls.target.set(0,-110,0);
  scene.add(new THREE.AmbientLight(0xffffff,0.85));
  const L=layoutTree(root);const leaves=Math.max(1,L.leaves);const sgeo=new THREE.SphereGeometry(1,16,16);const seg=[];
  const pos=n=>{const a=(n._x/leaves)*Math.PI*2,r=n._d*72;return new THREE.Vector3(r*Math.cos(a),-n._d*82+150,r*Math.sin(a));};
  const nodeMeshes=[];
  // 1:1 zum 2D-Org: JEDER Org-Knoten ist auch hier ein Punkt; Einrichtungen grün +
  // größer + klickbar (öffnet die begehbare 3D-Welt) — wie die grünen Boxen im Flussdiagramm.
  (function place(n){const p=pos(n);const ein=n.type==='einrichtung';
    const r=ein?6:(n._d===0?11:n._d===1?7.5:n._d===2?5:3.4);
    const m=new THREE.Mesh(sgeo,new THREE.MeshBasicMaterial({color:new THREE.Color(ein?'#67d98b':(n.color||'#46527a'))}));
    m.position.copy(p);m.scale.setScalar(r);m.userData={unit:n,ein,base:r,phase:Math.abs(hashStr(n.name||''))%628/100};
    scene.add(m);nodeMeshes.push(m);
    (n.children||[]).forEach(ch=>{const cp=pos(ch);seg.push(p.x,p.y,p.z,cp.x,cp.y,cp.z);place(ch);});})(root);
  const lg=new THREE.BufferGeometry();lg.setAttribute('position',new THREE.Float32BufferAttribute(seg,3));
  scene.add(new THREE.LineSegments(lg,new THREE.LineBasicMaterial({color:0x2a3550,transparent:true,opacity:0.55})));
  // Tooltip + Hover/Click-Raycast
  let tip=$('org3dtip');if(!tip){tip=document.createElement('div');tip.id='org3dtip';document.body.appendChild(tip);}
  const ray=new THREE.Raycaster(),mo=new THREE.Vector2();
  const pick=ev=>{const r=renderer.domElement.getBoundingClientRect();
    mo.x=((ev.clientX-r.left)/r.width)*2-1;mo.y=-((ev.clientY-r.top)/r.height)*2+1;ray.setFromCamera(mo,camera);
    return ray.intersectObjects(nodeMeshes)[0];};
  renderer.domElement.addEventListener('pointermove',ev=>{const h=pick(ev);
    if(h){const n=h.object.userData.unit;const ein=h.object.userData.ein;
      tip.style.display='block';tip.style.left=(ev.clientX+12)+'px';tip.style.top=(ev.clientY+12)+'px';
      tip.innerHTML=`${esc((n.icon?n.icon+' ':'')+n.name)}${ein?' <span style="color:#67d98b">→ 3D-Welt</span>':''}`;
      renderer.domElement.style.cursor=ein?'pointer':'default';}
    else{tip.style.display='none';renderer.domElement.style.cursor='default';}});
  renderer.domElement.addEventListener('pointerleave',()=>{tip.style.display='none';});
  renderer.domElement.addEventListener('click',ev=>{const h=pick(ev);
    if(h&&h.object.userData.ein)openScene3d(h.object.userData.unit);});
  const clock=new THREE.Clock();const H={renderer};
  (function loop(){H.raf=requestAnimationFrame(loop);const t=clock.getElapsedTime();
    nodeMeshes.forEach(m=>{if(m.userData.ein)m.scale.setScalar(m.userData.base*(1+0.12*Math.sin(t*2.5+m.userData.phase)));});
    controls.update();renderer.render(scene,camera);})();ORG3D=H;}

// ── 3D-OSM-BURNER: begehbare Welt der Einrichtung (Overpass-Gebäude, WASD, Stile) ──
let SCENE=null;
function projXY(lat,lon,lat0,lon0){return [(lon-lon0)*111320*Math.cos(lat0*Math.PI/180), -(lat-lat0)*110540];}
async function openScene3d(unit){const ov=$('scene3d');ov.classList.remove('hide');
  $('scname').textContent=unit.name||'Einrichtung';$('scsub').textContent='lädt OSM-Gebäude…';
  $('scload').classList.remove('schide');$('scload').textContent='lädt OSM-Gebäude…';
  let d;try{d=await j('api/org/scene?id='+unit.id);}catch(e){d={error:String(e)};}
  if(!d||d.error){$('scsub').textContent='Fehler: '+((d&&d.error)||'?')+((d&&(d.error||'').includes('geocoded'))?' — erst Geocoding laufen lassen':'');
    $('scload').textContent=(d&&d.error)||'Fehler';return;}
  if(d.name)$('scname').textContent=d.name;
  d.unit_id=unit.id;
  $('scsub').textContent=d.buildings.length+' Gebäude · '+(d.address||(d.center.lat.toFixed(4)+', '+d.center.lon.toFixed(4)));
  $('scload').classList.add('schide');buildScene(d);}
function disposeSceneR(){if(SCENE){cancelAnimationFrame(SCENE.raf);document.removeEventListener('keydown',SCENE.kd);document.removeEventListener('keyup',SCENE.ku);
  if(SCENE.controls&&SCENE.controls.isLocked)SCENE.controls.unlock();
  if(SCENE.renderer){SCENE.renderer.dispose();const d=SCENE.renderer.domElement;if(d&&d.parentNode)d.parentNode.removeChild(d);}SCENE=null;}}
function closeScene(){disposeSceneR();$('scene3d').classList.add('hide');}
function setSceneStyle(n){if(SCENE)SCENE.applyStyle(n);}
function buildScene(d){disposeSceneR();const el=$('sccanvas');const renderer=makeRenderer(el);
  const scene=new THREE.Scene();
  const camera=new THREE.PerspectiveCamera(72,el.clientWidth/el.clientHeight,0.1,5000);camera.position.set(0,1.7,0);
  const controls=new THREE.PointerLockControls(camera,renderer.domElement);scene.add(controls.getObject());
  controls.getObject().position.set(0,1.7,45);
  const ground=new THREE.Mesh(new THREE.PlaneGeometry(6000,6000),new THREE.MeshStandardMaterial({color:0x222a33,roughness:1}));
  ground.rotation.x=-Math.PI/2;scene.add(ground);
  scene.add(new THREE.HemisphereLight(0xbfd4ff,0x202830,0.95));
  const sun=new THREE.DirectionalLight(0xfff3da,1.0);sun.position.set(120,220,80);scene.add(sun);
  const lat0=d.center.lat,lon0=d.center.lon,mats=[];const focalKey=(d.name||'').toLowerCase();
  d.buildings.forEach(b=>{const pts=[];let ok=true;
    b.coords.forEach(c=>{const [x,z]=projXY(c[0],c[1],lat0,lon0);if(!isFinite(x)||!isFinite(z))ok=false;else pts.push([x,z]);});
    if(!ok||pts.length<3)return;
    const shp=new THREE.Shape();pts.forEach((p,i)=>i?shp.lineTo(p[0],p[1]):shp.moveTo(p[0],p[1]));
    const baseY=Math.max(0,b.min_height||0),totalH=Math.max(baseY+2,b.height||9);
    let roofH=b.roof_height||0;const tagged=b.roof_shape&&b.roof_shape!=='flat';
    if(!tagged&&roofH===0&&(totalH-baseY)<15)roofH=Math.min(3,(totalH-baseY)*0.32);  // Default-Walmdach für Häuser
    roofH=Math.min(roofH,(totalH-baseY)*0.6);const wallTop=totalH-roofH;
    let bcol=0x8590a6;if(b.colour){try{bcol=new THREE.Color(b.colour).getHex();}catch(e){}}
    let rcol=0x7a5246;if(b.roof_colour){try{rcol=new THREE.Color(b.roof_colour).getHex();}catch(e){}}
    const grp=new THREE.Group();let wg;
    try{wg=new THREE.ExtrudeGeometry(shp,{depth:wallTop-baseY,bevelEnabled:false});}catch(e){return;}
    wg.rotateX(-Math.PI/2);if(baseY>0)wg.translate(0,baseY,0);grp.add(new THREE.Mesh(wg));
    if(roofH>0.4){let cx=0,cz=0;pts.forEach(p=>{cx+=p[0];cz+=p[1];});cx/=pts.length;cz/=pts.length;
      const po=[];for(let i=0;i<pts.length;i++){const a=pts[i],q=pts[(i+1)%pts.length];po.push(a[0],wallTop,a[1],q[0],wallTop,q[1],cx,wallTop+roofH,cz);}
      const rg=new THREE.BufferGeometry();rg.setAttribute('position',new THREE.Float32BufferAttribute(po,3));rg.computeVertexNormals();
      const rm=new THREE.Mesh(rg);grp.add(rm);grp.userData.roofMesh=rm;}
    grp.userData.focal=!!(b.name&&focalKey&&(b.name.toLowerCase().includes(focalKey)||focalKey.includes(b.name.toLowerCase())));
    grp.userData.bcol=bcol;grp.userData.rcol=rcol;scene.add(grp);mats.push(grp);});
  // Stil-Presets
  function applyStyle(name){document.querySelectorAll('.scbtn').forEach(x=>x.classList.toggle('on',x.dataset.st===name));
    let bg,fog,gc,mk;  // mk(group, isRoof) → Material (Wand vs Dach)
    if(name==='neon'){bg=0x05030f;fog=[0x05030f,60,800];gc=0x0a0618;mk=(g,rf)=>new THREE.MeshBasicMaterial({color:g.userData.focal?(rf?0xffb84d:0xffe24d):(rf?0xff6ec7:0x00ffd0),wireframe:true});}
    else if(name==='toon'){bg=0xcfe7ff;fog=[0xcfe7ff,200,1600];gc=0x7da35a;mk=(g,rf)=>new THREE.MeshToonMaterial({color:g.userData.focal?(rf?0xe07a3a:0xffb347):(rf?0x9c6b52:0xb7c7dd)});}
    else if(name==='blueprint'){bg=0x0a1a3a;fog=[0x0a1a3a,100,1300];gc=0x06122a;mk=(g,rf)=>new THREE.MeshBasicMaterial({color:g.userData.focal?0xffd24d:0x66ccff,wireframe:true});}
    else{bg=0x9fb6d4;fog=[0x9fb6d4,250,1800];gc=0x2a3340;mk=(g,rf)=>new THREE.MeshStandardMaterial({color:g.userData.focal?(rf?0xd49a3a:0xffcc44):(rf?(g.userData.rcol||0x7a5246):(g.userData.bcol||0x8590a6)),roughness:0.9});}
    scene.background=new THREE.Color(bg);scene.fog=new THREE.Fog(fog[0],fog[1],fog[2]);ground.material.color.set(gc);
    mats.forEach(g=>g.children.forEach(ch=>{if(ch.isMesh){if(ch.material&&ch.material.dispose)ch.material.dispose();ch.material=mk(g,ch===g.userData.roofMesh);}}));}
  const keys={},kd=e=>{keys[e.code]=true;if(['1','2','3','4'].includes(e.key))applyStyle(['real','neon','toon','blueprint'][+e.key-1]);},ku=e=>{keys[e.code]=false;};
  document.addEventListener('keydown',kd);document.addEventListener('keyup',ku);
  el.onclick=()=>controls.lock();
  controls.addEventListener('lock',()=>$('sclock').classList.add('schide'));
  controls.addEventListener('unlock',()=>{if(SCENE)$('sclock').classList.remove('schide');});
  // Fokus-Gebäude: benanntes Match, sonst das der Einrichtungs-Koordinate (Zentrum) nächste.
  let focal=mats.find(m=>m.userData.focal);
  if(!focal&&mats.length){focal=mats.reduce((best,m)=>{const c=new THREE.Box3().setFromObject(m).getCenter(new THREE.Vector3());
    const dd=c.x*c.x+c.z*c.z;return dd<best.d?{m,d:dd}:best;},{m:mats[0],d:1e18}).m;}
  if(focal)focal.userData.focal=true;
  const clock=new THREE.Clock();const H={renderer,controls,kd,ku,applyStyle,scene,ground,buildings:mats,focal,unit:d.unit_id||null,name:d.name};
  applyStyle('real');$('sclock').classList.remove('schide');
  (function loop(){H.raf=requestAnimationFrame(loop);const dt=Math.min(0.05,clock.getDelta());
    if(controls.isLocked){const sp=(keys['ShiftLeft']||keys['ShiftRight']?30:12)*dt;const o=controls.getObject();
      if(keys['KeyW'])controls.moveForward(sp);if(keys['KeyS'])controls.moveForward(-sp);
      if(keys['KeyD'])controls.moveRight(sp);if(keys['KeyA'])controls.moveRight(-sp);
      if(keys['Space'])o.position.y+=sp;if(keys['KeyC'])o.position.y=Math.max(1.7,o.position.y-sp);}
    renderer.render(scene,camera);})();
  SCENE=H;}
$('tprev').onclick=()=>{dtIdx--;loadDiscourseTopic();};
$('tnext').onclick=()=>{dtIdx++;loadDiscourseTopic();};
$('htrefresh').onclick=refreshHt;
$('arctog').onclick=()=>{ARCS_ON=!ARCS_ON;$('arctog').textContent='⌇ Arcs: '+(ARCS_ON?'an':'aus');
  if(GLOBE&&GLOBE.arcGroup)GLOBE.arcGroup.visible=ARCS_ON;};
$('langtog').onclick=()=>{LANG_ON=!LANG_ON;$('langtog').textContent='🗣 Sprachen: '+(LANG_ON?'an':'aus');
  if(GLOBE&&GLOBE.langMesh)GLOBE.langMesh.visible=LANG_ON;
  $('langlegend').classList.toggle('hide',!LANG_ON);};
$('lglx').onclick=()=>{const b=$('langlegbody');b.classList.toggle('collapsed');$('lglx').textContent=b.classList.contains('collapsed')?'▸':'▾';};
$('htaddbtn').onclick=addHt;
$('htterm').onkeydown=e=>{if(e.key==='Enter')addHt();};
$('watchaddbtn').onclick=addWatch;
$('watchhandle').onkeydown=e=>{if(e.key==='Enter')addWatch();};
$('watchrefresh').onclick=refreshWatch;
$('scexit').onclick=closeScene;
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&SCENE&&!SCENE.controls.isLocked)closeScene();});
document.querySelectorAll('.tab[data-t]').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab[data-t]').forEach(x=>x.classList.toggle('on',x===t));
  ['dash','graph','timeline','diskurs','globe','quellen','org'].forEach(s=>$(s).classList.toggle('hide',s!==t.dataset.t));
  if(t.dataset.t!=='globe'){disposeScene(GLOBE);GLOBE=null;}
  if(t.dataset.t!=='org'){disposeScene(ORG3D);ORG3D=null;}
  if(t.dataset.t==='graph') loadGraph();
  if(t.dataset.t==='timeline') renderTimeline();
  if(t.dataset.t==='diskurs'){loadDiscourseTopic();}
  if(t.dataset.t==='globe') loadGlobe();
  if(t.dataset.t==='quellen') loadSources();
  if(t.dataset.t==='org') loadOrg();
  if(history.replaceState) history.replaceState(null,'','#'+t.dataset.t);
});
(function(){const h=(location.hash||'').replace('#','');
  if(h.indexOf('scene=')===0){openScene3d({id:parseInt(h.split('=')[1])});return;}
  if(h==='org3d'){document.querySelector('.tab[data-t=org]').click();setOrgMode('d3');return;}
  const t=h&&document.querySelector('.tab[data-t='+h+']');if(t)t.click();})();
$('markseen').onclick=async()=>{await fetch('api/mark_seen',{method:'POST'});refresh();};

async function loadGraph(){renderGraph(await j('api/graph'));}
let QFILTER='all',QDATA=[];
function setQFilter(f){
  QFILTER=f;
  document.querySelectorAll('.qfilter').forEach(t=>t.classList.toggle('on',t.dataset.f===f));
  renderSourcesTable();
}
async function loadSources(){
  const d=await j('api/sources');QDATA=d.sources||[];
  renderSourcesTable();
  loadEventTypes();
  loadNerPending();
}
function renderSourcesTable(){
  const rows=QFILTER==='all'?QDATA:
    QFILTER==='discovered'?QDATA.filter(s=>s.discovered===1):
    QDATA.filter(s=>s.type===QFILTER);
  const smax=Math.max(0.01,...rows.map(s=>s.score));
  const active=QDATA.filter(s=>s.enabled).length;
  $('qstat').textContent=`${QDATA.length} Quellen · ${active} aktiv · Ø-Score ${(QDATA.reduce((a,s)=>a+s.score,0)/(QDATA.length||1)).toFixed(3)}`;
  const TYPC={rss:'#5b8def',archive:'#f0a830'};
  $('qtable').innerHTML=
    '<tr><th class=qrank>#</th><th>Quelle</th><th>Typ</th><th>Reg</th><th>Tier</th><th>Trust</th>'+
    '<th class=num>Artikel</th><th class=num>relevant</th><th class=num>Yield</th>'+
    '<th class=num>30d</th><th>Score</th><th>Status</th><th></th></tr>'+
    rows.map((s,i)=>{
      const w=Math.round(100*s.score/smax);
      const col=s.score>=0.5?'#2ecc71':s.score>=0.25?'#f0a830':'#ff6b5b';
      const inst=s.trust==='institution';
      const tcol=inst?'#2ecc71':'#7a8aa0';
      const typc=TYPC[s.type]||'#7a8aa0';
      return `<tr class="${s.enabled?'':'qoff'}">`+
        `<td class=qrank>${i+1}</td>`+
        `<td>${esc(s.name)}</td>`+
        `<td><span class=qtypebadge style="color:${typc};border-color:${typc}">${esc(s.type||'rss')}</span></td>`+
        `<td>${esc(s.region||'')}</td>`+
        `<td class=num>${s.tier||''}</td>`+
        `<td><span class=trustbadge style="color:${tcol};border-color:${tcol}" title="institution = auto-add-fähig; unverified = manuelle Freigabe">${inst?'⚖ Inst':'· unverif'}</span></td>`+
        `<td class=num>${s.total}</td>`+
        `<td class=num>${s.relevant}</td>`+
        `<td class=num>${(s.ratio*100).toFixed(0)}%</td>`+
        `<td class=num>${s.recent}</td>`+
        `<td><span class=qbartrk><span class=qbar style="width:${w}%;background:${col}"></span></span>${s.score.toFixed(3)}</td>`+
        `<td class=muted>${esc((s.last_status||'').slice(0,22))}</td>`+
        `<td>${s.discovered&&!s.enabled
          ?`<button class=qtog style="color:#2ecc71;border-color:#2ecc71" title="Feed re-verifizieren + aktivieren" onclick="approveSource(${s.id})">✓ approve</button>`
          :`<button class=qtog onclick="toggleSource(${s.id},${!s.enabled})">${s.enabled?'off':'on'}</button>`}</td></tr>`;
    }).join('');
}
async function discoverSources(){
  const btn=$('qdiscbtn');btn.disabled=true;btn.textContent='🔍 scanne…';
  try{
    const r=await(await POST('api/sources/discover',{limit:10})).json();
    btn.textContent=`🔍 ${(r.found||[]).length} gefunden`;
    if((r.found||[]).length)setQFilter('discovered');
    loadSources();
  }finally{setTimeout(()=>{btn.disabled=false;btn.textContent='🔍 Discover';},2500);}
}
async function approveSource(id){
  const r=await(await POST('api/sources/approve',{id})).json();
  if(r.error)alert(r.error);
  loadSources();
}
async function loadEventTypes(){
  const d=await j('api/event_types');
  const ets=d.event_types||[];
  $('etstat').textContent=`${ets.length} Typen · ${ets.filter(e=>e.enabled).length} aktiv · ${(d.topics||[]).length} Themen`;
  $('ettable').innerHTML=
    '<tr><th>Name</th><th>Pattern</th><th>Herkunft</th><th>Status</th></tr>'+
    ets.map(e=>`<tr class="${e.enabled?'':'qoff'}"><td>${esc(e.name)}</td>`+
      `<td class=muted style="max-width:420px;overflow:hidden;text-overflow:ellipsis">${esc(e.pattern)}</td>`+
      `<td>${esc(e.created_by||'')}</td>`+
      `<td>${e.enabled?'aktiv':'<span style=color:#f0a830>quarantäne</span>'}</td></tr>`).join('');
}
async function addEventType(){
  const name=$('etname').value.trim(),pattern=$('etpattern').value.trim();
  if(!name||!pattern)return;
  const r=await(await POST('api/event_types/add',{name,pattern})).json();
  if(r.error)alert(r.error);else{$('etname').value='';$('etpattern').value='';}
  loadEventTypes();
}
async function loadNerPending(){
  const d=await j('api/entities/pending');
  const rows=d.pending||[];
  $('nerstat').textContent=`${rows.length} im Review`;
  $('nertable').innerHTML=rows.length?
    '<tr><th>Entität</th><th>Typ</th><th class=num>Konfidenz</th><th class=num>Meldungen</th><th></th></tr>'+
    rows.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.type)}</td>`+
      `<td class=num>${p.confidence!=null?p.confidence.toFixed(2):''}</td>`+
      `<td class=num>${p.articles}</td>`+
      `<td><button class=qtog style="color:#2ecc71;border-color:#2ecc71" onclick="reviewNer(${p.id},true)">✓</button> `+
      `<button class=qtog style="color:#ff6b5b;border-color:#ff6b5b" onclick="reviewNer(${p.id},false)">✕</button></td></tr>`).join('')
    :'<tr><td class=muted>keine offenen Reviews</td></tr>';
}
async function reviewNer(id,accept){
  await POST('api/entities/review',{id,accept});
  loadNerPending();
}
async function extractNer(){
  const btn=$('nerextract');btn.disabled=true;btn.textContent='⚡ läuft…';
  try{
    const r=await(await POST('api/entities/extract',{since_days:7,limit:50})).json();
    btn.textContent=`⚡ ${r.created||0} neu · ${r.linked||0} verlinkt`;
    loadNerPending();
  }finally{setTimeout(()=>{btn.disabled=false;btn.textContent='⚡ Extrahieren (letzte 7 Tage)';},3000);}
}
async function mirrorHeime(){
  const btn=$('mirrorheime');btn.disabled=true;
  try{
    const r=await(await POST('api/entities/mirror_heime',{})).json();
    btn.textContent=`⌂ ${r.mirrored} gespiegelt`;
  }finally{setTimeout(()=>{btn.disabled=false;btn.textContent='⌂ Heime spiegeln';},2500);}
}
async function addSource(){
  const name=$('qaname').value.trim();
  if(!name)return;
  const url=$('qaurl').value.trim();
  const type=$('qatype').value;
  const tier=parseInt($('qatier').value);
  const region=$('qaregion').value;
  const btn=$('qaaddbtn');btn.disabled=true;
  try{
    const r=await(await POST('api/sources/add',{name,url,type,tier,region})).json();
    if(r.added){$('qaname').value='';$('qaurl').value='';}
    else alert('URL bereits vorhanden oder Name fehlt');
    loadSources();
  }finally{btn.disabled=false;}
}
async function toggleSource(id,enabled){
  await POST('api/sources/toggle',{id,enabled});
  loadSources();
}
async function refresh(){
  const [o,f]=await Promise.all([j('api/overview'),j('api/feed')]);
  renderOverview(o); renderFeed(f);
}
// ?static unterdrückt den Live-Stream (Print/Embed/Headless-Snapshot ohne offene SSE).
const STATIC=location.search.indexOf('static')>=0;
if(!STATIC){
  const es=new EventSource('events');
  es.onopen=()=>$('conn').textContent='live · verbunden';
  es.onerror=()=>$('conn').textContent='reconnect…';
  es.addEventListener('change',()=>{$('conn').textContent='live · update';refresh();
    if(!$('graph').classList.contains('hide'))loadGraph();});
}else{$('conn').textContent='static';}
refresh();
if(!STATIC) setInterval(refresh,30000);
</script></body></html>"""


def main():
    _c = db.connect(DB_PATH); db.bootstrap(_c); hashtags.seed(_c)
    entities.seed_event_types(_c); entities.seed_topics(_c); _c.close()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Pflege-Marktradar Viewer → http://localhost:{PORT}  (DB: {DB_PATH})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
