#!/usr/bin/env python3
"""Remove unverified Wikidata-only Motorsport market proxies.

A generic occupation statement is discovery evidence, not proof of a current
professional racing seat. Other individual sports are intentionally untouched.
"""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
from typing import Any

def should_prune(r:dict[str,Any])->bool:
    return (
        str(r.get("primaryCategory") or "")=="Athlete"
        and str(r.get("discipline") or "").lower()=="motorsport"
        and str(r.get("sourceNamespace") or "")=="wikidata-individual-sport"
        and not bool(r.get("motorsportProfessionalVerified"))
    )

def write_csv(path,records):
    fields=sorted({k for r in records for k,v in r.items() if not isinstance(v,(dict,list))})
    with path.open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(records)

def main():
    p=argparse.ArgumentParser();p.add_argument("--catalog",type=Path,required=True);a=p.parse_args()
    rows=json.loads(a.catalog.read_text(encoding="utf-8")); removed=[r for r in rows if isinstance(r,dict) and should_prune(r)]
    out=[dict(r) for r in rows if isinstance(r,dict) and not should_prune(r)]
    a.catalog.write_text(json.dumps(out,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    if a.catalog.name=="current_catalog.json":write_csv(a.catalog.with_suffix(".csv"),out)
    print(f"Pruned {len(removed):,} unverified Wikidata-only Motorsport proxy listing(s); other sports unchanged.")
    return 0
if __name__=="__main__":raise SystemExit(main())
