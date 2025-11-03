def _ri(x):
    try:
        return int(round(float(x or 0)))
    except Exception:
        return 0


import os, json, time, datetime
from typing import Dict, Tuple

SETTINGS = "budget_long.json"
STATE = "budget_long_state.json"

DEFAULT_SETTINGS = {"version":1,"tz_offset_hours":0,"cycle_weeks":4,"pairs":{}}
DEFAULT_STATE    = {"tz_offset_hours":0,"week_index":1,"week_start_utc":None,"next_week_start_utc":None,"month_start_utc":None,"month_end_utc":None,"last_week_roll_utc":None,"last_month_roll_utc":None,"pairs":{}}

TABLES = {
    "UP":    {"OCO": 0.40, "L0": 0.40, "L1": 0.20, "L2": 0.00, "L3": 0.00},
    "RANGE": {"OCO": 0.20, "L0": 0.20, "L1": 0.40, "L2": 0.20, "L3": 0.00},
    "DOWN":  {"OCO": 0.20, "L0": 0.00, "L1": 0.20, "L2": 0.40, "L3": 0.20},
}

STABLES = {"USDC","USDT","BUSD","FDUSD"}

def _atomic_write(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _read_json(path: str, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(default))

def load_settings(sd: str) -> dict:
    # prefer root; fallback to legacy data/
    root_path = os.path.join(sd, SETTINGS)
    data_path = os.path.join(sd, "data", os.path.basename(SETTINGS))
    cfg = _read_json(root_path, DEFAULT_SETTINGS)
    if cfg == DEFAULT_SETTINGS:
        legacy = _read_json(data_path, DEFAULT_SETTINGS)
        if legacy != DEFAULT_SETTINGS:
            # migrate legacy to root for visibility in /json
            _atomic_write(root_path, legacy)
            cfg = legacy
    return cfg
def save_settings(sd: str, cfg: dict): _atomic_write(os.path.join(sd, SETTINGS), cfg)
def load_state(sd: str) -> dict:
    # prefer root; fallback to legacy data/
    root_state = os.path.join(sd, STATE)
    data_state = os.path.join(sd, "data", os.path.basename(STATE))
    st = _read_json(root_state, DEFAULT_STATE)
    if st == DEFAULT_STATE:
        legacy = _read_json(data_state, DEFAULT_STATE)
        if legacy != DEFAULT_STATE:
            _atomic_write(root_state, legacy)
            st = legacy
    st["tz_offset_hours"] = int(load_settings(sd).get("tz_offset_hours",0) or 0)
    return st
def save_state(sd: str, st: dict): _atomic_write(os.path.join(sd, STATE), st)

def _localize(ts_utc: float, tz: int) -> float: return ts_utc + tz*3600
def _to_utc(ts_local: float, tz: int) -> float: return ts_local - tz*3600
def _iso(ts_utc: float) -> str: return datetime.datetime.utcfromtimestamp(ts_utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_pair(pair: str):
    p = (pair or "").upper().strip()
    for suf in ("USDC","USDT","BUSD","FDUSD"):
        if p.endswith(suf) and len(p) > len(suf):
            return p[:-len(suf)], suf
    return p, ""

def _core_set(sd: str) -> set:
    try:
        pairs = _read_json(os.path.join(sd, "pairs.json"), [])
        if pairs == []:
            # fallback legacy path
            pairs = _read_json(os.path.join(sd, "data", "pairs.json"), [])
    except Exception:
        pairs = []
    out = set()
    for sym in pairs or []:
        base, suf = _parse_pair(sym)
        if not base or suf not in STABLES: continue
        try:
            # root first
            j = _read_json(os.path.join(sd, f"{sym.upper()}.json"), {})
            if j == {}:
                # fallback legacy data/<pair>.json
                j = _read_json(os.path.join(sd, "data", f"{sym.upper()}.json"), {})
            if (j.get("trade_mode") or "").upper() == "LONG":
                out.add(sym.upper())
        except Exception: pass
    return out

def _weekly_anchor(now_utc: float, tz: int):
    dt_local = datetime.datetime.utcfromtimestamp(_localize(now_utc, tz))
    days_back = (dt_local.weekday() - 6) % 7
    sunday = dt_local - datetime.timedelta(days=days_back)
    sunday_10 = sunday.replace(hour=10, minute=0, second=0, microsecond=0)
    if dt_local < sunday_10: ws_local = sunday_10 - datetime.timedelta(days=7)
    else: ws_local = sunday_10
    next_local = ws_local + datetime.timedelta(days=7)
    return _to_utc(ws_local.timestamp(), tz), _to_utc(next_local.timestamp(), tz)

def _month_anchors(now_utc: float, tz: int):
    dt_local = datetime.datetime.utcfromtimestamp(_localize(now_utc, tz))
    ms_local = dt_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if ms_local.month == 12: nm_local = ms_local.replace(year=ms_local.year+1, month=1)
    else: nm_local = ms_local.replace(month=ms_local.month+1)
    return _to_utc(ms_local.timestamp(), tz), _to_utc(nm_local.timestamp(), tz)

def _ensure_pair_state(ps: dict):
    for k in ("oco","l0"):
        if k not in ps: ps[k] = {"weekly_quota":0.0,"rollover":0.0,"spent":0.0}
        else:
            ps[k].setdefault("weekly_quota",0.0); ps[k].setdefault("rollover",0.0); ps[k].setdefault("spent",0.0)
    if "l_month" not in ps:
        ps["l_month"] = {"L1":{"left":0.0,"spent":0.0},"L2":{"left":0.0,"spent":0.0},"L3":{"left":0.0,"spent":0.0}}

def init_if_needed(sd: str, now_ts: float|None=None):
    now_utc = now_ts or time.time()
    cfg = load_settings(sd); st = load_state(sd); changed=False
    if not st.get("week_start_utc"):
        ws, nxt = _weekly_anchor(now_utc, int(cfg.get("tz_offset_hours",0) or 0))
        st["week_start_utc"], st["next_week_start_utc"] = _iso(ws), _iso(nxt); st["week_index"]=1; st["last_week_roll_utc"]=None; changed=True
    if not st.get("month_start_utc"):
        ms, me = _month_anchors(now_utc, int(cfg.get("tz_offset_hours",0) or 0))
        st["month_start_utc"], st["month_end_utc"] = _iso(ms), _iso(me); st["last_month_roll_utc"]=None; changed=True
    core = _core_set(sd)
    for sym in core:
        st["pairs"].setdefault(sym, {}); _ensure_pair_state(st["pairs"][sym])
    if changed: save_state(sd, st)

def set_timezone(sd: str, tz_hours: int):
    cfg = load_settings(sd); cfg["tz_offset_hours"]=int(tz_hours); save_settings(sd, cfg); init_if_needed(sd)

def _current_M_for_pair(sd: str, sym: str) -> float:
    w = float(((load_settings(sd).get("pairs") or {}).get(sym, {}) or {}).get("weekly", 0.0) or 0.0)
    return max(0.0, 4.0*w)

def set_weekly(sd: str, sym: str, weekly: float): 
    cfg = load_settings(sd); pairs = cfg.setdefault("pairs", {})
    if weekly < 0: weekly = 0.0
    old = float(((pairs.get(sym, {}) or {}).get("weekly", 0.0)) or 0.0)
    pairs[sym] = {"weekly": round(weekly, 2)}; save_settings(sd, cfg); return old, pairs[sym]["weekly"]

def add_weekly(sd: str, sym: str, delta: float):
    cfg = load_settings(sd); pairs = cfg.setdefault("pairs", {})
    old = float(((pairs.get(sym, {}) or {}).get("weekly", 0.0)) or 0.0)
    new = max(0.0, old + delta); pairs[sym] = {"weekly": round(new, 2)}; save_settings(sd, cfg); return old, pairs[sym]["weekly"]

def _table_for_mode(mode: str) -> dict: return TABLES.get((mode or "RANGE").upper(), TABLES["RANGE"])

def weekly_tick(sd: str, market_modes: Dict[str,str]) -> dict:
    init_if_needed(sd)
    st = load_state(sd); cfg = load_settings(sd)
    core = _core_set(sd); out = {}
    now_utc = time.time()
    ws_utc, nxt_utc = _weekly_anchor(now_utc, int(cfg.get("tz_offset_hours",0) or 0))
    st["week_start_utc"]=_iso(ws_utc); st["next_week_start_utc"]=_iso(nxt_utc); st["last_week_roll_utc"]=_iso(now_utc)
    st["week_index"] = ((st.get("week_index") or 1) % int(cfg.get("cycle_weeks",4) or 4)) + 1
    for sym in core:
        mode = (market_modes.get(sym) or "RANGE").upper(); tbl = _table_for_mode(mode)
        ps = st["pairs"].setdefault(sym, {}); _ensure_pair_state(ps)
        M = _current_M_for_pair(sd, sym)
        oco_month = M * tbl["OCO"]; l0_month = M * tbl["L0"]
        week_quota_oco = oco_month / int(cfg.get("cycle_weeks",4) or 4); week_quota_l0 = l0_month / int(cfg.get("cycle_weeks",4) or 4)
        ps["oco"]["weekly_quota"] = _ri(week_quota_oco); ps["l0"]["weekly_quota"]  = _ri(week_quota_l0)
        out[sym] = {"mode":mode,"M":round(M,2),"OCO_week":round(week_quota_oco + (ps['oco']['rollover'] or 0.0),2),"L0_week":round(week_quota_l0 + (ps['l0']['rollover'] or 0.0),2)}
        for k in ("L1","L2","L3"): ps["l_month"].setdefault(k, {"left":0.0,"spent":0.0})
    save_state(sd, st); return out

def month_end_tick(sd: str) -> dict:
    init_if_needed(sd)
    st = load_state(sd); cfg = load_settings(sd)
    now_utc = time.time(); ms_utc, me_utc = _month_anchors(now_utc, int(cfg.get("tz_offset_hours",0) or 0))
    st["month_start_utc"]=_iso(ms_utc); st["month_end_utc"]=_iso(me_utc); st["last_month_roll_utc"]=_iso(now_utc)
    actions = {}
    for sym, ps in (st.get("pairs") or {}).items():
        _ensure_pair_state(ps)
        oco_left = max(0.0, (ps["oco"].get("weekly_quota") or 0.0) + (ps["oco"].get("rollover") or 0.0) - (ps["oco"].get("spent") or 0.0))
        l0_left  = max(0.0, (ps["l0"].get("weekly_quota") or 0.0)  + (ps["l0"].get("rollover")  or 0.0) - (ps["l0"].get("spent")  or 0.0))
        ps["oco"]["rollover"] = 0; ps["l0"]["rollover"] = 0
        actions[sym] = {"market_buy": round(oco_left + l0_left,2), "carry": {}}
        carry = {}
        for k in ("L1","L2","L3"):
            left = float(ps["l_month"].get(k, {}).get("left",0.0) or 0.0); spent=float(ps["l_month"].get(k, {}).get("spent",0.0) or 0.0)
            rem = max(0.0, left - spent); carry[k]=round(rem,2); ps["l_month"][k] = {"left": rem, "spent": 0.0}
        actions[sym]["carry"] = carry
    save_state(sd, st); return actions


def manual_reset(sd: str, market_modes: Dict[str,str]) -> dict:
    """
    Сбрасывает ролловеры/траты и пересчитывает так, будто сейчас
    1-е число и первая неделя. Распределяет L1/L2/L3 на месяц,
    OCO/L0 — недельные квоты.
    """
    init_if_needed(sd)
    st = load_state(sd); cfg = load_settings(sd)
    tz = int(cfg.get("tz_offset_hours", 0) or 0)
    now_utc = time.time()
    ws_utc, nxt_utc = _weekly_anchor(now_utc, tz)
    ms_utc, me_utc  = _month_anchors(now_utc, tz)
    st["week_index"] = 1
    st["week_start_utc"] = _iso(ws_utc)
    st["next_week_start_utc"] = _iso(nxt_utc)
    st["last_week_roll_utc"] = _iso(now_utc)
    st["month_start_utc"] = _iso(ms_utc)
    st["month_end_utc"] = _iso(me_utc)
    st["last_month_roll_utc"] = _iso(now_utc)
    core = _core_set(sd)
    for sym in core:
        mode = (market_modes.get(sym) or "RANGE").upper()
        tbl = _table_for_mode(mode)
        ps = st["pairs"].setdefault(sym, {})
        _ensure_pair_state(ps)
        M = _current_M_for_pair(sd, sym)
        # weekly OCO/L0
        oco_month = M * tbl["OCO"]; l0_month = M * tbl["L0"]
        week_quota_oco = oco_month / int(cfg.get("cycle_weeks",4) or 4)
        week_quota_l0  = l0_month  / int(cfg.get("cycle_weeks",4) or 4)
        ps["oco"]["weekly_quota"] = _ri(week_quota_oco)
        ps["l0"]["weekly_quota"]  = _ri(week_quota_l0)
        ps["oco"]["rollover"] = 0; ps["l0"]["rollover"] = 0
        ps["oco"]["spent"] = 0; ps["l0"]["spent"] = 0
        # monthly L1/L2/L3
        for k in ("L1","L2","L3"):
            alloc = _ri(M * tbl[k])
            ps["l_month"][k] = {"left": int(alloc), "spent": 0}
        ps.setdefault("l_month_meta", {})["alloc_month"] = st["month_start_utc"]
    save_state(sd, st)
    return st
def budget_summary(sd: str):
    cfg = load_settings(sd); st = load_state(sd); core = _core_set(sd)
    lines = []; tot_weekly=0.0
    for sym in sorted(core):
        w = float(((cfg.get("pairs") or {}).get(sym, {}) or {}).get("weekly", 0.0) or 0.0); M = 4.0*w
        ps = (st.get("pairs") or {}).get(sym, {}); _ensure_pair_state(ps)
        oco_avail = (ps["oco"]["weekly_quota"] or 0.0) + (ps["oco"]["rollover"] or 0.0)
        l0_avail  = (ps["l0"]["weekly_quota"]  or 0.0) + (ps["l0"]["rollover"]  or 0.0)
        l1 = ps["l_month"]["L1"]; l2 = ps["l_month"]["L2"]; l3 = ps["l_month"]["L3"]
        lines.append(f"{sym}  W {w:.2f}  M {M:.2f}  OCO_w {oco_avail:.2f}  L0_w {l0_avail:.2f}  L1 {l1['left']:.2f}/{l1['spent']:.2f}  L2 {l2['left']:.2f}/{l2['spent']:.2f}  L3 {l3['left']:.2f}/{l3['spent']:.2f}")
        tot_weekly += w
    header = "Budget Long (core)\nSYMBOL  W weekly  M monthly  OCO_week  L0_week  L1 left/spent  L2 left/spent  L3 left/spent"
    body = "\n".join(lines) if lines else "(no core pairs)"
    footer = f"TOTAL weekly {tot_weekly:.2f}\nWeek: {st.get('week_start_utc','?')} → {st.get('next_week_start_utc','?')}\nMonth: {st.get('month_start_utc','?')} → {st.get('month_end_utc','?')}\nTZ: UTC{cfg.get('tz_offset_hours',0):+d}"
    msg = "```\n" + header + "\n" + body + "\n" + footer + "\n```"; return msg, {"lines": lines}

def budget_per_symbol_texts(sd: str):
    """Возвращает список сообщений по каждой core-LONG паре. Значения — целые."""
    cfg = load_settings(sd); st = load_state(sd); core = _core_set(sd)
    out = []
    for sym in sorted(core):
        w = float(((cfg.get("pairs") or {}).get(sym, {}) or {}).get("weekly", 0.0) or 0.0)
        w_i = _ri(w)
        M_i = _ri(4.0 * w)
        ps = (st.get("pairs") or {}).get(sym, {})
        _ensure_pair_state(ps)
        oco_quota = _ri(ps["oco"].get("weekly_quota"))
        oco_roll  = _ri(ps["oco"].get("rollover"))
        oco_spent = _ri(ps["oco"].get("spent"))
        oco_left  = max(0, oco_quota + oco_roll - oco_spent)

        l0_quota = _ri(ps["l0"].get("weekly_quota"))
        l0_roll  = _ri(ps["l0"].get("rollover"))
        l0_spent = _ri(ps["l0"].get("spent"))
        l0_left  = max(0, l0_quota + l0_roll - l0_spent)

        l1 = ps["l_month"]["L1"]; l2 = ps["l_month"]["L2"]; l3 = ps["l_month"]["L3"]
        line = f"{sym}  W {w_i}  M {M_i}
" \
               f"OCO_w {oco_left}/{oco_spent}  L0_w {l0_left}/{l0_spent}
" \
               f"L1 { _ri(l1['left']) }/{ _ri(l1['spent']) }  L2 { _ri(l2['left']) }/{ _ri(l2['spent']) }  L3 { _ri(l3['left']) }/{ _ri(l3['spent']) }"
        out.append(line)
    return out

def budget_schedule_text(sd: str):
    cfg = load_settings(sd); st = load_state(sd)
    tz = int(cfg.get("tz_offset_hours",0) or 0)
    return "Week: {ws} → {wn}\\nMonth: {ms} → {me}\\nTZ: UTC{tz:+d}".format(
        ws=st.get("week_start_utc","?"), wn=st.get("next_week_start_utc","?"),
        ms=st.get("month_start_utc","?"), me=st.get("month_end_utc","?"),
        tz=tz
    )
