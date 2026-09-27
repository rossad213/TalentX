#!/usr/bin/env python3
"""Apply source-backed Motorsport race results exactly once to live market state."""
from __future__ import annotations
import argparse,csv,json,math,re,unicodedata
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
from results_event_pricing import result_move_from_delta
from motorsport_metric_calibration import is_motorsport

MODEL_VERSION="1.0-motorsport-results"
def norm(v):
    return re.sub(r"[^a-z0-9]+","",unicodedata.normalize("NFKD",str(v or "")).encode("ascii","ignore").decode("ascii").lower())
def num(v,d=0.0):
    try:x=float(v)
    except (TypeError,ValueError):return d
    return x if math.isfinite(x) else d
def event_keys(r):
    return {str(e.get("eventKey") or "") for e in r.get("priceEvents",[]) if isinstance(e,dict)}
def sensitivity(r):
    ev=r.get("motorsportEvidence") if isinstance(r.get("motorsportEvidence"),dict) else {}
    starts=num(ev.get("careerStarts"))
    if starts and starts<45:return 1.08
    if starts>=180:return .88
    if starts>=90:return .94
    return 1.0
def event_delta(r,finish,status,field):
    ev=r.get("motorsportEvidence") if isinstance(r.get("motorsportEvidence"),dict) else {}
    rank=max(1.0,num(ev.get("seasonRank"),num(r.get("sourceRank"),field)))
    expected=max(.08,min(1.0,1.0-(rank-1.0)/max(1.0,field-1.0)))
    if str(status).lower()=="dnf":
        actual=0.0
        delta=(actual-expected)*100.0-12.0
    else:
        pos=max(1.0,num(finish,field))
        actual=max(0.0,min(1.0,1.0-(pos-1.0)/max(1.0,field-1.0)))
        delta=(actual-expected)*100.0
        if pos==1:delta+=18.0
        elif pos<=3:delta+=8.0
    return delta
def apply_event(r,event,result,field):
    key=str(event.get("eventKey") or ""); before=max(.01,num(r.get("marketPrice"),num(r.get("fundamentalValue"),4)))
    delta=event_delta(r,result[1],result[2],field)
    move=result_move_from_delta(delta,scale=1.15,reference_pct=20.0,exponent=1.45,dead_zone_pct=2.0)*sensitivity(r)
    after=round(max(.01,before*(1+move/100.0)),2)
    actual=round((after/before-1)*100.0,3)
    out=dict(r);events=[dict(x) for x in out.get("priceEvents",[]) if isinstance(x,dict)]
    item={"eventKey":key,"eventId":event.get("eventId"),"eventType":"race","provider":"official-series","league":event.get("series"),"name":event.get("name"),"startedAt":event.get("startedAt"),"verified":True,"verifiedSource":event.get("sourceName"),"sourceUrl":event.get("sourceUrl"),"finishPosition":result[1],"raceStatus":result[2],"performanceDeltaPct":round(delta,3),"modelMovePct":round(move,3),"movePct":actual,"priceBefore":round(before,2),"priceAfter":after,"pricingModel":MODEL_VERSION}
    events.append(item);out["priceEvents"]=events[-500:]
    history=[dict(x) for x in out.get("priceHistory",[]) if isinstance(x,dict)]
    stamp=str(event.get("startedAt") or "")
    try:
        close_time=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        if close_time.tzinfo is None:close_time=close_time.replace(tzinfo=timezone.utc)
        open_stamp=(close_time-timedelta(seconds=1)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
    except ValueError:
        open_stamp=stamp
    history.extend([
      {"time":open_stamp,"price":round(before,2),"eventId":key,"label":event.get("name"),"phase":"open","historyType":"verified","eventType":"race","source":event.get("sourceUrl"),"movePct":actual},
      {"time":stamp,"price":after,"eventId":key,"label":event.get("name"),"phase":"close","historyType":"verified","eventType":"race","source":event.get("sourceUrl"),"movePct":actual},
    ])
    out["priceHistory"]=sorted(history,key=lambda x:str(x.get("time") or ""))[-2500:]
    out["previousMarketPrice"]=round(before,2);out["marketPrice"]=after;out["dailyChange"]=actual;out["hourlyChangePct"]=actual
    out["lastGameMovePct"]=actual;out["lastPriceEventId"]=key;out["lastPriceEvent"]=event.get("name");out["lastPriceEventAt"]=event.get("startedAt")
    return out
def main():
    p=argparse.ArgumentParser();p.add_argument("--catalog",type=Path,required=True);p.add_argument("--events",type=Path,default=Path("data/verified_motorsport_events.json"));a=p.parse_args()
    rows=json.loads(a.catalog.read_text(encoding="utf-8"));payload=json.loads(a.events.read_text(encoding="utf-8"));events=payload.get("events",[])
    indexes={norm(r.get("name")):i for i,r in enumerate(rows) if isinstance(r,dict) and is_motorsport(r)}
    changed=0
    for event in events:
        if not isinstance(event,dict) or event.get("verified") is False:continue
        results=event.get("results") if isinstance(event.get("results"),list) else [];field=len(results)
        for result in results:
            if not isinstance(result,list) or len(result)<3:continue
            idx=indexes.get(norm(result[0]))
            if idx is None or str(event.get("eventKey") or "") in event_keys(rows[idx]):continue
            rows[idx]=apply_event(rows[idx],event,result,field);changed+=1
    a.catalog.write_text(json.dumps(rows,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    if a.catalog.name=="current_catalog.json":
        fields=sorted({k for r in rows for k,v in r.items() if isinstance(r,dict) and not isinstance(v,(dict,list))})
        with a.catalog.with_suffix(".csv").open("w",newline="",encoding="utf-8") as h:
            w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)
    print(f"Applied {changed:,} verified Motorsport driver-race event(s) exactly once.")
    return 0
if __name__=="__main__":raise SystemExit(main())
