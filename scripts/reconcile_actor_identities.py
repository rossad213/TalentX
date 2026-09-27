#!/usr/bin/env python3
"""Reconcile duplicate TalentX Actor identities while preserving verified market state."""
from __future__ import annotations
import argparse, csv, json, re, unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any
try:
    from merge_hourly_market_state import MARKET_STATE_FIELDS
except ModuleNotFoundError:
    from scripts.merge_hourly_market_state import MARKET_STATE_FIELDS

def normalize_name(value: Any) -> str:
    text=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+","",text.lower())

def is_actor(record:dict[str,Any])->bool:
    return str(record.get("primaryCategory") or "")=="Actor"

def _qid(record:dict[str,Any])->str:
    for key in ("wikidataSourceRecordId","sourceRecordId"):
        value=str(record.get(key) or "").strip()
        if re.fullmatch(r"Q\d+",value): return value
    return ""

def _is_curated(record:dict[str,Any])->bool:
    return bool(record.get("nonAthleteRosterVersion")) or "curated" in str(record.get("sourceNamespace") or "").lower()

def _same_identity(group:list[dict[str,Any]])->bool:
    qids=[x for x in (_qid(r) for r in group) if x]
    if qids and len(set(qids))==1: return True
    curated=[r for r in group if _is_curated(r)]
    discovered=[r for r in group if not _is_curated(r)]
    return len(curated)==1 and bool(discovered)

def _num(v:Any)->float:
    try:return float(v)
    except (TypeError,ValueError):return 0.0

def _canonical_score(record:dict[str,Any],preferred:set[str])->tuple[Any,...]:
    rid=str(record.get("id") or "")
    events=len(record.get("priceEvents") or []) if isinstance(record.get("priceEvents"),list) else 0
    history=len(record.get("priceHistory") or []) if isinstance(record.get("priceHistory"),list) else 0
    return (int(rid in preferred),int(_is_curated(record)),int(bool(_qid(record))),events+history,_num(record.get("dataConfidence")),_num(record.get("pricingConfidence")))

def _state_score(record:dict[str,Any])->tuple[str,int,float]:
    timestamps=[str(record.get("lastPriceEventAt") or ""),str(record.get("lastPriceRefreshAt") or "")]
    events=record.get("priceEvents") if isinstance(record.get("priceEvents"),list) else []
    history=record.get("priceHistory") if isinstance(record.get("priceHistory"),list) else []
    timestamps.extend(str(i.get("startedAt") or i.get("time") or "") for i in events if isinstance(i,dict))
    timestamps.extend(str(i.get("time") or i.get("date") or "") for i in history if isinstance(i,dict))
    return (max(timestamps or [""]),len(events)+len(history),_num(record.get("marketPrice")))

def _event_key(e:dict[str,Any])->tuple[str,str,str]:
    return (str(e.get("eventKey") or e.get("eventId") or ""),str(e.get("startedAt") or e.get("time") or e.get("date") or ""),str(e.get("eventType") or ""))

def _history_key(p:dict[str,Any])->tuple[str,str,str,str]:
    return (str(p.get("time") or p.get("date") or ""),str(p.get("eventId") or p.get("eventKey") or ""),str(p.get("phase") or ""),str(p.get("source") or ""))

def _merge_lists(group,field,key_fn,preferred):
    merged={}
    ordered=[r for r in group if r is not preferred]+[preferred]
    for record in ordered:
        vals=record.get(field) if isinstance(record.get(field),list) else []
        for value in vals:
            if not isinstance(value,dict):continue
            key=key_fn(value)
            if any(str(part) for part in key):merged[key]=dict(value)
    return sorted(merged.values(),key=lambda item:str(item.get("startedAt") or item.get("time") or item.get("date") or ""))

def reconcile_records(records:list[dict[str,Any]],preferred_ids:set[str]|None=None):
    preferred_ids=preferred_ids or set()
    groups=defaultdict(list)
    for record in records:
        if is_actor(record):
            key=normalize_name(record.get("name"))
            if key:groups[key].append(record)
    replacements={};suppressed=set();repairs=[]
    for name_key,group in groups.items():
        if len(group)<2 or not _same_identity(group):continue
        canonical=max(group,key=lambda r:_canonical_score(r,preferred_ids))
        cid=str(canonical.get("id") or "")
        if not cid:continue
        donor=max(group,key=_state_score)
        result=dict(canonical)
        for field in MARKET_STATE_FIELDS:
            if field in donor and field not in {"priceEvents","priceHistory"}:result[field]=donor[field]
        result["priceEvents"]=_merge_lists(group,"priceEvents",_event_key,donor)
        result["priceHistory"]=_merge_lists(group,"priceHistory",_history_key,donor)
        qids=sorted({x for x in (_qid(r) for r in group) if x})
        if len(qids)==1:result["wikidataSourceRecordId"]=qids[0]
        evidence=[]
        for r in group:
            if isinstance(r.get("pricingEvidence"),list):evidence.extend(str(v) for v in r["pricingEvidence"] if v)
        if evidence:result["pricingEvidence"]=list(dict.fromkeys(evidence))
        aliases=sorted(str(r.get("id") or "") for r in group if str(r.get("id") or "") and str(r.get("id") or "")!=cid)
        result["actorIdentityReconciled"]=True
        result["actorCanonicalAliasIds"]=aliases
        result["actorIdentityKey"]=name_key
        replacements[cid]=result
        for r in group:
            rid=str(r.get("id") or "")
            if rid and rid!=cid:
                suppressed.add(rid)
                repairs.append({"name":str(result.get("name") or ""),"canonicalId":cid,"suppressedId":rid,"wikidataId":qids[0] if len(qids)==1 else ""})
    output=[]
    for r in records:
        rid=str(r.get("id") or "")
        if rid in suppressed:continue
        output.append(replacements.get(rid,dict(r)))
    return output,repairs

def preferred_actor_ids(path:Path|None)->set[str]:
    if path is None or not path.exists():return set()
    payload=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload,list):return set()
    return {str(r.get("id") or "") for r in payload if isinstance(r,dict) and is_actor(r) and r.get("id")}

def write_csv(path:Path,records):
    fields=sorted({k for r in records for k,v in r.items() if not isinstance(v,(dict,list))})
    with path.open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(records)

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--catalog",type=Path,required=True);p.add_argument("--preferred-catalog",type=Path);args=p.parse_args()
    payload=json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload,list):raise ValueError(f"{args.catalog} must contain a JSON array")
    records=[dict(i) for i in payload if isinstance(i,dict)]
    reconciled,repairs=reconcile_records(records,preferred_actor_ids(args.preferred_catalog))
    args.catalog.write_text(json.dumps(reconciled,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    if args.catalog.name=="current_catalog.json":write_csv(args.catalog.with_suffix(".csv"),reconciled)
    print(f"Actor identity reconciliation: {len(records):,} -> {len(reconciled):,}; suppressed {len(repairs):,} duplicate listing(s).")
    for repair in repairs[:40]:print(f"  {repair['name']}: {repair['suppressedId']} -> {repair['canonicalId']}")
    return 0
if __name__=="__main__":raise SystemExit(main())
