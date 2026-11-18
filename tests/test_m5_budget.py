import pytest
from decimal import Decimal as D
from app.errors import DomainError
from app.pipeline import run
from app.normalize import instant
from app.modules.m5_budget.rules import day_budget,split_budget,parse_delays
from app.modules.m4_storage.service import level_to_volume
from app.modules.m3_integrate.rules import integrate
C=[(D(100),D(0)),(D(102),D(30000))]
def p(time,level,inflow=1,outflow=0):return dict(station='S',time=instant(time),inflow=D(inflow),outflow=D(outflow),level=D(level))

def test_midnight_split():
    # TRAP-23
    x=integrate([p('2026-12-31T23:00:00+08:00','100'),p('2027-01-01T01:00:00+08:00','102')],D(2))[0];days=day_budget(x,C,level_to_volume);assert len(days)==2 and [r['in_m3'] for r in days]==[3600,3600]

def test_storage_conservation():
    x=integrate([p('2026-11-16T23:00:00+08:00','100'),p('2026-11-17T01:00:00+08:00','102')],D(2))[0];assert sum(r['storage_change'] for r in day_budget(x,C,level_to_volume))==30000

def test_partial_day_flagged():
    x=integrate([p('2026-11-16T00:00:00Z','100'),p('2026-11-16T01:00:00Z','101')],D(2))[0];assert day_budget(x,C,level_to_volume)[0]['state']=='缺测'

def test_empty():
    assert day_budget([],C,level_to_volume)==[]

def test_zulu_midnight_stays_on_local_day():
    # 本地 23:30→次日 00:30，跨午夜来水不能整段记回前一天。
    x=integrate([p('2026-11-16T15:30:00Z','100'),p('2026-11-16T16:30:00Z','100')],D(2))[0]
    days=day_budget(x,C,level_to_volume);assert [r['date'] for r in days]==['2026-11-16','2026-11-17'] and [r['in_m3'] for r in days]==[1800,1800]

def test_voucher_date_and_total_frozen():
    # 传输入库在 22:00 后 2 h 到达次日，凭证日仍记 16 日原量，到达日记 17 日。
    x=integrate([p('2026-11-16T22:00:00+08:00','100'),p('2026-11-17T00:00:00+08:00','101')],D(2))[0]
    split=split_budget(x,C,level_to_volume,{'*':D(2)})
    assert [(r['voucher_date'],r['in_m3']) for r in split['vouchers']]==[('2026-11-16',D(7200))]
    arrived=[(r['date'],r['in_m3']) for r in split['budgets'] if r['in_m3']]
    assert arrived==[('2026-11-17',D(7200))]

def test_arrival_day_shift_counts_once():
    # 传播时延只提前到达日，不复制或丢失任何水量。
    x=integrate([p('2026-11-16T20:00:00+08:00','100'),p('2026-11-17T02:00:00+08:00','102')],D(8))[0]
    split=split_budget(x,C,level_to_volume,{'*':D(4)})
    assert sum(r['in_m3'] for r in split['vouchers'])==sum(r['in_m3'] for r in split['budgets'])==sum(r['in_m3'] for r in x)

def test_crossday_links_keep_both_dates():
    x=integrate([p('2026-11-16T22:00:00+08:00','100'),p('2026-11-17T00:00:00+08:00','101')],D(2))[0]
    split=split_budget(x,C,level_to_volume,{'*':D(3)});link=split['links'][0]
    assert link['voucher_date']=='2026-11-16' and link['arrival_date']=='2026-11-17' and link['shifted']
    assert link['in_m3']==7200

def test_delay_validation():
    assert parse_delays('2')=={'*':D(2)}
    assert parse_delays({'青岚库':'1.5'})=={'青岚库':D('1.5')}
    with pytest.raises(DomainError):parse_delays('-1')
    with pytest.raises(DomainError):parse_delays('169')

def test_real_day_count(raw):
    assert len(run(raw,stop=5,persist=False)['budgets'])>=2

def test_real_missing_day(raw):
    assert any(x['state']=='缺测' for x in run(raw,stop=5,persist=False)['budgets'])

def test_real_daily_integral(raw):
    x=run(raw,stop=5,persist=False);assert abs(sum(D(y['in_m3']) for y in x['budgets'])-sum(D(y['in_m3']) for y in x['flows']))<D('.000000001')

def test_real_voucher_total_unchanged(raw):
    a=run(raw,stop=5,persist=False);b=run(raw,{'travel_hours':'6'},stop=5,persist=False)
    assert [(x['voucher_date'],x['in_m3']) for x in a['vouchers']]==[(x['voucher_date'],x['in_m3']) for x in b['vouchers']]

def test_real_days_show_both_columns(raw):
    x=run(raw,{'travel_hours':'6'},stop=5,persist=False)
    assert x['days'] and all('voucher_in_m3' in d and 'arrival_in_m3' in d for d in x['days'])

def test_real_delay_keeps_water_balance(raw):
    x=run(raw,{'travel_hours':'6'},persist=False)
    flow_total=sum(D(y['in_m3']) for y in x['flows'])
    assert abs(sum(D(y['voucher_in_m3']) for y in x['days'])-flow_total)<D('.000000001')
    assert abs(sum(D(y['arrival_in_m3']) for y in x['days'])-flow_total)<D('.000000001')
    assert abs(D(x['audit']['in_m3'])-D(x['audit']['day_in_m3']))<D('.000000001')

