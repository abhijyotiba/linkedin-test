"""LinkedIn Studio: the web app. Pages, forms, run launcher, LinkedIn OAuth, scheduler startup."""
import asyncio
import datetime as dt
import hmac
import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from . import flows, kits as K, linkedin, mailer, render, scheduler
from .db import METRIC_FIELDS, Flow, Kit, Metric, Run, SessionLocal, get_settings, init_db, save_settings, utcnow
from .llm import MODELS

HERE = Path(__file__).parent
SECRET = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
CRON_TOKEN = os.environ.get("CRON_TOKEN", "")
LOOP: asyncio.AbstractEventLoop | None = None  # the server loop; sync routes run in a threadpool, so launches hop onto it
TASKS: dict[int, object] = {}


def launch(slug: str, params: dict | None = None) -> int:
    with SessionLocal() as s:
        run = Run(flow_slug=slug, params_json=json.dumps(params or {}))
        s.add(run)
        s.commit()
        run_id = run.id
    TASKS[run_id] = asyncio.run_coroutine_threadsafe(flows.execute_run(run_id), LOOP)
    TASKS[run_id].add_done_callback(lambda f: TASKS.pop(run_id, None))
    return run_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    global LOOP
    LOOP = asyncio.get_running_loop()
    init_db()
    task = asyncio.create_task(scheduler.loop(launch)) if os.environ.get("SCHEDULER", "1") == "1" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="LinkedIn Studio", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["tojson_pretty"] = lambda v: json.dumps(v, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------ auth ----
def _sig() -> str:
    return hmac.new(SECRET.encode(), PASSWORD.encode(), hashlib.sha256).hexdigest()


def authed(request: Request) -> bool:
    return not PASSWORD or hmac.compare_digest(request.cookies.get("session", ""), _sig())


def require_auth(request: Request):
    if not authed(request):
        raise HTTPException(status_code=307, headers={"Location": "/login"})


def page(request: Request, name: str, **ctx):
    with SessionLocal() as s:
        settings = get_settings(s)
    env = {"anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")), "smtp": mailer.configured(),
           "linkedin_app": bool(os.environ.get("LINKEDIN_CLIENT_ID") and os.environ.get("LINKEDIN_CLIENT_SECRET")),
           "password": bool(PASSWORD), "fake": os.environ.get("FAKE_LLM") == "1"}
    return templates.TemplateResponse(request, name, {"settings": settings, "env": env, "msg": request.query_params.get("msg", ""), **ctx})


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"msg": request.query_params.get("msg", ""), "settings": None, "env": {}})


