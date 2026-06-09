"""Lokales Foto-Kuratier-Tool für die Blender-Renders (Schritt 2 der Gebäude-Pipeline).

Läuft auf dem Laptop, liest `out/buildings/` + `pflege.db` direkt von Disk und serviert
die Renders selbst. Drag+Drop-Tabelle je Einrichtung: Sortierung nach Perspektive
(N/O/S/W) und Style/Typ, „gut"-Flag, Reihenfolge per Drag speichern, eigene Fotos per
Drag-Upload ergänzen. Das gewählte Set (`chosen=1`) ist das Deliverable für den Prod-Viewer.

Start:  python -m marktradar.curate   →  http://127.0.0.1:8766
"""
import base64
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from marktradar import building_photos as bp
from marktradar import db
from marktradar.blender_render import _slug

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.abspath(os.getenv("PFLEGE_OUT_ROOT", os.path.join(_REPO, "out", "buildings")))
DB_PATH = os.getenv("PFLEGE_DB", db.DEFAULT_DB)
PORT = int(os.getenv("PFLEGE_CURATE_PORT", "8766"))
HOST = os.getenv("PFLEGE_CURATE_HOST", "127.0.0.1")

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif"}


def _db():
    conn = db.connect(DB_PATH)
    db.bootstrap(conn)
    return conn


def _safe_under_root(rel):
    """Absoluten Pfad unter OUT_ROOT auflösen oder None (Path-Traversal-Schutz)."""
    p = os.path.abspath(os.path.join(OUT_ROOT, rel))
    if p == OUT_ROOT or p.startswith(OUT_ROOT + os.sep):
        return p
    return None


def _safe_name(name):
    base = os.path.basename(name or "")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_.") or "foto.jpg"


def _unit_dir_slug(conn, unit_id):
    r = conn.execute("SELECT name, traeger FROM org_units WHERE id=?", (unit_id,)).fetchone()
    if not r:
        return None
    return _slug(r["traeger"] or "unbekannt"), _slug(r["name"])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json; charset=utf-8", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(PAGE, "text/html; charset=utf-8")
        if u.path.startswith("/img/"):
            return self._serve_img(u.path[len("/img/"):])
        conn = _db()
        try:
            if u.path == "/api/units":
                return self._send({"units": bp.units_with_photos(conn),
                                   "styles": bp.STYLES, "perspectives": bp.PERSPECTIVES})
            if u.path == "/api/photos":
                uid = int(q.get("unit_id", ["0"])[0])
                return self._send({"photos": bp.photos_for_unit(conn, uid)})
            return self._send({"error": "not found"}, code=404)
        finally:
            conn.close()

    def _serve_img(self, rel):
        p = _safe_under_root(rel)
        if not p or not os.path.isfile(p):
            return self._send({"error": "not found"}, code=404)
        with open(p, "rb") as f:
            data = f.read()
        self._send(data, _MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))

    def do_POST(self):
        path = urlparse(self.path).path
        b = self._body()
        conn = _db()
        try:
            if path == "/api/ingest":
                return self._send(bp.ingest_renders(conn, OUT_ROOT))
            if path == "/api/update":
                return self._send(bp.update_photo(
                    conn, int(b["id"]), perspective=b.get("perspective"),
                    style=b.get("style"), chosen=b.get("chosen")))
            if path == "/api/order":
                return self._send(bp.set_order(conn, int(b["unit_id"]), b.get("ids", [])))
            if path == "/api/add_manual":
                return self._send(self._add_manual(conn, b))
            return self._send({"error": "not found"}, code=404)
        except ValueError as e:  # WHY: bad style / fehlende Felder klar zurückmelden, nicht 500
            return self._send({"error": str(e)}, code=400)
        finally:
            conn.close()

    def _add_manual(self, conn, b):
        unit_id = int(b["unit_id"])
        slugs = _unit_dir_slug(conn, unit_id)
        if not slugs:
            return {"error": "unit not found"}
        traeger_slug, unit_slug = slugs
        data_url = b.get("data_b64", "")
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        raw = base64.b64decode(data_url)
        fname = _safe_name(b.get("filename", "foto.jpg"))
        rel = f"{traeger_slug}/{unit_slug}/manual/{fname}"
        dst = _safe_under_root(rel)
        if not dst:
            return {"error": "bad path"}
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(raw)
        return bp.add_manual(conn, unit_id, rel, style=b.get("style", "photo"),
                             perspective=b.get("perspective"))


