import os,sqlite3,json
from datetime import datetime,timezone
from decimal import Decimal as D, InvalidOperation
from pathlib import Path
from app.errors import DomainError

DAY_FIELDS=('in_m3','out_m3','storage_change')
DAY_STATES=('完整','缺测')

def connection():
    path=Path(os.environ.get("DATA_DIR","data"));path.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path/"runs.sqlite",timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,review TEXT NOT NULL DEFAULT '待复核')")
    db.execute("""CREATE TABLE IF NOT EXISTS ledger (
        station TEXT NOT NULL, arrival_date TEXT NOT NULL,
        in_m3 TEXT NOT NULL, out_m3 TEXT NOT NULL, storage_change TEXT NOT NULL, residual TEXT NOT NULL,
        state TEXT NOT NULL, run_id TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
        published_at TEXT NOT NULL, PRIMARY KEY(station,arrival_date))""")
    return db

def save(result):
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES(?,?)",(result["manifest"]["run_id"],json.dumps(result,ensure_ascii=False)))
    return fetch(result["manifest"]["run_id"])

def fetch(run_id):
    with connection() as db: row=db.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
    if row is None: raise DomainError("NOT_FOUND","批次不存在",404)
    result=json.loads(row["payload"]);result["version"]=row["version"];result["review"]=row["review"]
    return result

def history():
    with connection() as db: rows=db.execute("SELECT id,version,review FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
    return [dict(row) for row in rows]

def review(run_id,version,decision):
    if decision not in ("通过","退回"): raise DomainError("REVIEW","请选择通过或退回")
    if not isinstance(version,int): raise DomainError("STALE","版本必须为整数")
    with connection() as db:
        cur=db.execute("UPDATE runs SET review=?,version=version+1 WHERE id=? AND version=?",(decision,run_id,version))
        if cur.rowcount!=1: raise DomainError("STALE","复核版本已更新",409)
    return fetch(run_id)

def _amount(value,field):
    try: result=D(str(value))
    except (InvalidOperation,ValueError): raise DomainError("PUBLISH",f"日账{field}不是数值")
    if not result.is_finite(): raise DomainError("PUBLISH",f"日账{field}必须有限")
    return result

def prepare_days(days):
    """发布前先整批校验；任一日不合格则整批不写入，旧版日账保持不变。"""
    if not isinstance(days,list) or not days: raise DomainError("PUBLISH","没有可发布的到达日水量")
    prepared=[];seen=set()
    for row in days:
        if not isinstance(row,dict): raise DomainError("PUBLISH","日账行必须是对象")
        station=str(row.get("station") or "").strip();date=str(row.get("date") or "").strip()
        if not station or not date: raise DomainError("PUBLISH","日账缺少测站或到达日")
        try: datetime.strptime(date,"%Y-%m-%d")
        except ValueError: raise DomainError("PUBLISH",f"到达日格式无效：{date}")
        if (station,date) in seen: raise DomainError("PUBLISH",f"到达日重复：{station} {date}")
        seen.add((station,date))
        state=str(row.get("state") or "")
        if state not in DAY_STATES: raise DomainError("PUBLISH",f"到达日 {date} 状态无效")
        values={field:_amount(row.get(field,row.get("arrival_"+field,"")),field) for field in DAY_FIELDS}
        if values["in_m3"]<0 or values["out_m3"]<0: raise DomainError("PUBLISH",f"到达日 {date} 出现负水量")
        # 收支差由三项当日水量重算，残差随入账一起生成，不接受调用方自报。
        values["residual"]=values["storage_change"]-values["in_m3"]+values["out_m3"]
        prepared.append((station,date,values,state))
    return prepared

def _write_day(db,station,date,values,state,run_id,now):
    db.execute("""INSERT INTO ledger(station,arrival_date,in_m3,out_m3,storage_change,residual,state,run_id,confirmed,published_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(station,arrival_date) DO UPDATE SET
                  in_m3=excluded.in_m3,out_m3=excluded.out_m3,storage_change=excluded.storage_change,
                  residual=excluded.residual,state=excluded.state,run_id=excluded.run_id,
                  confirmed=excluded.confirmed,published_at=excluded.published_at""",
               (station,date,str(values["in_m3"]),str(values["out_m3"]),str(values["storage_change"]),
                str(values["residual"]),state,run_id,1 if state=="完整" else 0,now))

def publish(days,run_id):
    """发布到达日库区日账。

    每个测站以已确认（完整且已入账）的最大到达日为续跑水位线：水位线及之前
    的到达日保留旧版值、不重复入账，也不能用重算结果改写其日期和水量；水位
    线之后的日期从已确认到达日续跑。未确认的缺测日可反复重跑补齐，补齐后转
    为已确认并推进水位线。整批在同一事务内写入，任一天失败即回滚，旧版日账
    保持不变。
    """
    prepared=prepare_days(days);now=datetime.now(timezone.utc).isoformat()
    posted=[];skipped=[]
    with connection() as db:
        marks={row["station"]:row["mark"] for row in db.execute(
            "SELECT station,MAX(arrival_date) mark FROM ledger WHERE confirmed=1 GROUP BY station") if row["mark"]}
        for station,date,values,state in prepared:
            mark=marks.get(station)
            if mark is not None and date<=mark:
                skipped.append({"station":station,"date":date,"reason":"已在确认到达日内"});continue
            _write_day(db,station,date,values,state,run_id,now);posted.append({"station":station,"date":date})
        confirmed={row["station"]:row["mark"] for row in db.execute(
            "SELECT station,MAX(arrival_date) mark FROM ledger WHERE confirmed=1 GROUP BY station") if row["mark"]}
    return {"run_id":run_id,"posted":posted,"skipped":skipped,
            "confirmed_through":max(confirmed.values()) if confirmed else None,
            "confirmed":confirmed,"counts":{"posted":len(posted),"skipped":len(skipped)}}

def ledger():
    with connection() as db: rows=db.execute("SELECT * FROM ledger ORDER BY station,arrival_date").fetchall()
    return [dict(row) for row in rows]
