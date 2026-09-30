#!/usr/bin/env python3
"""One-time Actor market rebase onto source-backed identity, events, and v2 fair value.

The Actor workflow historically restored durable event market prices after fair-value
recalculation. That preserved valid event history, but it also preserved old price
levels from before Actor evidence gating and allowed alias/date-key duplicates to
remain compounded. This migration creates a new Actor-only market epoch:

* canonicalize Actor event identities around Wikidata person/work identity;
* collapse semantic duplicate project/outcome events;
* scrub stale Music-only metadata from Actor records;
* replay verified Actor events so their chart shape ends at today's v2 fair value;
* reset the current market price to that fair value exactly once.

Future verified Actor events continue to compound normally from the new epoch.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pricing_engine_v2 import apply_v2

MIGRATION_VERSION = "1.7-actor-v6.10-idempotent-elite-epoch"
MIGRATION_EVENT_ID = "model:actor-idempotent-elite-market-epoch-v1-7"

SUPPORTED_EVENT_TYPES = {
    "actor-release",
    "actor-upcoming-project",
    "actor-box-office-outcome",
    "actor-streaming-outcome",
    "actor-attention-outcome",
    "award",
    "nomination",
}


def finite(value: Any) -> float | None:
    try:
        parsed=float(value)
    except (TypeError,ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_time(value: Any) -> datetime | None:
    text=str(value or "").strip()
    if not text:
        return None
    try:
        parsed=datetime.fromisoformat(text.replace("Z","+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")


def is_actor(record: dict[str,Any]) -> bool:
    return str(record.get("primaryCategory") or "")=="Actor"


def actor_qid(record: dict[str,Any]) -> str:
    for key in ("wikidataSourceRecordId","sourceRecordId"):
        value=str(record.get(key) or "").strip()
        if re.fullmatch(r"Q\d+",value):
            return value
    return ""


def event_date(event: dict[str,Any]) -> str:
    when=parse_time(event.get("startedAt") or event.get("time") or event.get("date"))
    return when.date().isoformat() if when is not None else ""

def event_observation_key(event: dict[str,Any]) -> str:
    when=parse_time(event.get("startedAt") or event.get("time") or event.get("date"))
    return iso(when) if when is not None else ""


def provider_slug(event: dict[str,Any]) -> str:
    provider=str(event.get("provider") or "").strip().lower()
    if "numbers" in provider:
        return "the-numbers"
    if "netflix" in provider or "tudum" in provider:
        return "netflix-top10"
    if "wikimedia" in provider:
        return "wikimedia"
    return re.sub(r"[^a-z0-9]+","-",provider).strip("-") or "actor-event"


def canonical_event_key(record: dict[str,Any], event: dict[str,Any]) -> str:
    original=str(event.get("eventKey") or event.get("eventId") or "").strip()
    event_type=str(event.get("eventType") or "").strip()
    person=actor_qid(record)
    work=str(event.get("workQid") or "").strip()
    date=event_date(event)

    if person and work and event_type in {"actor-release","actor-upcoming-project"}:
        return f"wikidata:{event_type}:{person}:{work}"
    if person and work and event_type in {"actor-box-office-outcome","actor-streaming-outcome"} and date:
        return f"{provider_slug(event)}:{person}:{work}:{date}"
    if person and event_type=="actor-attention-outcome":
        observation=event_observation_key(event)
        if observation:
            return f"wikimedia:attention:{person}:{observation}"
    return original


def semantic_event_key(record: dict[str,Any], event: dict[str,Any]) -> tuple[str,...]:
    event_type=str(event.get("eventType") or "").strip()
    work=str(event.get("workQid") or "").strip()
    provider=provider_slug(event)
    date=event_date(event)

    if work and event_type in {"actor-release","actor-upcoming-project"}:
        return (event_type,work)
    if work and event_type in {"actor-box-office-outcome","actor-streaming-outcome"}:
        return (event_type,provider,work,date)
    if event_type=="actor-attention-outcome":
        return (event_type,canonical_event_key(record,event))
    return (canonical_event_key(record,event),)


def event_strength(event: dict[str,Any]) -> tuple[float,str]:
    return (
        abs(finite(event.get("movePct")) or 0.0),
        str(event.get("startedAt") or event.get("time") or ""),
    )


def canonicalize_events(record: dict[str,Any]) -> tuple[list[dict[str,Any]],dict[str,str],int]:
    groups: dict[tuple[str,...], list[dict[str,Any]]] = {}
    aliases: dict[str,str] = {}
    passthrough: list[dict[str,Any]] = []

    for raw in record.get("priceEvents",[]) if isinstance(record.get("priceEvents"),list) else []:
        if not isinstance(raw,dict):
            continue
        event=dict(raw)
        event_type=str(event.get("eventType") or "")
        old_key=str(event.get("eventKey") or event.get("eventId") or "")
        if event_type not in SUPPORTED_EVENT_TYPES:
            passthrough.append(event)
            continue
        new_key=canonical_event_key(record,event)
        if new_key:
            aliases[old_key]=new_key
            event["eventKey"]=new_key
            if event_type in {"actor-box-office-outcome","actor-streaming-outcome","actor-attention-outcome"}:
                event["eventId"]=new_key
        groups.setdefault(semantic_event_key(record,event),[]).append(event)

    kept: list[dict[str,Any]]=[]
    removed=0
    for values in groups.values():
        winner=max(values,key=event_strength)
        kept.append(winner)
        removed += max(0,len(values)-1)
        winner_key=str(winner.get("eventKey") or winner.get("eventId") or "")
        for event in values:
            old=str(event.get("eventKey") or event.get("eventId") or "")
            if old:
                aliases[old]=winner_key

    all_events=[*passthrough,*kept]
    all_events.sort(key=lambda item:str(item.get("startedAt") or item.get("time") or ""))
    return all_events,aliases,removed


def normalized_event_path(events: list[dict[str,Any]], target: float) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    priced=[
        dict(event) for event in events
        if str(event.get("eventType") or "") in SUPPORTED_EVENT_TYPES
        and finite(event.get("movePct")) is not None
        and parse_time(event.get("startedAt") or event.get("time")) is not None
    ]
    priced.sort(key=lambda item:str(item.get("startedAt") or item.get("time") or ""))
    if not priced:
        return events,[]

    factor=1.0
    for event in priced:
        factor *= max(0.01,1.0+(finite(event.get("movePct")) or 0.0)/100.0)
    price=max(0.01,target/max(0.01,factor))
    rebuilt_by_key: dict[str,dict[str,Any]]={}
    history: list[dict[str,Any]]=[]

    for index,raw in enumerate(priced):
        event=dict(raw)
        move=finite(event.get("movePct")) or 0.0
        before=price
        after=max(0.01,before*(1.0+move/100.0))
        if index==len(priced)-1:
            after=target
        before_r=round(before,2)
        after_r=round(after,2)
        realized=round((after_r/before_r-1.0)*100.0,3) if before_r>0 else round(move,3)
        event["priceBefore"]=before_r
        event["priceAfter"]=after_r
        event["movePct"]=realized
        event["marketEpochRebased"]=MIGRATION_VERSION
        key=str(event.get("eventKey") or event.get("eventId") or "")
        rebuilt_by_key[key]=event
        when=parse_time(event.get("startedAt") or event.get("time"))
        if when is not None:
            label=str(event.get("name") or "Verified Actor event")
            common={
                "eventId":key,"label":label,"historyType":"verified-event-replay",
                "eventType":str(event.get("eventType") or "actor-event"),
                "provider":event.get("provider"),"source":event.get("sourceUrl"),
                "movePct":realized,
            }
            history.extend([
                {"time":iso(when-timedelta(seconds=1)),"price":before_r,"phase":"open",**common},
                {"time":iso(when),"price":after_r,"phase":"close",**common},
            ])
        price=after

    output=[]
    for event in events:
        key=str(event.get("eventKey") or event.get("eventId") or "")
        output.append(rebuilt_by_key.get(key,dict(event)))
    output.sort(key=lambda item:str(item.get("startedAt") or item.get("time") or ""))
    return output,history


def clean_metadata(record: dict[str,Any]) -> dict[str,Any]:
    result=dict(record)
    for field in ("musicCategoryVerified","musicCategoryVerification","musicBrainzArtistIds","verifiedMusicOccupations"):
        result.pop(field,None)
    namespace=str(result.get("sourceNamespace") or "")
    if namespace.startswith("wikidata-music"):
        result["categoryOriginSourceNamespace"]=namespace
        result["sourceNamespace"]="wikidata-actor-resolved-from-music"
    status=str(result.get("pricingDataStatus") or "")
    if "roster, experience and role" in status.lower() or status.startswith("Strict music-source"):
        result["pricingDataStatus"]="Screen-career identity verified; direct performance evidence may be partial"
    return result


def clean_history(record: dict[str,Any], aliases: dict[str,str]) -> list[dict[str,Any]]:
    actor_keys=set(aliases)|set(aliases.values())
    out=[]
    seen=set()
    for raw in record.get("priceHistory",[]) if isinstance(record.get("priceHistory"),list) else []:
        if not isinstance(raw,dict):
            continue
        item=dict(raw)
        event_id=str(item.get("eventId") or item.get("eventKey") or "")
        event_type=str(item.get("eventType") or "")
        if event_id=="current-market-price" or event_type=="market-observation":
            continue
        if event_id in actor_keys or event_type in SUPPORTED_EVENT_TYPES:
            continue
        if (
            event_type=="recorded-event"
            and event_id.startswith(("wikidata:award:", "wikidata:nomination:", "wikidata:actor-"))
        ):
            continue
        if event_type=="model_migration" and event_id.startswith("model:actor-source-first-market-epoch"):
            continue
        key=(str(item.get("time") or ""),event_id,str(item.get("phase") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def migrate_record(record: dict[str,Any], stamp: str) -> tuple[dict[str,Any],bool]:
    if not is_actor(record):
        return dict(record),False
    if str(record.get("actorMarketMigrationVersion") or "")==MIGRATION_VERSION:
        return dict(record),False

    prior=finite(record.get("marketPrice"))

    result=clean_metadata(record)
    events,aliases,removed=canonicalize_events(result)
    result["priceEvents"]=events
    # Recalculate Actor v2 only after semantic dedupe so duplicated legacy alias
    # events cannot inflate direct-evidence maturity during the new market epoch.
    repriced=apply_v2(result)
    for field in (
        "talentScore","marketScore","confidenceScore","situationScore",
        "expectedValueScore","fairValue","fundamentalValue",
        "pricingModelVersion","pricingEngine","pricingV2",
    ):
        if field in repriced:
            result[field]=repriced[field]
    target=finite(result.get("fairValue") or result.get("fundamentalValue"))
    if target is None or target<=0:
        return dict(record),False
    target=round(target,2)
    rebuilt,event_history=normalized_event_path(events,target)
    result["priceEvents"]=rebuilt[-2500:]

    history=clean_history(result,aliases)
    history.extend(event_history)
    history.append({
        "time":stamp,
        "eventId":MIGRATION_EVENT_ID,
        "label":"Actor source-first market rebase",
        "phase":"close",
        "price":target,
        "historyType":"verified",
        "eventType":"model_migration",
        "priceBasis":"one-time Actor identity/event cleanup and evidence-scale market reset",
    })
    history.sort(key=lambda item:str(item.get("time") or ""))
    result["priceHistory"]=history[-2500:]
    result["marketPrice"]=target
    result["modelTargetPrice"]=target
    result["previousMarketPrice"]=target
    result["dailyChange"]=0.0
    result["hourlyChangePct"]=0.0
    result["lastPriceRefreshAt"]=stamp
    closes=[
        finite(item.get("priceAfter")) for item in rebuilt
        if str(item.get("eventType") or "") in SUPPORTED_EVENT_TYPES and finite(item.get("priceAfter")) is not None
    ]
    result["trend"]=[round(float(value),2) for value in closes[-18:]] or [target]
    priced=[
        e for e in rebuilt
        if str(e.get("eventType") or "") in SUPPORTED_EVENT_TYPES
        and finite(e.get("movePct")) is not None
    ]
    if priced:
        latest=priced[-1]
        result["lastPriceEventAt"]=latest.get("startedAt")
        result["lastPriceEvent"]=latest.get("name")
        result["lastPriceEventId"]=latest.get("eventKey") or latest.get("eventId")
        result["lastEventMovePct"]=latest.get("movePct")
        result["lastEventType"]=latest.get("eventType")
        result["lastEventSource"]=latest.get("provider")
    else:
        for field in (
            "lastPriceEventAt","lastPriceEvent","lastPriceEventId",
            "lastEventMovePct","lastEventType","lastEventSource",
        ):
            result.pop(field,None)
    result.pop("priceExplanation",None)
    result["priceHistoryStatus"]="source-backed-partial-history" if event_history else "market-observation-only"
    result["actorMarketMigrationVersion"]=MIGRATION_VERSION
    result["actorMarketMigratedAt"]=stamp
    result["actorMarketMigrationPriorMarketPrice"]=round(prior,2) if prior is not None else None
    result["actorMarketMigrationTargetPrice"]=target
    result["actorMarketDuplicateEventsRemoved"]=removed
    result["actorMarketMigrationReason"]="Rebase Actor after v6.4 evidence-scale calibration, source-first identity cleanup, actor-wide attention dedupe, curated-prior preservation, and semantic event canonicalization"
    return result,True


def migrate_catalog(path: Path, migrated_at: str | None=None) -> tuple[int,int]:
    payload=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload,list):
        raise ValueError(f"{path} must contain a JSON array")
    stamp=migrated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
    output=[]
    changed=0
    removed=0
    for item in payload:
        if not isinstance(item,dict):
            continue
        updated,did=migrate_record(item,stamp)
        output.append(updated)
        if did:
            changed+=1
            removed+=int(updated.get("actorMarketDuplicateEventsRemoved") or 0)
    path.write_text(json.dumps(output,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    if path.name=="current_catalog.json":
        fields=sorted({key for record in output for key,value in record.items() if not isinstance(value,(dict,list))})
        with path.with_suffix(".csv").open("w",newline="",encoding="utf-8") as handle:
            writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output)
    return changed,removed


def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--catalog",type=Path,default=Path("data/current_catalog.json"))
    args=parser.parse_args()
    changed,removed=migrate_catalog(args.catalog)
    print(f"Actor market migration: {changed:,} record(s) rebased; {removed:,} semantic duplicate event(s) removed.")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
