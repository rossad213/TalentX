#!/usr/bin/env python3
"""Canonicalize duplicate Motorsport identities by exact normalized name."""
from __future__ import annotations

import argparse, csv, json, re, unicodedata
from pathlib import Path
from typing import Any

def norm(value: Any) -> str:
    text=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+","",text)

def is_motor(r: dict[str,Any]) -> bool:
    return str(r.get("primaryCategory") or "")=="Athlete" and str(r.get("discipline") or "").lower()=="motorsport"

def score(r: dict[str,Any]) -> tuple[int,int,float,float]:
    ns=str(r.get("sourceNamespace") or "")
    official=int(ns=="curated-individual-sport-roster")
    structured=int(isinstance(r.get("motorsportEvidence"),dict) and bool(r["motorsportEvidence"]))
    return official,structured,float(r.get("dataConfidence") or 0),float(r.get("pricingConfidence") or 0)

def merge_dict_lists(group, canonical, field, key_fields):
    out={}
    for r in [x for x in group if x is not canonical]+[canonical]:
        for item in r.get(field,[]) if isinstance(r.get(field),list) else []:
            if not isinstance(item,dict): continue
            key=tuple(str(item.get(k) or "") for k in key_fields)
            if any(key): out[key]=dict(item)
    return sorted(out.values(),key=lambda x:str(x.get("startedAt") or x.get("time") or ""))

def reconcile(records:list[dict[str,Any]]) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    groups={}
    for r in records:
        if is_motor(r) and norm(r.get("name")):
            groups.setdefault(norm(r["name"]),[]).append(r)
    suppress=set(); replacements={}; repairs=[]
    for key,group in groups.items():
        if len(group)<2: continue
        canonical=max(group,key=score)
        cid=str(canonical.get("id") or "")
        if not cid: continue
        result=dict(canonical)
        result["priceEvents"]=merge_dict_lists(group,canonical,"priceEvents",("eventKey","eventId","startedAt"))
        result["priceHistory"]=merge_dict_lists(group,canonical,"priceHistory",("time","eventId","phase"))
        aliases=sorted(str(x.get("id") or "") for x in group if x is not canonical and x.get("id"))
        result["motorsportCanonicalAliasIds"]=aliases
        result["motorsportIdentityReconciled"]=True
        replacements[cid]=result
        for r in group:
            rid=str(r.get("id") or "")
            if rid and rid!=cid:
                suppress.add(rid)
                repairs.append({"name":result.get("name"),"canonicalId":cid,"suppressedId":rid})
    output=[]
    for r in records:
        rid=str(r.get("id") or "")
        if rid in suppress: continue
        output.append(replacements.get(rid,dict(r)))
    return output,repairs

def write_csv(path:Path,records):
    fields=sorted({k for r in records for k,v in r.items() if not isinstance(v,(dict,list))})
    with path.open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(records)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--catalog",type=Path,required=True); a=p.parse_args()
    payload=json.loads(a.catalog.read_text(encoding="utf-8")); records=[dict(x) for x in payload if isinstance(x,dict)]
    out,repairs=reconcile(records)
    a.catalog.write_text(json.dumps(out,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    if a.catalog.name=="current_catalog.json": write_csv(a.catalog.with_suffix(".csv"),out)
    print(f"Motorsport identity reconciliation: {len(records):,} -> {len(out):,}; suppressed {len(repairs):,} duplicate listing(s).")
    for row in repairs[:30]: print(f"  {row['name']}: {row['suppressedId']} -> {row['canonicalId']}")
    return 0
if __name__=="__main__": raise SystemExit(main())
