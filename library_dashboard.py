#!/usr/bin/env python3
"""Sussex County Library – Little Ones dashboard (ages 0-3).

Run:  python3 library_dashboard.py
Then open  http://<this-computer's-IP>:8080  on your phone (same Wi-Fi).
Needs only Python 3.8+ (no extra packages).
"""
import json, os, socket, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import calendar

BASE = "https://sussexcountylibrary.libcal.com"
PORT = int(os.environ.get("PORT", 8080))
TTL = 1800  # cache each month for 30 minutes

BRANCHES = {
    "Main Library": "125 Morris Turnpike, Newton, NJ 07860",
    "Dennis Branch": "101 Main Street, Newton, NJ 07860",
    "Dorothy Henry Branch": "66 Route 94, Vernon, NJ 07462",
    "Franklin Branch": "103 Main Street, Franklin, NJ 07416",
    "Louise Childs Branch": "21 Sparta Road, Stanhope, NJ 07874",
    "Sussex-Wantage Branch": "69 Route 639, Sussex, NJ 07461",
    "Dot (Mobile Library)": "Countywide – check the event for the stop",
}
KEYS = [
    ("Dorothy Henry Branch", ("dorothy henry", " dh")),
    ("Louise Childs Branch", ("louise childs",)),
    ("Sussex-Wantage Branch", ("wantage",)),
    ("Franklin Branch", ("franklin",)),
    ("Dennis Branch", ("dennis",)),
    ("Dot (Mobile Library)", ("mobile library", "meet dot", "dot (")),
    ("Main Library", ("main library",)),
]
AUD_WORDS = ("babies", "toddlers", "birth-18", "18 months")
KW_WORDS = ("tiny tots", "baby", "babies", "toddler", "lapsit", "lap sit",
            "little ears", "munchkin", "bedtime storytime", "playtime")

cache = {}


def branch_of(ev):
    for field in ("campus", "location", "title"):
        t = " " + str(ev.get(field) or "").lower()
        for name, words in KEYS:
            if any(w in t for w in words):
                return name
    return str(ev.get("campus") or "Unknown branch")


def names(v):
    out = []
    for x in (v if isinstance(v, list) else [v]):
        if isinstance(x, dict):
            x = x.get("name") or x.get("title") or ""
        if x:
            out.append(str(x))
    return out


def reg_of(ev):
    for k in ("registration", "has_registration", "registration_required", "requires_registration"):
        if k in ev:
            v = ev[k]
            return bool(v) and str(v).lower() not in ("0", "false", "no", "none")
    t = str(ev.get("description") or "").lower()  # fall back to the event text
    if "no registration" in t or "drop-in" in t or "drop in" in t:
        return False
    if "registration is required" in t or "registration required" in t or "register" in t:
        return True
    return None  # unknown


def parse_dt(s):
    try:
        return datetime.fromisoformat(str(s)[:19].replace("T", " "))
    except Exception:
        return None


def clock(d):
    if not d:
        return ""
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def normalize(ev):
    s = parse_dt(ev.get("startdt") or ev.get("start"))
    if not s:
        return None
    e = parse_dt(ev.get("enddt") or ev.get("end"))
    aud = names(ev.get("audiences") or ev.get("audience") or [])
    audtxt = " ".join(aud).lower()
    title = str(ev.get("title") or "")
    kids = any(w in audtxt for w in AUD_WORDS) or any(w in title.lower() for w in KW_WORDS)
    b = branch_of(ev)
    return {
        "id": str(ev.get("id") or ev.get("url") or title + s.isoformat()),
        "title": title, "date": s.date().isoformat(),
        "time": clock(s) + (" – " + clock(e) if e else ""),
        "sort": s.isoformat(), "branch": b, "address": BRANCHES.get(b, ""),
        "room": str(ev.get("location") or ""), "url": ev.get("url") or ev.get("permalink") or "",
        "reg": reg_of(ev), "audience": aud, "kids": kids,
        "closed": b == "Dennis Branch",
    }


def get_json(path, params):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (library-dashboard)",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def day_raw(d):
    return get_json("/ajax/calendar/list", {"c": -1, "date": d.isoformat(), "perpage": 100,
                                            "page": 1, "days": 1, "inc": 0})