PAGE = """<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Gebäude-Fotos · Kuratieren</title>
<style>
:root{--bg:#0e1117;--pan:#161b24;--ln:#27303d;--mut:#8a93a3;--acc:#5b8def;--ok:#2ecc71}
*{box-sizing:border-box}body{margin:0;font:13px/1.45 system-ui,sans-serif;background:var(--bg);color:#e6eaf0;display:flex;height:100vh}
#side{width:260px;border-right:1px solid var(--ln);overflow:auto;flex:0 0 auto}
#side h2{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);padding:14px 14px 6px;margin:0}
.unit{padding:9px 14px;border-bottom:1px solid var(--ln);cursor:pointer}
.unit:hover{background:#1b2330}.unit.on{background:#1e2a40;border-left:3px solid var(--acc)}
.unit b{display:block}.unit small{color:var(--mut)}
#main{flex:1;overflow:auto;padding:16px 20px}
.bar{display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.btn{background:#1d2532;border:1px solid var(--ln);color:#dfe6f0;border-radius:7px;padding:7px 12px;cursor:pointer;font-size:12px}
.btn:hover{border-color:var(--acc)}.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}
#drop{border:2px dashed var(--ln);border-radius:9px;padding:14px;text-align:center;color:var(--mut);margin-bottom:14px}
#drop.hot{border-color:var(--ok);color:var(--ok);background:#13351f}
table{width:100%;border-collapse:collapse}
th,td{padding:7px 9px;border-bottom:1px solid var(--ln);text-align:left;vertical-align:middle}
th{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);cursor:pointer;user-select:none}
th.sort:hover{color:#fff}th .ar{opacity:.5;font-size:9px}
tr.row{cursor:grab}tr.row.drag{opacity:.4}tr.row.over td{border-top:2px solid var(--acc)}
tr.row.chosen{background:#13251a}
img.th{height:64px;width:84px;object-fit:cover;border-radius:5px;border:1px solid var(--ln);display:block}
select{background:#0f1622;color:#e6eaf0;border:1px solid var(--ln);border-radius:5px;padding:4px 6px;font-size:12px}
.grip{color:var(--mut);cursor:grab;font-size:16px}
.gut{transform:scale(1.4);accent-color:var(--ok)}
#status{color:var(--mut);font-size:12px;margin-left:auto}
.empty{color:var(--mut);padding:40px;text-align:center}
</style></head><body>
<div id=side><h2>Einrichtungen</h2><div id=units></div></div>
<div id=main>
  <div class=bar>
    <button class=btn id=ingest>⟳ Renders einlesen</button>
    <button class="btn pri" id=saveorder>⇅ Reihenfolge speichern</button>
    <span id=status></span>
  </div>
  <div id=drop>📷 Eigene Fotos hierher ziehen (oder klicken) — werden dieser Einrichtung als „echtes Foto" zugeordnet</div>
  <input type=file id=file accept="image/*" multiple hidden>
  <div id=tablewrap><div class=empty>Links eine Einrichtung wählen.</div></div>
</div>
<script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
let META={styles:[],perspectives:[]},CUR=null,SORT={key:null,dir:1};
const esc=s=>(s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const j=async u=>(await fetch(u)).json();
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json());
function status(t){$('#status').textContent=t;}

async function loadUnits(){const d=await j('/api/units');META.styles=d.styles;META.perspectives=d.perspectives;
  $('#units').innerHTML=d.units.map(u=>`<div class=unit data-id=${u.id}><b>${esc(u.name)}</b>
    <small>${esc(u.traeger||'')} · ${u.n} Fotos · ${u.n_chosen||0} gut</small></div>`).join('')
    ||'<div class=empty style="padding:20px">Noch keine Fotos.<br>„Renders einlesen" klicken.</div>';
  $$('#units .unit').forEach(el=>el.onclick=()=>selectUnit(+el.dataset.id,el));}

async function selectUnit(id,el){CUR=id;SORT={key:null,dir:1};
  $$('#units .unit').forEach(x=>x.classList.toggle('on',x===el));
  const d=await j('/api/photos?unit_id='+id);renderTable(d.photos);}

function renderTable(photos){
  if(!photos.length){$('#tablewrap').innerHTML='<div class=empty>Keine Fotos für diese Einrichtung.</div>';return;}
  const opt=(arr,v)=>arr.map(o=>`<option value="${esc(o)}"${o===v?' selected':''}>${esc(o)}</option>`).join('');
  const head=`<tr>
    <th></th><th>Bild</th>
    <th class=sort data-k=perspective>Perspektive <span class=ar></span></th>
    <th class=sort data-k=style>Style <span class=ar></span></th>
    <th class=sort data-k=chosen>Gut <span class=ar></span></th></tr>`;
  const rows=photos.map(p=>`<tr class="row${p.chosen?' chosen':''}" draggable=true data-id=${p.id}
      data-perspective="${esc(p.perspective||'')}" data-style="${esc(p.style)}" data-chosen=${p.chosen}>
    <td class=grip>⠿</td>
    <td><a href="/img/${esc(p.path)}" target=_blank><img class=th src="/img/${esc(p.path)}" loading=lazy></a></td>
    <td><select data-f=perspective><option value="">–</option>${opt(META.perspectives,p.perspective||'')}</select></td>
    <td><select data-f=style>${opt(META.styles,p.style)}</select></td>
    <td><input type=checkbox class=gut ${p.chosen?'checked':''}></td></tr>`).join('');
  $('#tablewrap').innerHTML=`<table><thead>${head}</thead><tbody id=tb>${rows}</tbody></table>`;
  wire();
}

function wire(){
  $$('th.sort').forEach(th=>th.onclick=()=>sortBy(th.dataset.k,th));
  $$('#tb select').forEach(s=>s.onchange=()=>{const tr=s.closest('tr');
    const f=s.dataset.f;tr.dataset[f]=s.value;
    post('/api/update',{id:+tr.dataset.id,[f]:s.value||null}).then(()=>status('gespeichert'));});
  $$('#tb .gut').forEach(c=>c.onchange=()=>{const tr=c.closest('tr');
    tr.dataset.chosen=c.checked?1:0;tr.classList.toggle('chosen',c.checked);
    post('/api/update',{id:+tr.dataset.id,chosen:c.checked}).then(()=>status('gespeichert'));});
  let drag=null;
  $$('#tb tr.row').forEach(tr=>{
    tr.ondragstart=e=>{drag=tr;tr.classList.add('drag');e.dataTransfer.effectAllowed='move';};
    tr.ondragend=()=>{if(drag)drag.classList.remove('drag');$$('#tb tr').forEach(x=>x.classList.remove('over'));drag=null;};
    tr.ondragover=e=>{e.preventDefault();$$('#tb tr').forEach(x=>x.classList.remove('over'));tr.classList.add('over');};
    tr.ondrop=e=>{e.preventDefault();if(drag&&drag!==tr)tr.parentNode.insertBefore(drag,tr);tr.classList.remove('over');};
  });
}

function sortBy(key,th){SORT.dir=SORT.key===key?-SORT.dir:1;SORT.key=key;
  $$('th .ar').forEach(a=>a.textContent='');th.querySelector('.ar').textContent=SORT.dir>0?'▲':'▼';
  const order=key==='perspective'?META.perspectives:null;
  const rows=$$('#tb tr.row').sort((a,b)=>{
    let x=a.dataset[key],y=b.dataset[key];
    if(order){x=order.indexOf(x);y=order.indexOf(y);if(x<0)x=99;if(y<0)y=99;}
    return (x>y?1:x<y?-1:0)*SORT.dir;});
  const tb=$('#tb');rows.forEach(r=>tb.appendChild(r));}

$('#saveorder').onclick=async()=>{if(!CUR)return;const ids=$$('#tb tr.row').map(r=>+r.dataset.id);
  await post('/api/order',{unit_id:CUR,ids});status('Reihenfolge gespeichert');};
$('#ingest').onclick=async()=>{status('lese Renders…');const r=await post('/api/ingest',{});
  status(`${r.inserted} neu · ${r.units} Einrichtungen`+(r.unmatched&&r.unmatched.length?` · ${r.unmatched.length} ohne Match`:''));
  await loadUnits();};

const drop=$('#drop'),fileIn=$('#file');
drop.onclick=()=>fileIn.click();
fileIn.onchange=()=>uploadFiles([...fileIn.files]);
drop.ondragover=e=>{e.preventDefault();drop.classList.add('hot');};
drop.ondragleave=()=>drop.classList.remove('hot');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('hot');uploadFiles([...e.dataTransfer.files]);};
async function uploadFiles(files){if(!CUR){status('erst eine Einrichtung wählen');return;}
  const imgs=files.filter(f=>f.type.startsWith('image/'));
  for(const f of imgs){const data=await new Promise(res=>{const r=new FileReader();r.onload=()=>res(r.result);r.readAsDataURL(f);});
    await post('/api/add_manual',{unit_id:CUR,filename:f.name,style:'photo',data_b64:data});}
  status(imgs.length+' Foto(s) hinzugefügt');
  const el=$(`#units .unit[data-id="${CUR}"]`);await selectUnit(CUR,el);await loadUnits();}

loadUnits();
</script></body></html>"""


def main():
    print(f"Kuratier-Tool: http://{HOST}:{PORT}  (OUT_ROOT={OUT_ROOT}, DB={DB_PATH})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
