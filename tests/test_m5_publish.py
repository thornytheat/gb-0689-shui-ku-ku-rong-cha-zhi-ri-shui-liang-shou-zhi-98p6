from decimal import Decimal as D
from app.errors import DomainError
from app.pipeline import run
from app import store

def day(station,date,inflow,outflow='0',storage='0',state='完整'):
    residual=D(storage)-D(inflow)+D(outflow)
    return {'station':station,'date':date,'in_m3':D(inflow),'out_m3':D(outflow),
            'storage_change':D(storage),'residual':residual,'state':state}

def test_publish_writes_arrival_days(isolate):
    result=store.publish([day('青岚库','2026-11-16','100','10','90'),day('青岚库','2026-11-17','200','10','190')],'r1')
    assert result['counts']=={'posted':2,'skipped':0} and result['confirmed_through']=='2026-11-17'
    rows=store.ledger();assert [(r['arrival_date'],r['in_m3']) for r in rows]==[('2026-11-16','100'),('2026-11-17','200')]

def test_publish_reruns_same_day_once(isolate):
    days=[day('青岚库','2026-11-16','100','10','90'),day('青岚库','2026-11-17','200','10','190')]
    store.publish(days,'r1')
    again=store.publish(days,'r2')
    assert again['counts']=={'posted':0,'skipped':2}
    rows=store.ledger();assert len(rows)==2 and [r['in_m3'] for r in rows]==['100','200']

def test_publish_does_not_rewrite_confirmed_day(isolate):
    store.publish([day('青岚库','2026-11-16','100','10','90')],'r1')
    changed=store.publish([day('青岚库','2026-11-16','999','10','90')],'r2')
    assert changed['counts']=={'posted':0,'skipped':1}
    assert store.ledger()[0]['in_m3']=='100'

def test_publish_resumes_after_confirmed_day(isolate):
    store.publish([day('青岚库','2026-11-16','100','10','90')],'r1')
    resumed=store.publish([day('青岚库','2026-11-16','100','10','90'),day('青岚库','2026-11-17','200','10','190')],'r2')
    assert resumed['posted']==[{'station':'青岚库','date':'2026-11-17'}]
    assert [(r['arrival_date'],r['in_m3'],r['run_id']) for r in store.ledger()]==[('2026-11-16','100','r1'),('2026-11-17','200','r2')]

def test_publish_keeps_old_ledger_on_failure(isolate):
    store.publish([day('青岚库','2026-11-16','100','10','90')],'r1')
    with __import__('pytest').raises(DomainError):
        store.publish([day('青岚库','2026-11-17','200','10','190'),{'station':'青岚库','date':'2026-11-18','in_m3':'oops','out_m3':'0','storage_change':'0','residual':'0','state':'完整'}],'r2')
    assert [(r['arrival_date'],r['run_id']) for r in store.ledger()]==[('2026-11-16','r1')]

def test_publish_rejects_invalid_days(isolate):
    for bad in ([], [{'station':'','date':'2026-11-16'}], [day('青岚库','2026-13-01','1')],
                [day('青岚库','2026-11-16','-1')], [day('青岚库','2026-11-16','1',state='未知')],
                [day('青岚库','2026-11-16','1'),day('青岚库','2026-11-16','2')]):
        try:store.publish(bad,'r1')
        except DomainError:pass
        else:raise AssertionError(bad)
    assert store.ledger()==[]

def test_publish_skips_days_before_confirmed_watermark(isolate):
    store.publish([day('青岚库','2026-11-17','200','10','190')],'r1')
    late=store.publish([day('青岚库','2026-11-16','100','10','90'),day('青岚库','2026-11-18','300','10','290')],'r2')
    assert late['skipped']==[{'station':'青岚库','date':'2026-11-16','reason':'已在确认到达日内'}]
    assert late['posted']==[{'station':'青岚库','date':'2026-11-18'}]
    assert [(r['arrival_date'],r['in_m3']) for r in store.ledger()]==[('2026-11-17','200'),('2026-11-18','300')]

def test_publish_recomputes_residual(isolate):
    store.publish([{'station':'青岚库','date':'2026-11-16','in_m3':'100','out_m3':'10','storage_change':'85','residual':'9999','state':'完整'}],'r1')
    assert store.ledger()[0]['residual']=='-5'

def test_publish_missing_day_retry_until_confirmed(isolate):
    store.publish([day('青岚库','2026-11-16','100','10','90',state='缺测')],'r1')
    assert store.ledger()[0]['confirmed']==0
    filled=store.publish([day('青岚库','2026-11-16','100','10','90')],'r2')
    assert filled['counts']=={'posted':1,'skipped':0} and store.ledger()[0]['confirmed']==1

def test_publish_api_uses_stored_run(client):
    r=client.post('/api/runs',json={}).json
    reply=client.post('/api/ledger/publish',json={'run_id':r['manifest']['run_id']})
    assert reply.status_code==200 and reply.json['counts']['posted']==len(r['days'])
    # 样例日账均为缺测日：可重跑补齐，但同一天只保留一行，不会重复入账。
    again=client.post('/api/ledger/publish',json={'run_id':r['manifest']['run_id']})
    assert again.status_code==200 and len(store.ledger())==len(r['days'])
    assert sum(int(x['confirmed']) for x in store.ledger())==0

def test_publish_api_needs_run(client):
    assert client.post('/api/ledger/publish',json={}).status_code==422
    assert client.post('/api/ledger/publish',json={'run_id':'missing'}).status_code==404