def fetch_month(ym):
    hit = cache.get(ym)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    y, m = map(int, ym.split("-"))
    days = [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
    events, errors = {}, []

    def one(d):
        try:
            data = day_raw(d)
            res = data.get("results") or data.get("events") or []
            return [x for x in (normalize(r) for r in res) if x and x["date"] == d.isoformat()]
        except Exception as ex:
            errors.append(f"{d}: {type(ex).__name__}: {ex}")
            return []

    with ThreadPoolExecutor(6) as pool:
        for lst in pool.map(one, days):
            for x in lst:
                events[x["id"] + x["date"]] = x
    out = {"events": sorted(events.values(), key=lambda x: x["sort"]),
           "errors": errors[:3], "failed_days": len(errors)}
    if len(errors) < len(days):  # don't cache a total failure
        cache[ym] = (time.time(), out)
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self.send(200, PAGE, "text/html")
        if u.path == "/api/events":
            ym = (q.get("month") or [date.today().strftime("%Y-%m")])[0]
            data = fetch_month(ym)
            return self.send(200, json.dumps(data), "application/json")
        if u.path == "/api/debug":  # shows the raw feed so field names can be checked
            d = date.fromisoformat((q.get("date") or [date.today().isoformat()])[0])
            try:
                raw = day_raw(d)
                if isinstance(raw.get("results"), list):
                    raw["results"] = raw["results"][:2]
                return self.send(200, json.dumps(raw, indent=1), "application/json")
            except Exception as ex:
                return self.send(200, f"error: {ex}", "text/plain")
        self.send(404, "not found", "text/plain")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Library Little Ones">
<title>Little Ones at the Library</title>
<style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#222;--mut:#6b6b66;--acc:#2f6f5e;--line:#e4e0d6;--warn:#b26a00;--ok:#2f7d4f;--sel:#dff0e9}
@media(prefers-color-scheme:dark){:root{--bg:#161715;--card:#222421;--ink:#ecece6;--mut:#a2a39b;--acc:#6fc2a8;--line:#363933;--warn:#e0a24a;--ok:#74c993;--sel:#27403a}}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:16px/1.4 -apple-system,system-ui,sans-serif;padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
header{position:sticky;top:0;background:var(--bg);padding:10px 12px 8px;border-bottom:1px solid var(--line);z-index:5}
h1{font-size:18px;margin:0 0 8px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.nav{justify-content:space-between;margin-bottom:8px}
.nav b{font-size:17px}
button,select{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px 12px}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.seg{display:flex}.seg button{border-radius:0}.seg button:first-child{border-radius:10px 0 0 10px}.seg button:last-child{border-radius:0 10px 10px 0}
main{padding:10px 12px 40px;max-width:560px;margin:0 auto}
#q{flex:1;min-width:0;font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px}
.btn{display:inline-block;margin-top:8px;padding:6px 16px;border-radius:10px;background:var(--acc);color:#fff;text-decoration:none;font-weight:600}
.chip{font-size:13px;padding:3px 10px;border-radius:14px}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px}
.dow{text-align:center;font-size:12px;color:var(--mut)}
.day{min-height:42px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:2px 2px 3px;font-size:13px;display:flex;flex-direction:column;justify-content:space-between;align-items:center;cursor:pointer}
.day.empty{background:none;border:none}.day.sel{background:var(--sel);border-color:var(--acc)}
.day.today span{color:var(--acc);font-weight:700}
.cnt{font-size:10px;line-height:14px;background:var(--acc);color:#fff;border-radius:8px;padding:0 5px;min-width:16px;text-align:center}
.dh{margin:16px 0 6px;font-weight:700}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:8px}
.card h3{margin:0 0 2px;font-size:16px}.mut{color:var(--mut);font-size:14px}
a{color:var(--acc)}
.tag{display:inline-block;font-size:12px;border-radius:8px;padding:1px 8px;border:1px solid currentColor;margin-top:6px}
.t-reg{color:var(--warn)}.t-ok{color:var(--ok)}.t-unk{color:var(--mut)}
.warn{background:var(--card);border:1px solid var(--warn);border-radius:12px;padding:10px 12px;margin-bottom:10px;font-size:14px}
</style></head><body>
<header>
<h1>Little Ones at the Library</h1>
<div class="row nav"><button id="prev">‹</button><b id="mlabel"></b><button id="next">›</button></div>
<div class="row">
<div class="seg"><button id="vm" class="on">Month</button><button id="vl">List</button></div>
<div class="seg"><button id="ak" class="on">Ages 0-3</button><button id="aa">All ages</button></div>
<select id="br"><option value="">All branches</option></select>
<div class="seg"><button id="r0" class="on">Any</button><button id="r1">Drop-in</button><button id="r2">Registration</button></div>
</div>
<div class="row" style="margin-top:8px"><input id="q" type="search" placeholder="Search events: Tiny Tots, music…"><button id="qc" hidden>✕</button></div>
<div class="row" style="margin-top:6px" id="chips"></div>
</header>
<main><div id="msg"></div><div id="cal"></div><div id="list"></div></main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const MON=['January','February','March','April','May','June','July','August','September','October','November','December'];
const iso=d=>d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
let cur=new Date();cur.setDate(1);
let S={view:'month',kids:true,branch:'',regf:'',sel:iso(new Date()),data:[],q:''};
const BR=['Main Library','Dennis Branch','Dorothy Henry Branch','Franklin Branch','Louise Childs Branch','Sussex-Wantage Branch','Dot (Mobile Library)'];
BR.forEach(b=>$('br').insertAdjacentHTML('beforeend','<option>'+esc(b)+'</option>'));
function setOn(a,b,v){$(a).classList.toggle('on',v);$(b).classList.toggle('on',!v)}
const store={};
async function ensure(ym){
  if(store[ym])return;
  try{const r=await fetch('/api/events?month='+ym);store[ym]=await r.json()}
  catch(e){store[ym]={events:[],failed_days:1,errors:[e.message]}}
}
function monthsInView(){const out=[];for(let i=0;i<(S.q?3:1);i++)out.push(iso(new Date(cur.getFullYear(),cur.getMonth()+i,1)).slice(0,7));return out}
async function load(){
  const ms=monthsInView();
  $('msg').innerHTML='<p class="mut">Loading…</p>';$('cal').innerHTML='';$('list').innerHTML='';
  $('mlabel').textContent=MON[cur.getMonth()]+' '+cur.getFullYear()+(S.q?' (+2 months)':'');
  await Promise.all(ms.map(ensure));
  S.data=ms.flatMap(m=>store[m].events);
  const failed=ms.reduce((a,m)=>a+(store[m].failed_days||0),0);
  const err=ms.map(m=>(store[m].errors||[])[0]).find(Boolean);
  $('msg').innerHTML=failed>0?'<div class="warn">Couldn’t load '+failed+' day(s) from the library calendar.'+(err?'<br><span class="mut">'+esc(err)+'</span>':'')+'<br>Try <a href="/api/debug">/api/debug</a> to see the raw feed.</div>':'';
  ms.forEach(m=>{if(store[m].failed_days)delete store[m]});
  render();
}
function filt(){
  const toks=S.q.toLowerCase().split(/\s+/).filter(Boolean),t=iso(new Date());
  return S.data.filter(e=>(!S.kids||e.kids)&&(!S.branch||e.branch===S.branch)&&(S.regf==='req'?e.reg===true:S.regf==='drop'?e.reg!==true:true)&&(!S.q||(e.date>=t&&toks.every(w=>(e.title+' '+e.room).toLowerCase().includes(w)))));
}
function card(e){
  const reg=e.reg===true?'<span class="tag t-reg">Registration required</span>':e.reg===false?'<span class="tag t-ok">Drop-in</span>':'<span class="tag t-unk">Check registration</span>';
  const maps='https://maps.apple.com/?q='+encodeURIComponent(e.branch+' '+e.address);
  const aud=(e.audience||[]).map(x=>x.split('|')[0].trim()).join(' · ');
  return '<div class="card"><h3>'+esc(e.title)+'</h3><div>'+esc(e.time)+'</div>'
   +'<div class="mut">'+esc(e.branch)+(e.room?' · '+esc(e.room):'')+'</div>'+(aud?'<div class="mut">Ages: '+esc(aud)+'</div>':'')
   +'<div class="mut"><a href="'+maps+'">'+esc(e.address)+'</a></div>'
   +(e.closed?'<div class="mut">⚠ Dennis Branch has been closed for renovations—confirm before going.</div>':'')
   +reg+(e.url?(e.reg===true?'<div><a class="btn" href="'+esc(e.url)+'">Register ›</a></div>':' <a class="mut" href="'+esc(e.url)+'">'+(e.reg===false?'Details':'Details / register')+' ›</a>'):'')+'</div>';
}
function dayLabel(s){const d=new Date(s+'T12:00');return d.toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'})}
function render(){
  const ev=filt(),by={};ev.forEach(e=>(by[e.date]=by[e.date]||[]).push(e));
  $('cal').innerHTML='';$('list').innerHTML='';
  if(S.view==='month'&&!S.q){
    const y=cur.getFullYear(),m=cur.getMonth(),first=new Date(y,m,1).getDay(),n=new Date(y,m+1,0).getDate();
    let h='<div class="grid">'+'SMTWTFS'.split('').map(c=>'<div class="dow">'+c+'</div>').join('');
    for(let i=0;i<first;i++)h+='<div class="day empty"></div>';
    for(let d=1;d<=n;d++){
      const k=iso(new Date(y,m,d)),c=(by[k]||[]).length;
      h+='<div class="day'+(k===S.sel?' sel':'')+(k===iso(new Date())?' today':'')+'" data-d="'+k+'"><span>'+d+'</span>'+(c?'<span class="cnt">'+c+'</span>':'<span></span>')+'</div>';
    }
    $('cal').innerHTML=h+'</div>';
    document.querySelectorAll('.day[data-d]').forEach(el=>el.onclick=()=>{S.sel=el.dataset.d;render()});
    const l=by[S.sel]||[];
    $('list').innerHTML='<div class="dh">'+esc(dayLabel(S.sel))+'</div>'+(l.length?l.map(card).join(''):'<p class="mut">Nothing matching this day.</p>');
  }else{
    const ks=Object.keys(by).sort();
    const sum=S.q?'<p class="mut">'+ev.length+' match'+(ev.length===1?'':'es')+' for “'+esc(S.q)+'” · '+MON[cur.getMonth()]+' to '+MON[(cur.getMonth()+2)%12]+'</p>':'';
    $('list').innerHTML=sum+(ks.length?ks.map(k=>'<div class="dh">'+esc(dayLabel(k))+'</div>'+by[k].map(card).join('')).join(''):'<p class="mut">No matching events.</p>');
  }
}
$('prev').onclick=()=>{cur.setMonth(cur.getMonth()-1);load()};
$('next').onclick=()=>{cur.setMonth(cur.getMonth()+1);load()};
$('vm').onclick=()=>{S.view='month';setOn('vm','vl',true);render()};
$('vl').onclick=()=>{S.view='list';setOn('vm','vl',false);render()};
$('ak').onclick=()=>{S.kids=true;setOn('ak','aa',true);render()};
$('aa').onclick=()=>{S.kids=false;setOn('ak','aa',false);render()};
$('br').onchange=e=>{S.branch=e.target.value;render()};
['r0','r1','r2'].forEach((id,i)=>$(id).onclick=()=>{S.regf=['','drop','req'][i];['r0','r1','r2'].forEach((x,j)=>$(x).classList.toggle('on',j===i));render()});
const CH=['Tiny Tots','Music','Storytime','Toddler','Baby'];
CH.forEach(c=>{const b=document.createElement('button');b.className='chip';b.textContent=c;b.onclick=()=>{$('q').value=c;applyQ()};$('chips').appendChild(b)});
let qt;function applyQ(){S.q=$('q').value.trim();$('qc').hidden=!S.q;load()}
$('q').oninput=()=>{clearTimeout(qt);qt=setTimeout(applyQ,300)};
$('qc').onclick=()=>{$('q').value='';applyQ()};
load();
</script></body></html>"""

if __name__ == "__main__":
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "localhost"
    print(f"Dashboard running. On your phone (same Wi-Fi) open: http://{ip}:{PORT}")
    print("If events look empty, open /api/debug to inspect the raw feed. Ctrl+C to stop.")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
