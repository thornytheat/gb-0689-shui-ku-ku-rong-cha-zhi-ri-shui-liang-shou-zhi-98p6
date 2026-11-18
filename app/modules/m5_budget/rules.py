from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from collections import defaultdict
from app.errors import DomainError
from app.normalize import number

ZONE=timezone(timedelta(hours=8))
MAX_TRAVEL_HOURS=D(168)

def _span(hours):
    """传播时长转 timedelta；用整数微秒，避免浮点截断十进制值。"""
    return timedelta(microseconds=int(D(hours)*3600000000))

def _seconds(delta):
    """timedelta 转精确 Decimal 秒，不经过浮点。"""
    return D(delta.days*86400+delta.seconds)+D(delta.microseconds)/D(1000000)

def _midnights(start,end):
    """本地（+08:00）日界，严格落在 (start,end) 内。"""
    day=start.astimezone(ZONE).date();points=[]
    while True:
        day=day+timedelta(days=1)
        point=datetime.combine(day,datetime.min.time(),ZONE)
        if point>=end:break
        if point>start:points.append(point)
    return points

def parse_delays(value):
    """传播时长可为标量（全站）或 {测站: 小时}；'*' 为全站缺省。"""
    if value is None or value=="":return {}
    items=value.items() if isinstance(value,dict) else [("*",value)]
    delays={}
    for station,hours in items:
        if not isinstance(station,str) or not station.strip():raise DomainError('RANGE','传播时长测站键无效')
        try:hours=number(hours)
        except DomainError:raise DomainError('RANGE','传播时长必须为数值')
        if hours<0 or hours>MAX_TRAVEL_HOURS:raise DomainError('RANGE','传播时长超出 0–168 h')
        delays[station.strip()]=hours
    return delays

def _delay(part,delays):
    value=delays.get(part['station'],delays.get('*'))
    return value if value is not None else D(0)

def split_budget(flows,curve,interpolate,delays=None):
    """把传播中的水量拆成凭证日口径与到达日口径。

    凭证日：测站按本地日签发的回执口径——日期取观测本地日，入库/出库总量
    与改口径前一致，传播时延不改变已签发回执的日期和总量。
    到达日：库区日账口径——入库/出库按观测时刻加传播时长后的本地日归集，
    跨午夜来水不再落错日。
    库容变化不是传播中的水量，留在观测（凭证）日，使每个到达日都能用
    “库变-入库+出库”核对当日实际水量。
    """
    delays=delays or {}
    arrival=defaultdict(lambda:{'in_m3':D(0),'out_m3':D(0),'coverage_hours':D(0)})
    voucher=defaultdict(lambda:{'in_m3':D(0),'out_m3':D(0),'coverage_hours':D(0)})
    storage=defaultdict(lambda:{'storage_change':D(0),'observed_hours':D(0)})
    links=defaultdict(lambda:{'in_m3':D(0),'out_m3':D(0),'coverage_hours':D(0)})
    for part in flows:
        left,right=part['left'],part['right'];start,end=part['start'],part['end']
        duration=_seconds(end-start)
        if duration<=0:continue
        span=_span(_delay(part,delays))
        def at(t,key):return left[key]+(right[key]-left[key])*_seconds(t-start)/duration
        # 观测日本地午夜与到达日本地午夜（换算回观测时间轴）都是分割点。
        bounds=set(_midnights(start,end))
        bounds.update(midnight-span for midnight in _midnights(start+span,end+span))
        points=[start]+sorted(x for x in bounds if start<x<end)+[end]
        for a,b in zip(points,points[1:]):
            seconds=_seconds(b-a)
            in_m3=(at(a,'inflow')+at(b,'inflow'))/2*seconds
            out_m3=(at(a,'outflow')+at(b,'outflow'))/2*seconds
            vouched=a.astimezone(ZONE).date().isoformat()
            arrived=(a+span).astimezone(ZONE).date().isoformat()
            key=(part['station'],arrived)
            arrival[key]['in_m3']+=in_m3;arrival[key]['out_m3']+=out_m3;arrival[key]['coverage_hours']+=seconds/3600
            vkey=(part['station'],vouched)
            voucher[vkey]['in_m3']+=in_m3;voucher[vkey]['out_m3']+=out_m3;voucher[vkey]['coverage_hours']+=seconds/3600
            storage[vkey]['storage_change']+=interpolate(at(b,'level'),curve)-interpolate(at(a,'level'),curve)
            storage[vkey]['observed_hours']+=seconds/3600
            link=links[(part['station'],vouched,arrived)]
            link['in_m3']+=in_m3;link['out_m3']+=out_m3;link['coverage_hours']+=seconds/3600
    vouchers=[{'station':station,'voucher_date':day,**values,
               'state':'完整' if values['coverage_hours']==24 else '缺测'} for (station,day),values in sorted(voucher.items())]
    budgets=[]
    for (station,day) in sorted(set(arrival)|set(storage)):
        flows_=arrival.get((station,day),{'in_m3':D(0),'out_m3':D(0),'coverage_hours':D(0)})
        kept=storage.get((station,day),{'storage_change':D(0),'observed_hours':D(0)})
        row={'station':station,'date':day,**flows_,**kept}
        row['residual']=kept['storage_change']-flows_['in_m3']+flows_['out_m3']
        row['state']='完整' if flows_['coverage_hours']==24 and kept['observed_hours']==24 else '缺测'
        budgets.append(row)
    link_rows=[{'station':s,'voucher_date':v,'arrival_date':a,**values,'shifted':v!=a}
               for (s,v,a),values in sorted(links.items())]
    # 凭证日与到达日按同一日期并列，便于在日收支页面逐日对照。
    by_voucher={(x['station'],x['voucher_date']):x for x in vouchers}
    by_arrival={(x['station'],x['date']):x for x in budgets}
    keys=sorted(set(by_voucher)|set(by_arrival))
    days=[]
    for station,day in keys:
        v=by_voucher.get((station,day));a=by_arrival.get((station,day))
        days.append({'station':station,'date':day,
                     'voucher_in_m3':v['in_m3'] if v else D(0),'voucher_out_m3':v['out_m3'] if v else D(0),
                     'voucher_state':v['state'] if v else '无回执',
                     'arrival_in_m3':a['in_m3'] if a else D(0),'arrival_out_m3':a['out_m3'] if a else D(0),
                     'storage_change':a['storage_change'] if a else D(0),
                     'residual':a['residual'] if a else D(0),
                     'coverage_hours':a['coverage_hours'] if a else D(0),
                     'state':a['state'] if a else '缺测'})
    return {'budgets':budgets,'vouchers':vouchers,'links':link_rows,'days':days}

def day_budget(flows,curve,interpolate,delays=None):
    """兼容原调用：只返回到达日库区日账。"""
    return split_budget(flows,curve,interpolate,delays)['budgets']
