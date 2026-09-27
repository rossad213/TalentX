#!/usr/bin/env python3
"""One-time Motorsport market reset onto the structured race-evidence model."""
from __future__ import annotations
import argparse,csv,json,math
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from pricing_engine_v2 import apply_v2
from motorsport_metric_calibration import is_motorsport

MIGRATION_VERSION="1.0-motorsport-race-evidence"
MIGRATION_EVENT_ID="model:motorsport-race-evidence-v1"
V2_FIELDS=("talentScore","marketScore","confidenceScore","situationScore","expectedValueScore","fairValue","fundamentalValue","pricingModelVersion","pricingEngine","pricingV2","motorsportMetrics","motorsportCalibrationVersion","motorsportEvidenceStatus")

def finite(v):
    try:x=float(v)
    except (TypeError,ValueError):return None
    return x if math.isfinite(x) else None

def migrate_record(record:dict[str,Any],stamp:str)->tuple[dict[str,Any],bool]:
    if not is_motorsport(record): return dict(record),False
    if str(record.get("motorsportMarketMigrationVersion") or "")==MIGRATION_VERSION:return dict(record),False
    if not isinstance(record.get("motorsportEvidence"),dict) or not record["motorsportEvidence"]:
        return dict(record),False
    prior=finite(record.get("marketPrice"))
    clean=dict(record);clean["lastGameMovePct"]=0.0;clean["dailyChange"]=0.0;clean["hourlyChangePct"]=0.0
    priced=apply_v2(clean);target=finite(priced.get("fairValue"))
    if target is None or target<=0:return dict(record),False
    target=round(target,2);result=dict(record)
    for field in V2_FIELDS:
        if field in priced:result[field]=priced[field]
    history=[dict(x) for x in result.get("priceHistory",[]) if isinstance(x,dict)]
    if not any(str(x.get("eventId") or "")==MIGRATION_EVENT_ID for x in history):
        history.append({"time":stamp,"price":target,"eventId":MIGRATION_EVENT_ID,"label":"Motorsport race-evidence market migration","phase":"close","historyType":"verified","eventType":"model_migration"})
    result["priceHistory"]=history
    result["marketPrice"]=target;result["previousMarketPrice"]=target;result["modelTargetPrice"]=target
    result["dailyChange"]=0.0;result["hourlyChangePct"]=0.0;result["lastGameMovePct"]=0.0;result["trend"]=[target]*18
    result["motorsportMarketMigrationVersion"]=MIGRATION_VERSION
    result["motorsportMarketMigratedAt"]=stamp
    result["motorsportMarketMigrationPriorMarketPrice"]=round(prior,2) if prior is not None else None
    result["motorsportMarketMigrationTargetPrice"]=target
    result["motorsportMarketMigrationReason"]="Reset Motorsport current market state after structured official-series evidence and race-start confidence calibration"
    return result,True

def write_csv(path,records):
    fields=sorted({k for r in records for k,v in r.items() if not isinstance(v,(dict,list))})
    with path.open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(records)

def main():
    p=argparse.ArgumentParser();p.add_argument("--catalog",type=Path,default=Path("data/current_catalog.json"));a=p.parse_args()
    rows=json.loads(a.catalog.read_text(encoding="utf-8"));stamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
    out=[];changed=0
    for row in rows:
        if not isinstance(row,dict):continue
        updated,did=migrate_record(row,stamp);out.append(updated);changed+=int(did)
    a.catalog.write_text(json.dumps(out,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    if a.catalog.name=="current_catalog.json":write_csv(a.catalog.with_suffix(".csv"),out)
    print(f"Migrated {changed:,} Motorsport listing(s) to structured race-evidence market epoch; non-Motorsport records unchanged.")
    return 0
if __name__=="__main__":raise SystemExit(main())
