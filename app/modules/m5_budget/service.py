from .rules import split_budget,parse_delays
from app.modules.m4_storage.service import level_to_volume
def execute(ctx):
    delays=parse_delays(ctx['params'].get('travel_hours'));ctx['travel_hours']={k:str(v) for k,v in delays.items()}
    ctx['travel_label']='默认无传播时延' if not any(delays.values()) else ' · '.join(f"{'全站' if k=='*' else k} {v} h" for k,v in sorted(delays.items()))
    split=split_budget(ctx['flows'],ctx['curve'],level_to_volume,delays)
    ctx.update(split)
    shifted=sum(1 for x in split['links'] if x['shifted'])
    ctx['stages'].append({'title':'日水量收支','rows':split['days'],'metrics':{'日账':len(split['days']),'缺测日':sum(x['state']=='缺测' for x in split['days']),'跨日来水段':shifted,'收支差 m³':str(sum((x['residual'] for x in split['days']),0))}});return ctx
