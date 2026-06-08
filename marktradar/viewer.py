"""Live-Viewer für den Pflege-Marktradar — schlanker stdlib-HTTP-Service an pflege.db.

Home = Signal-Dashboard (Δ-Zähler, Feed, Event-/Quellen-Status), Tabs für
Entitäten-Graph und Timeline. SSE (/events) pusht bei DB-Änderung → der Client
lädt neu, sodass man sieht, was sich an den Daten ändert. Ein 'als gesehen'-
Watermark (meta.last_seen) markiert neue Meldungen.

    python -m marktradar.viewer            # → http://localhost:8765
    PFLEGE_VIEWER_PORT=9000 python -m marktradar.viewer
"""
import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from marktradar import db, hashtags, organigram, query

DB_PATH = os.getenv("PFLEGE_DB", db.DEFAULT_DB)
PORT = int(os.getenv("PFLEGE_VIEWER_PORT", "8765"))
HOST = os.getenv("PFLEGE_VIEWER_HOST", "127.0.0.1")  # Container: 0.0.0.0 für nginx


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


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # WHY: stdlib-Default spammt stderr pro Request
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/events":
            return self._sse()
        conn = _db()
        try:
            if u.path == "/api/overview":
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
                traeger = q.get("traeger", ["Bergische Diakonie"])[0]
                _json(self, {
                    "stats": organigram.stats(conn, traeger),
                    "tree": organigram.tree(conn, traeger),
                    "persons": organigram.persons(conn, traeger=traeger),
                })
            elif u.path == "/api/hashtags":
                _json(self, hashtags.map_data(conn))
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
            elif path == "/api/hashtags/refresh":
                b = self._body()
                src = tuple(b["sources"]) if b.get("sources") else ("mastodon", "bluesky", "news")
                _json(self, hashtags.refresh(conn, src, int(b.get("limit", 15)),
                                             only_id=b.get("id")))
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
.row{display:grid;grid-template-columns:74px 96px 1fr;gap:8px;padding:6px 0;border-bottom:1px solid #131c2a;align-items:center}
.row .ti{grid-column:3;color:#e6ebf2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .meta{grid-column:3;font-size:10px;color:var(--mut)}
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
.htadd{display:flex;gap:6px;margin-bottom:10px}
.htin{flex:1;background:#0c121c;border:1px solid var(--ln);color:var(--fg);border-radius:5px;padding:5px 8px;font:inherit;font-size:11px}
input[type=color]{width:30px;height:28px;border:1px solid var(--ln);border-radius:5px;background:#0c121c;padding:1px;cursor:pointer}
.htrow{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #131c2a;font-size:11px}
.htrow .sw{width:12px;height:12px;border-radius:3px;flex:0 0 auto;cursor:pointer}
.htrow .nm{color:#e6ebf2;cursor:pointer}.htrow.off .nm{color:var(--mut);text-decoration:line-through}
.htrow .ct{margin-left:auto;color:var(--mut);font-size:10px}
.htrow .geo{color:#2ecc71;font-size:9px}
.htrow .tog,.htrow .x{cursor:pointer;font-size:13px;padding:0 2px}.htrow .x{color:#ff6b6b}
.htsrc{display:block;padding:6px 0;border-bottom:1px solid #131c2a;font-size:11px}
.htsrc .sm{color:var(--mut);font-size:10px}
.htsrc a{color:#cdd8e0}.htsrc a:hover{color:#fff}
.srcbadge{font-size:8px;border:1px solid;border-radius:3px;padding:1px 4px;margin-right:5px;letter-spacing:1px}
.orgmodes{display:flex;gap:6px;margin:4px 0 14px}
.flowwrap{overflow:auto;padding:6px 0 16px;max-height:74vh}
#org3d{height:620px;border-radius:8px;overflow:hidden;background:radial-gradient(circle at 50% 38%,#10203f,#05080f);cursor:grab}
.flegend{display:flex;gap:14px;margin-top:8px;flex-wrap:wrap;font-size:10px;color:var(--mut)}
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
      <div class=ph><span>HASHTAG-GLOBUS · ECHTE POSTS (Mastodon · Bluesky · News) · Drag = drehen · Klick Punkt = Quelle</span><span class=muted id=globestat></span></div>
      <div id=globecanvas></div>
    </div>
    <div>
      <div class=panel>
        <div class=ph><span>HASHTAGS</span><button class=btn id=htrefresh>↻ Quellen abrufen</button></div>
        <div class=htadd><input id=htterm placeholder="neuer-hashtag" class=htin><input id=htcolor type=color value="#5b8def"><button class=btn id=htaddbtn>+ </button></div>
        <div id=htlist class=muted>lädt…</div>
      </div>
      <div class=panel>
        <div class=ph><span id=htsrc_title>QUELLEN · echte Links</span></div>
        <div id=htsources class=muted>Hashtag oben anklicken…</div>
      </div>
    </div>
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
  </div>
</section>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
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
    return `<div class="row${a.is_new?' fresh':''}"><span class=dt>${esc((a.published||'').slice(0,10))}</span><span class=ev style="color:${c};border-color:${c}">${et}</span>`+
    `<a class=ti href="${esc(a.link||'#')}" target=_blank>${esc((a.title||'').slice(0,90))}</a><span class=meta>${esc(a.source_domain||'')}${ent}</span></div>`;}).join('');
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
function makeRenderer(el){el.innerHTML='';const r=new THREE.WebGLRenderer({antialias:true,alpha:true});
  r.setPixelRatio(Math.min(2,devicePixelRatio));r.setSize(el.clientWidth,el.clientHeight||600);el.appendChild(r.domElement);return r;}
function ll2v(lat,lon,r){const phi=(90-lat)*Math.PI/180,th=(lon+180)*Math.PI/180;
  return new THREE.Vector3(-r*Math.sin(phi)*Math.cos(th),r*Math.cos(phi),r*Math.sin(phi)*Math.sin(th));}
function disposeScene(h){if(h){cancelAnimationFrame(h.raf);if(h.renderer){h.renderer.dispose();const d=h.renderer.domElement;if(d&&d.parentNode)d.parentNode.removeChild(d);}}}

// ── HASHTAG-GLOBUS ──
const SRCC={mastodon:'#6364ff',bluesky:'#1185fe',news:'#f0a830'};
let HTDATA=null, GLOBE=null;
async function loadGlobe(){HTDATA=await j('api/hashtags');renderHtLegend();initGlobe(HTDATA.points);
  $('globestat').textContent=`${HTDATA.points.length} Geo-Punkte · ${HTDATA.total_posts} Posts`;}
function renderHtLegend(){const d=HTDATA;$('htlist').className='';
  $('htlist').innerHTML=d.legend.map(t=>`<div class="htrow${t.active?'':' off'}">`+
    `<span class=sw style="background:${t.color}" onclick="pickColor(${t.id},'${t.color}')" title="Farbe ändern"></span>`+
    `<span class=nm onclick="showHtSources(${t.id},'${esc(t.term).replace(/'/g,'')}')">#${esc(t.term)}</span>`+
    `<span class=geo>${t.geo}📍</span><span class=ct>${t.count}</span>`+
    `<span class=tog onclick="toggleHt(${t.id},${t.active?0:1})" title="aktiv/inaktiv">${t.active?'◉':'○'}</span>`+
    `<span class=x onclick="delHt(${t.id})" title="löschen">✕</span></div>`).join('')||'<span class=muted>keine Hashtags</span>';}
function showHtSources(id,term){$('htsrc_title').textContent='QUELLEN · #'+term;
  const list=(HTDATA.sources[id]||[]);$('htsources').className=list.length?'':'muted';
  $('htsources').innerHTML=list.map(s=>`<a class=htsrc href="${esc(s.url)}" target=_blank>`+
    `<span class=srcbadge style="color:${SRCC[s.source]||'#888'};border-color:${SRCC[s.source]||'#888'}">${esc(s.source)}</span>`+
    `${esc((s.content||'').slice(0,100))}<span class=sm> — ${esc(s.author||'')} · ${esc((s.published||'').slice(0,10))}</span></a>`).join('')
    ||'noch keine Quellen — „↻ Quellen abrufen“ klicken';}
const POST=(u,o)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o||{})});
async function addHt(){const term=$('htterm').value.trim();if(!term)return;
  const b=$('htaddbtn');b.textContent='…';b.disabled=true;
  try{const res=await (await POST('api/hashtag/add',{term,color:$('htcolor').value})).json();
    $('htterm').value='';
    if(res&&res.id) await POST('api/hashtags/refresh',{id:res.id,limit:15});  // sofort Quellen für den neuen Tag
    await loadGlobe();
    if(res&&res.id) showHtSources(res.id,res.term);
  }finally{b.textContent='+';b.disabled=false;}}
async function delHt(id){if(!confirm('Hashtag samt Posts löschen?'))return;await POST('api/hashtag/delete',{id});loadGlobe();}
async function toggleHt(id,active){await POST('api/hashtag/update',{id,active:!!active});loadGlobe();}
function pickColor(id,cur){const c=prompt('Farbe (Hex, z.B. #ff4d4d):',cur);if(c)POST('api/hashtag/update',{id,color:c}).then(loadGlobe);}
async function refreshHt(){const b=$('htrefresh');b.textContent='lädt…';b.disabled=true;
  try{await POST('api/hashtags/refresh',{limit:15});}finally{b.textContent='↻ Quellen abrufen';b.disabled=false;}loadGlobe();}
function initGlobe(points){disposeScene(GLOBE);const el=$('globecanvas');const renderer=makeRenderer(el);
  const scene=new THREE.Scene();
  const camera=new THREE.PerspectiveCamera(45,el.clientWidth/(el.clientHeight||600),0.1,100);
  camera.position.copy(ll2v(32,12,2.7));  // Startblick auf Europa/DE (Cluster)
  const controls=new THREE.OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=0.08;
  controls.enablePan=false;controls.minDistance=1.4;controls.maxDistance=5;controls.autoRotate=false;
  scene.add(new THREE.AmbientLight(0x6982b8,0.32));  // gedämpft → Tag/Nacht-Kontrast sichtbar
  // Sonne: Richtung = subsolarer Punkt aus aktueller UTC-Zeit (Berlin-Tageszeit), live nachgeführt.
  const sun=new THREE.DirectionalLight(0xfff1cc,1.25);scene.add(sun);scene.add(sun.target);
  const sunMesh=new THREE.Mesh(new THREE.SphereGeometry(0.12,24,24),new THREE.MeshBasicMaterial({color:0xffe27a}));scene.add(sunMesh);
  const sunGlow=new THREE.Mesh(new THREE.SphereGeometry(0.12,24,24),new THREE.MeshBasicMaterial({color:0xffd24d,transparent:true,opacity:0.35,blending:THREE.AdditiveBlending,depthWrite:false}));sunMesh.add(sunGlow);sunGlow.scale.setScalar(2.4);
  function subsolar(){const n=new Date();const utcH=n.getUTCHours()+n.getUTCMinutes()/60+n.getUTCSeconds()/3600;
    const lon=15*(12-utcH);  // Längengrad, an dem die Sonne im Zenit steht
    const start=Date.UTC(n.getUTCFullYear(),0,0);const doy=(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate())-start)/864e5;
    const decl=-23.44*Math.cos((2*Math.PI/365)*(doy+10));return ll2v(decl,lon,1).normalize();}
  function updateSun(){const d=subsolar();sun.position.copy(d.clone().multiplyScalar(5));sun.target.position.set(0,0,0);
    sunMesh.position.copy(d.clone().multiplyScalar(3.4));}
  updateSun();
  const mat=new THREE.MeshPhongMaterial({color:0x16294a,emissive:0x081222,shininess:6,transparent:true,opacity:0.97});
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(1,64,64),mat));
  new THREE.TextureLoader().load('https://unpkg.com/three-globe/example/img/earth-dark.jpg',
    t=>{mat.map=t;mat.color.set(0xffffff);mat.emissive.set(0x223355);mat.needsUpdate=true;},undefined,()=>{});
  scene.add(new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.SphereGeometry(1.003,24,16)),
    new THREE.LineBasicMaterial({color:0x2a4a7a,transparent:true,opacity:0.13})));
  const group=new THREE.Group();scene.add(group);
  const buckets={};points.forEach(p=>{const k=p.term+Math.round(p.lat)+','+Math.round(p.lon);buckets[k]=(buckets[k]||0)+1;});
  const pgeo=new THREE.SphereGeometry(1,8,8);
  points.forEach(p=>{const k=p.term+Math.round(p.lat)+','+Math.round(p.lon);const inten=Math.min(1,(buckets[k]||1)/5);
    const col=new THREE.Color(p.color||'#5b8def');const pos=ll2v(p.lat,p.lon,1.012);
    const base=0.009+0.016*inten,phase=(Math.abs(hashStr(p.url))%628)/100;
    const m=new THREE.Mesh(pgeo,new THREE.MeshBasicMaterial({color:col}));m.position.copy(pos);m.scale.setScalar(base);
    m.userData={url:p.url,base,inten,phase};group.add(m);
    const halo=new THREE.Mesh(pgeo,new THREE.MeshBasicMaterial({color:col,transparent:true,opacity:0.22,blending:THREE.AdditiveBlending,depthWrite:false}));
    halo.position.copy(pos);halo.userData={host:m};group.add(halo);});
  const ray=new THREE.Raycaster(),mouse=new THREE.Vector2();
  renderer.domElement.addEventListener('click',ev=>{const r=renderer.domElement.getBoundingClientRect();
    mouse.x=((ev.clientX-r.left)/r.width)*2-1;mouse.y=-((ev.clientY-r.top)/r.height)*2+1;
    ray.setFromCamera(mouse,camera);const hit=ray.intersectObjects(group.children).find(o=>o.object.userData.url);
    if(hit)window.open(hit.object.userData.url,'_blank');});
  const clock=new THREE.Clock();const H={renderer};let frame=0;
  (function loop(){H.raf=requestAnimationFrame(loop);const t=clock.getElapsedTime();
    if((frame++%120)===0)updateSun();  // Sonne ~alle 2s nachführen (Zeit ändert sich langsam)
    group.children.forEach(m=>{if(m.userData.host){const hm=m.userData.host;m.scale.setScalar(hm.scale.x*2.6);
        m.material.opacity=0.16+0.14*Math.sin(t*2.2+hm.userData.phase);}
      else{const u=m.userData;m.scale.setScalar(u.base*(1+0.55*u.inten*Math.sin(t*2.4+u.phase)));}});
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
    return `<g><title>${esc((n.icon?n.icon+' ':'')+full)}</title><rect x="${x-w/2}" y="${y-14}" width="${w}" height="28" rx="7" fill="${bg}" stroke="${col}" stroke-width="1.7"/>`+
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
  (function place(n){const p=pos(n);const r=n._d===0?11:n._d===1?7.5:n._d===2?5:3.4;
    const m=new THREE.Mesh(sgeo,new THREE.MeshBasicMaterial({color:new THREE.Color(n.color||'#46527a')}));
    m.position.copy(p);m.scale.setScalar(r);scene.add(m);
    (n.children||[]).forEach(ch=>{const cp=pos(ch);seg.push(p.x,p.y,p.z,cp.x,cp.y,cp.z);place(ch);});})(root);
  const lg=new THREE.BufferGeometry();lg.setAttribute('position',new THREE.Float32BufferAttribute(seg,3));
  scene.add(new THREE.LineSegments(lg,new THREE.LineBasicMaterial({color:0x2a3550,transparent:true,opacity:0.55})));
  const H={renderer};(function loop(){H.raf=requestAnimationFrame(loop);controls.update();renderer.render(scene,camera);})();ORG3D=H;}
$('tprev').onclick=()=>{dtIdx--;loadDiscourseTopic();};
$('tnext').onclick=()=>{dtIdx++;loadDiscourseTopic();};
$('htrefresh').onclick=refreshHt;
$('htaddbtn').onclick=addHt;
$('htterm').onkeydown=e=>{if(e.key==='Enter')addHt();};
document.querySelectorAll('.tab[data-t]').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab[data-t]').forEach(x=>x.classList.toggle('on',x===t));
  ['dash','graph','timeline','diskurs','globe','org'].forEach(s=>$(s).classList.toggle('hide',s!==t.dataset.t));
  if(t.dataset.t!=='globe'){disposeScene(GLOBE);GLOBE=null;}
  if(t.dataset.t!=='org'){disposeScene(ORG3D);ORG3D=null;}
  if(t.dataset.t==='graph') loadGraph();
  if(t.dataset.t==='timeline') renderTimeline();
  if(t.dataset.t==='diskurs'){loadDiscourseTopic();}
  if(t.dataset.t==='globe') loadGlobe();
  if(t.dataset.t==='org') loadOrg();
  if(history.replaceState) history.replaceState(null,'','#'+t.dataset.t);
});
(function(){const h=(location.hash||'').replace('#','');const t=h&&document.querySelector('.tab[data-t='+h+']');if(t)t.click();})();
$('markseen').onclick=async()=>{await fetch('api/mark_seen',{method:'POST'});refresh();};

async function loadGraph(){renderGraph(await j('api/graph'));}
async function refresh(){
  const [o,f]=await Promise.all([j('api/overview'),j('api/feed')]);
  renderOverview(o); renderFeed(f);
}
const es=new EventSource('events');
es.onopen=()=>$('conn').textContent='live · verbunden';
es.onerror=()=>$('conn').textContent='reconnect…';
es.addEventListener('change',()=>{$('conn').textContent='live · update';refresh();
  if(!$('graph').classList.contains('hide'))loadGraph();});
refresh();
setInterval(refresh,30000);
</script></body></html>"""


def main():
    _c = db.connect(DB_PATH); db.bootstrap(_c); hashtags.seed(_c); _c.close()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Pflege-Marktradar Viewer → http://localhost:{PORT}  (DB: {DB_PATH})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