@app.post("/login")
def login(request: Request, password: str = Form("")):
    if PASSWORD and not hmac.compare_digest(password, PASSWORD):
        return RedirectResponse("/login?msg=Wrong+password", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("session", _sig(), httponly=True, samesite="lax", max_age=30 * 86400, secure=request.url.scheme == "https")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": utcnow().isoformat()}


# ------------------------------------------------------------- dashboard ----
def _local_now(settings) -> dt.datetime:
    return dt.datetime.now(ZoneInfo(settings["profile"].get("timezone") or "UTC"))


def _next_fires(s, settings) -> list[dict]:
    now = _local_now(settings)
    out = []
    for f in s.scalars(select(Flow)):
        sch = f.data.get("schedule") or {}
        if not f.enabled or not sch.get("time"):
            out.append({"flow": f, "next": None})
            continue
        hh, mm = (int(x) for x in sch["time"].split(":"))
        for i in range(8):
            d = now + dt.timedelta(days=i)
            if K.WEEKDAYS[d.weekday()] in (sch.get("days") or []):
                at = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if at > now:
                    out.append({"flow": f, "next": at})
                    break
        else:
            out.append({"flow": f, "next": None})
    return out


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def dashboard(request: Request):
    with SessionLocal() as s:
        settings = get_settings(s)
        kits = list(s.scalars(select(Kit).order_by(Kit.date.desc()).limit(12)))
        runs = list(s.scalars(select(Run).order_by(Run.id.desc()).limit(8)))
        metrics = {m.date: m for m in s.scalars(select(Metric))}
        fires = _next_fires(s, settings)
        week_ago = (_local_now(settings).date() - dt.timedelta(days=7)).isoformat()
        imp7 = sum((m.impressions or 0) for m in metrics.values() if m.date >= week_ago)
        cost30 = sum(r.cost_usd or 0 for r in s.scalars(select(Run)) if r.started_at and r.started_at >= utcnow() - dt.timedelta(days=30))
        chart = [(m.date, m.impressions or 0, kits_type(s, m.date)) for m in sorted(metrics.values(), key=lambda m: m.date)[-20:]]
        li = settings.get("linkedin") or {}
        connected = bool(li.get("access_token")) and (li.get("expires_at") or 0) > dt.datetime.now().timestamp()
        needs_setup = not settings["profile"].get("niche") or not os.environ.get("ANTHROPIC_API_KEY")
        return page(request, "dashboard.html", kits=kits, runs=runs, metrics=metrics, fires=fires, imp7=imp7, cost30=cost30,
                    chart=chart, connected=connected, li=li, needs_setup=needs_setup, now=_local_now(settings))


def kits_type(s, date):
    k = s.get(Kit, date)
    return k.day_type if k else "?"


# ------------------------------------------------------------------ kits ----
@app.get("/kits", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def kits_list(request: Request):
    with SessionLocal() as s:
        kits = list(s.scalars(select(Kit).order_by(Kit.date.desc())))
        metrics = {m.date: m for m in s.scalars(select(Metric))}
        return page(request, "kits.html", kits=kits, metrics=metrics)


@app.get("/kits/{date}", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def kit_view(request: Request, date: str):
    with SessionLocal() as s:
        kit = s.get(Kit, date)
        if not kit:
            raise HTTPException(404)
        settings = get_settings(s)
        data = kit.data
        return page(request, "kit.html", kit=kit, data=data, metric=s.get(Metric, date), fields=METRIC_FIELDS,
                    lint=K.lint(data, settings["profile"].get("banned_phrases") or []), day_types=K.DAY_TYPES, brief=K.brief_html(data))


@app.post("/kits/{date}", dependencies=[Depends(require_auth)])
def kit_save(date: str, hook: str = Form(""), text: str = Form(""), self1: str = Form(""), self2: str = Form(""), day_type: str = Form("story"), raw: str = Form("")):
    with SessionLocal() as s:
        kit = s.get(Kit, date)
        if not kit:
            raise HTTPException(404)
        data = kit.data
        if raw.strip():
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                return RedirectResponse(f"/kits/{date}?msg=Invalid+JSON:+{e.msg}", status_code=303)
        else:
            data["post"] = {"hook": hook.strip(), "text": text.replace("\r\n", "\n").strip()}
            data["self_comments"] = [self1.strip(), self2.strip()]
        data["day_type"] = day_type
        kit.data, kit.day_type = data, day_type
        kit.data = {**data, "lint": K.lint(data, get_settings(s)["profile"].get("banned_phrases") or [])}
        s.commit()
    return RedirectResponse(f"/kits/{date}?msg=Saved", status_code=303)


@app.post("/kits/{date}/status", dependencies=[Depends(require_auth)])
def kit_status(date: str, status: str = Form(...), post_urn: str = Form("")):
    with SessionLocal() as s:
        kit = s.get(Kit, date)
        if not kit or status not in ("draft", "approved", "posted", "skipped"):
            raise HTTPException(400)
        kit.status = status
        if status == "posted":
            kit.post_urn, kit.posted_at = post_urn.strip() or kit.post_urn, kit.posted_at or utcnow()
        s.commit()
    return RedirectResponse(f"/kits/{date}?msg=Status:+{status}", status_code=303)


@app.post("/kits/{date}/metrics", dependencies=[Depends(require_auth)])
async def kit_metrics(request: Request, date: str):
    form = await request.form()
    with SessionLocal() as s:
        m = s.get(Metric, date) or Metric(date=date)
        for f in METRIC_FIELDS:
            v = str(form.get(f, "")).replace(",", "").strip()
            setattr(m, f, int(float(v)) if v else None)
        s.add(m)
        s.commit()
    return RedirectResponse(f"/kits/{date}?msg=Numbers+saved", status_code=303)


@app.post("/kits/{date}/publish", dependencies=[Depends(require_auth)])
def kit_publish(date: str):
    run_id = launch("publish", {"date": date, "trigger": "manual"})
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/kits/{date}/delete", dependencies=[Depends(require_auth)])
def kit_delete(date: str):
    with SessionLocal() as s:
        kit = s.get(Kit, date)
        if kit:
            s.delete(kit)
            s.commit()
    return RedirectResponse("/kits?msg=Deleted", status_code=303)


@app.get("/kits/{date}/carousel.pdf", dependencies=[Depends(require_auth)])
def kit_pdf(date: str):
    with SessionLocal() as s:
        kit = s.get(Kit, date)
        if not kit or not kit.data.get("carousel"):
            raise HTTPException(404, "this kit has no carousel")
        pdf = render.carousel_pdf(kit.data, get_settings(s)["brand"])
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f"inline; filename={date}.pdf"})


@app.get("/kits/{date}/brief", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def kit_brief(date: str):
    with SessionLocal() as s:
        kit = s.get(Kit, date)
        if not kit:
            raise HTTPException(404)
        return HTMLResponse(K.brief_html(kit.data))


# ----------------------------------------------------------------- flows ----
def flatten(d, prefix=""):
    """Scalar leaves of a nested dict/list as (path, value) for the editor form."""
    out = []
    items = d.items() if isinstance(d, dict) else enumerate(d)
    for k, v in items:
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, (dict, list)):
            out += flatten(v, path)
        else:
            out.append((path, v))
    return out


def set_path(d, path: str, value):
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    last = parts[-1]
    key = int(last) if isinstance(cur, list) else last
    old = cur[key]
    if isinstance(old, bool):
        value = value in ("1", "true", "on", "True")
    elif isinstance(old, int):
        value = int(value)
    elif isinstance(old, float):
        value = float(value)
    cur[key] = value


@app.get("/flows", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def flows_list(request: Request):
    with SessionLocal() as s:
        settings = get_settings(s)
        return page(request, "flows.html", fires=_next_fires(s, settings))


@app.get("/flows/{slug}", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def flow_view(request: Request, slug: str):
    with SessionLocal() as s:
        f = s.get(Flow, slug)
        if not f:
            raise HTTPException(404)
        data = f.data
        editable = {k: v for k, v in data.items() if k not in ("schedule", "last_fired", "description")}
        return page(request, "flow.html", flow=f, data=data, fields=flatten(editable), weekdays=K.WEEKDAYS, day_types=K.DAY_TYPES,
                    today=_local_now(get_settings(s)).date().isoformat())


@app.post("/flows/{slug}", dependencies=[Depends(require_auth)])
async def flow_save(request: Request, slug: str):
    form = await request.form()
    with SessionLocal() as s:
        f = s.get(Flow, slug)
        if not f:
            raise HTTPException(404)
        data = f.data
        if str(form.get("raw", "")).strip():
            try:
                new = json.loads(str(form["raw"]))
            except json.JSONDecodeError as e:
                return RedirectResponse(f"/flows/{slug}?msg=Invalid+JSON:+{e.msg}", status_code=303)
            new["schedule"] = data.get("schedule", {})
            data = new
        else:
            for key, value in form.multi_items():
                if key.startswith("f:"):
                    try:
                        set_path(data, key[2:], str(value).replace("\r\n", "\n"))
                    except (KeyError, IndexError, ValueError):
                        pass
        data["schedule"] = {"days": [d for d in K.WEEKDAYS if form.get(f"day_{d}")], "time": str(form.get("time", "")).strip()}
        data["description"] = str(form.get("description", data.get("description", "")))
        f.data, f.name, f.enabled = data, str(form.get("name", f.name)).strip() or f.name, 1 if form.get("enabled") else 0
        f.updated_at = utcnow()
        s.commit()
    return RedirectResponse(f"/flows/{slug}?msg=Saved", status_code=303)


@app.post("/flows/{slug}/run", dependencies=[Depends(require_auth)])
def flow_run(slug: str, date: str = Form(""), day_type: str = Form(""), overwrite: str = Form("")):
    params = {"trigger": "manual"}
    if date:
        params["date"] = date
    if day_type:
        params["day_type"] = day_type
    if overwrite:
        params["overwrite"] = True
    run_id = launch(slug, params)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/flows/{slug}/duplicate", dependencies=[Depends(require_auth)])
def flow_duplicate(slug: str, new_slug: str = Form(...)):
    new_slug = "".join(ch for ch in new_slug.lower().replace(" ", "_") if ch.isalnum() or ch == "_")[:64]
    with SessionLocal() as s:
        src = s.get(Flow, slug)
        if not src or not new_slug or s.get(Flow, new_slug):
            return RedirectResponse(f"/flows/{slug}?msg=Pick+a+new+unique+name", status_code=303)
        data = src.data
        data.pop("last_fired", None)
        f = Flow(slug=new_slug, name=f"{src.name} (copy)", kind=src.kind, enabled=0)
        f.data = data
        s.add(f)
        s.commit()
    return RedirectResponse(f"/flows/{new_slug}?msg=Copied;+it+is+disabled+until+you+enable+it", status_code=303)


@app.post("/flows/{slug}/delete", dependencies=[Depends(require_auth)])
def flow_delete(slug: str):
    with SessionLocal() as s:
        f = s.get(Flow, slug)
        if f and slug not in flows.DEFAULT_FLOWS:
            s.delete(f)
            s.commit()
    return RedirectResponse("/flows?msg=Deleted", status_code=303)


# ------------------------------------------------------------------ runs ----
@app.get("/runs", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def runs_list(request: Request):
    with SessionLocal() as s:
        runs = list(s.scalars(select(Run).order_by(Run.id.desc()).limit(100)))
        return page(request, "runs.html", runs=runs)


@app.get("/runs/{run_id}", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def run_view(request: Request, run_id: int):
    with SessionLocal() as s:
        run = s.get(Run, run_id)
        if not run:
            raise HTTPException(404)
        return page(request, "run.html", run=run, flow=s.get(Flow, run.flow_slug))


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_auth)])
def run_json(run_id: int):
    with SessionLocal() as s:
        run = s.get(Run, run_id)
        if not run:
            raise HTTPException(404)
        return {"id": run.id, "status": run.status, "error": run.error, "kit_date": run.kit_date, "log": run.log,
                "tokens_in": run.tokens_in, "tokens_out": run.tokens_out, "cost_usd": run.cost_usd,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None}


@app.post("/api/run/{slug}")
async def api_run(request: Request, slug: str):
    """External cron entry point (Render Cron Job or any scheduler): Authorization: Bearer $CRON_TOKEN."""
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not CRON_TOKEN or not hmac.compare_digest(token, CRON_TOKEN):
        raise HTTPException(401)
    with SessionLocal() as s:
        if not s.get(Flow, slug):
            raise HTTPException(404)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    return {"run_id": launch(slug, {"trigger": "api", **(body if isinstance(body, dict) else {})})}


# -------------------------------------------------------------- settings ----
LIST_FIELDS = ("proof_points", "stories", "banned_phrases")


@app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def settings_view(request: Request):
    return page(request, "settings.html", models=MODELS, timezones=sorted(available_timezones()), day_types=K.DAY_TYPES, weekdays=K.WEEKDAYS[:5],
                redirect_uri=str(request.url_for("linkedin_callback")))


@app.post("/settings", dependencies=[Depends(require_auth)])
async def settings_save(request: Request):
    form = await request.form()
    g = lambda k, d="": str(form.get(k, d)).replace("\r\n", "\n").strip()
    lines = lambda k: [x.strip() for x in g(k).split("\n") if x.strip()]
    with SessionLocal() as s:
        st = get_settings(s)
        p = st["profile"]
        for k in ("name", "headline", "niche", "audience", "offer", "market", "voice", "timezone"):
            p[k] = g(k, p.get(k, ""))
        for k in LIST_FIELDS:
            p[k] = lines(k)
        p["target_creators"] = [{"name": a.strip(), "topic": b.strip()} for a, _, b in (ln.partition(":") for ln in lines("target_creators")) if a.strip()]
        st["content"]["weekly_mix"] = {d: g(f"mix_{d}", "story") for d in K.WEEKDAYS[:5]}
        st["content"]["require_approval"] = bool(form.get("require_approval"))
        st["content"]["auto_apply_mix"] = bool(form.get("auto_apply_mix"))
        st["llm"] = {"model": g("model", "claude-opus-5-5"), "effort": g("effort", "medium"), "fallbacks": bool(form.get("fallbacks"))}
        st["delivery"]["email_to"] = g("email_to")
        st["brand"] = {k: g(f"brand_{k}", st["brand"].get(k, "")) for k in ("bg", "fg", "accent", "muted", "handle")}
        save_settings(s, st)
    return RedirectResponse("/settings?msg=Saved", status_code=303)


@app.get("/auth/linkedin", dependencies=[Depends(require_auth)])
def linkedin_start(request: Request):
    cid = os.environ.get("LINKEDIN_CLIENT_ID")
    if not cid:
        return RedirectResponse("/settings?msg=Set+LINKEDIN_CLIENT_ID+and+LINKEDIN_CLIENT_SECRET+in+the+environment+first", status_code=303)
    state = secrets.token_urlsafe(16)
    resp = RedirectResponse(linkedin.auth_url(cid, str(request.url_for("linkedin_callback")), state), status_code=303)
    resp.set_cookie("li_state", state, httponly=True, max_age=600, samesite="lax")
    return resp


@app.get("/auth/linkedin/callback", name="linkedin_callback")
async def linkedin_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    if error or state != request.cookies.get("li_state", ""):
        return RedirectResponse(f"/settings?msg=LinkedIn+connection+failed:+{error_description or error or 'state mismatch'}", status_code=303)
    try:
        tok = await asyncio.to_thread(linkedin.exchange, code, os.environ["LINKEDIN_CLIENT_ID"], os.environ["LINKEDIN_CLIENT_SECRET"], str(request.url_for("linkedin_callback")))
    except linkedin.LinkedInError as e:
        return RedirectResponse(f"/settings?msg={str(e)[:120]}", status_code=303)
    with SessionLocal() as s:
        st = get_settings(s)
        st["linkedin"] = tok
        save_settings(s, st)
    return RedirectResponse(f"/settings?msg=LinkedIn+connected+as+{tok['name']}", status_code=303)


@app.post("/settings/linkedin/disconnect", dependencies=[Depends(require_auth)])
def linkedin_disconnect():
    with SessionLocal() as s:
        st = get_settings(s)
        st["linkedin"] = {}
        save_settings(s, st)
    return RedirectResponse("/settings?msg=LinkedIn+disconnected", status_code=303)
