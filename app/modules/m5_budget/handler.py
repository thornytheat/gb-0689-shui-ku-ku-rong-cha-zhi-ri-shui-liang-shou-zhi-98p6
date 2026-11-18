from flask import Blueprint,request,jsonify
from app.pipeline import run
from app.errors import DomainError
from app import store
bp=Blueprint("m5",__name__)
@bp.route("/api/m5",methods=["GET","POST"])
def endpoint():
    payload=request.get_json(silent=True) if request.method=="POST" else {}
    if not isinstance(payload,dict): raise DomainError("BODY","请求必须是对象")
    raw=payload.get("text")
    if raw is not None and not isinstance(raw,str): raise DomainError("TEXT","数据必须为文本")
    return jsonify(run(raw,payload.get("params"),stop=5,persist=False))

@bp.post("/api/ledger/publish")
def publish():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BODY","请求必须是对象")
    run_id=payload.get("run_id")
    if not isinstance(run_id,str) or not run_id.strip(): raise DomainError("RUN","请指定批次")
    result=store.fetch(run_id.strip())
    days=payload.get("days",result.get("days"))
    if not days: raise DomainError("PUBLISH","该批次没有到达日账，请重算后再发布",422)
    return jsonify(store.publish(days,result["manifest"]["run_id"]))

@bp.get("/api/ledger")
def ledger(): return jsonify(store.ledger())
