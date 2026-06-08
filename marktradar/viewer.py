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

from marktradar import db, query

DB_PATH = os.getenv("PFLEGE_DB", db.DEFAULT_DB)
PORT = int(os.getenv("PFLEGE_VIEWER_PORT", "8765"))


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
            else:
                _json(self, {"error": "not found"}, 404)
        finally:
            conn.close()

    def do_POST(self):
        if urlparse(self.path).path == "/api/mark_seen":
            conn = _db()
            try:
                query.set_meta(conn, "last_seen", datetime.now(timezone.utc).isoformat())
                _json(self, {"ok": True})
            finally:
                conn.close()
        else:
            _json(self, {"error": "not found"}, 404)

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
  <div class=panel><div class=ph><span>THEMEN / HASHTAG-RADAR · WER GREIFT WAS AUF</span></div><div id=radar></div></div>
</section>

<section id=timeline class=hide>
  <div class=panel>
    <div class=ph><span>ZEITSTRAHL · MELDUNGEN (Farbe = Event-Typ · Klick = Quelle)</span></div>
    <div class=tlf id=tlfilter></div>
    <div id=tltax class=muted>lädt…</div>
  </div>
</section>

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
  const e=await j('/api/entity?name='+encodeURIComponent(name));
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
  let items=await j('/api/feed?limit=120');
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
  if(!DTOPICS.length){DTOPICS=await j('/api/discourse_topics');}
  if(!DTOPICS.length){$('dtchart').className='muted';$('dtchart').textContent='Positionen werden synthetisiert…';return;}
  dtIdx=((dtIdx%DTOPICS.length)+DTOPICS.length)%DTOPICS.length;
  const topic=DTOPICS[dtIdx];
  $('tname').textContent=topic+'  ('+(dtIdx+1)+'/'+DTOPICS.length+')';
  const d=await j('/api/discourse_topic?topic='+encodeURIComponent(topic));
  $('dtnote').textContent=d.note||'';
  $('dtlegend').innerHTML=(d.legend||[]).map(p=>`<span class=lg><span class=sw style="background:${p.color}"></span>${esc(p.label)} <span style="color:${p.valence==='pro'?'#2ecc71':p.valence==='contra'?'#ff6b6b':'#7a8290'}">(${esc(p.valence)})</span></span>`).join('')||'<span class=muted>Positionen werden synthetisiert…</span>';
  renderValenceChart($('dtchart'), d.items);
}
async function loadRadar(){
  const d=await j('/api/discourse');
  const tmax=Math.max(1,...d.terms.flatMap(t=>t.trend&&t.trend.length?t.trend:[0]));
  $('radar').innerHTML=d.terms.map(t=>{const spark=(t.trend||[]).map(v=>`<span class=sb style="height:${4+20*v/tmax}px"></span>`).join('');
    return `<div class=tr><div class=trh><span class=tt>#${esc(t.term)}</span><span class=tn>${t.count} Meldungen</span><span class=spark>${spark}</span></div><div class=trm>Quellen: ${(t.sources||[]).map(s=>esc(s[0])+' ('+s[1]+')').join(', ')||'—'}</div><div class=tre>Aufgegriffen von: ${(t.entities||[]).map(esc).join(', ')||'—'}</div></div>`;}).join('');
}
$('tprev').onclick=()=>{dtIdx--;loadDiscourseTopic();};
$('tnext').onclick=()=>{dtIdx++;loadDiscourseTopic();};
document.querySelectorAll('.tab[data-t]').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab[data-t]').forEach(x=>x.classList.toggle('on',x===t));
  ['dash','graph','timeline','diskurs'].forEach(s=>$(s).classList.toggle('hide',s!==t.dataset.t));
  if(t.dataset.t==='graph') loadGraph();
  if(t.dataset.t==='timeline') renderTimeline();
  if(t.dataset.t==='diskurs'){loadDiscourseTopic();loadRadar();}
});
$('markseen').onclick=async()=>{await fetch('/api/mark_seen',{method:'POST'});refresh();};

async function loadGraph(){renderGraph(await j('/api/graph'));}
async function refresh(){
  const [o,f]=await Promise.all([j('/api/overview'),j('/api/feed')]);
  renderOverview(o); renderFeed(f);
}
const es=new EventSource('/events');
es.onopen=()=>$('conn').textContent='live · verbunden';
es.onerror=()=>$('conn').textContent='reconnect…';
es.addEventListener('change',()=>{$('conn').textContent='live · update';refresh();
  if(!$('graph').classList.contains('hide'))loadGraph();});
refresh();
setInterval(refresh,30000);
</script></body></html>"""


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print(f"Pflege-Marktradar Viewer → http://localhost:{PORT}  (DB: {DB_PATH})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
