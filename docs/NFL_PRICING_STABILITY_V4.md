# NFL Pricing Stability v4 — Unified Established-Evidence Model

TalentX NFL pricing separates **fundamental player value** from **short-term market movement**. The v4 stability design fixes four problems observed in the live catalog: mixed NFL cohort pathways, repeated small-sample shrinkage, excessive confidence differences between established players, and award counts that lacked human-readable award titles.

No player-specific prices or ranking overrides are used.

## 1. One authoritative NFL evidence path

Every NFL player is normalized against a comparable football position group before entering the universal TalentX value scale:

- QB
- RB
- REC (wide receivers and tight ends)
- DEF
- OL
- ST

The hourly NFL adapter no longer puts all NFL players into an `NFL · UNIVERSAL` raw-stat cohort. Raw QB production is compared with QBs, RB production with RBs, and so on. The resulting normalized 0–100 values can then be compared on the shared TalentX price scale.

When a refresh has at least eight same-run NFL peers in a position group, that same-run pool is authoritative. This prevents newly stabilized evidence from being ranked against stale evidence produced by an older model generation.

## 2. Rookie/second-year transition versus established players

NFL players are divided into two modeling states.

### Rookie IPO transition

Players in their draft season or the following NFL season remain on the Rookie IPO transition path. Their professional statistics are put on the same per-game cohort scale as established players, but TalentX does **not** pretend they have a veteran multi-season baseline.

Their uncertainty is handled by the existing IPO blend: draft/pre-pro evidence fades as meaningful NFL evidence accumulates. The second season remains eligible for a small IPO anchor; Year 3 is the normal handoff to the established model when meaningful professional evidence exists.

### Established professional model

Beginning in Year 3, the fundamental model uses a durable multi-season evidence window. The purpose is to answer “How good has this player demonstrated he is?” rather than “What happened in the last one or two games?”

## 3. Established production window

For an established player, current-season cumulative statistics are converted to a **per-game production signal**. Completed prior seasons are also converted to per-game signals.

The two most recent completed seasons form the recent prior baseline:

[
Prior = 0.65(LastCompletedSeason) + 0.35(PreviousCompletedSeason)
]

when both exist.

Career per-game production is then added as a durable anchor:

[
EstablishedBaseline = 0.70(Prior) + 0.30(CareerPerGame)
]

when both inputs exist.

The current season does not immediately replace that baseline. It earns weight with games:

[
w_{current}=rac{CurrentSeasonGames}{CurrentSeasonGames+10}
]

and:

[
StableRecentProduction=(1-w_{current})(EstablishedBaseline)+w_{current}(CurrentSeasonPerGame)
]

Examples:

- 1 game → 9.1% current-season authority
- 2 games → 16.7%
- 4 games → 28.6%
- 8 games → 44.4%
- 12 games → 54.5%
- 17 games → 63.0%

The next season then treats the completed season as part of the established baseline. This makes fundamentals responsive without letting a hot September replace several seasons of evidence.

## 4. Rate-stat efficiency uses opportunities, not only games

Rate statistics such as YPC, YPR, completion rate and yards per attempt are particularly noisy in tiny samples. Their current-season weight is based on relevant opportunities.

The current efficiency weight is:

[
w_{eff}=rac{CurrentOpportunities}{CurrentOpportunities+PriorStrength}
]

Current opportunity definitions and stabilization priors are:

- QB: pass attempts, prior strength 300
- RB: carries + receptions, prior strength 200
- REC: targets when available, otherwise receptions, prior strength 120
- ST: field-goal attempts + punts, prior strength 40
- DEF/OL fallback: games, prior strength 10

The established efficiency baseline is built from the two most recent completed seasons plus career efficiency using the same 70% recent-prior / 30% career structure.

This means a two-game stretch in which one RB happens to post a higher YPC or YPR does not automatically make him fundamentally more efficient than an established peer with a stronger multi-season and career rate profile.

## 5. Small-sample shrinkage happens once

The old pipeline could stabilize an early-season percentile and then shrink the resulting Performance score again in the semantic calibration layer.

v4 removes that duplicate discount.

The evidence window is stabilized **before** percentile ranking. Once those percentiles reach the semantic Performance layer, they are treated as the already-stabilized evidence. Rookie and second-year uncertainty is handled by IPO blending rather than a second generic Performance shrink.

## 6. Performance semantics

After the evidence window is normalized inside the player's position cohort, NFL Performance remains:

[
PerformanceComposite =
0.50(RecentProductionPercentile)
+0.30(EfficiencyPercentile)
+0.20(CareerProductionPercentile)
]

[
Performance = 20 + 78(PerformanceComposite)
]

The “recent” percentile is no longer synonymous with one or two raw games for established players; it is the stable multi-season/current-season evidence window described above.

## 7. Confidence measures certainty, not career value

NFL Confidence continues to separate evidence certainty from accomplishments. Achievements and Consistency are not counted again inside Confidence.

Data quality is:

[
DataQuality = 65 + 35(PricingConfidence)
]

Sample maturity now saturates faster:

[
SampleMaturity = 100(1-e^{-Games/16})
]

If the game total is missing, professional experience is converted to an approximate representative sample of fourteen games per year before the same maturity curve is applied.

Final confidence is:

[
Confidence = 0.70(DataQuality)+0.30(SampleMaturity)
]

This makes 40–60 meaningful NFL games already highly informative. Additional veteran games still add certainty, but 81 games should not reverse the valuation of a stronger 51-game player merely because the older player has accumulated more appearances.

## 8. Award titles are resolved, not only counted

ESPN Core athlete-award collections can return reference-only items. The old parser could see the count but often stored:

`awardNames=[]`

v4 follows those returned award references with a bounded resolver and stores factual titles such as MVP or All-Pro when the source exposes them.

NFL award evidence is refreshed on a bounded 30-day cadence during hourly participant refreshes. Raw award counts remain usable if a title reference cannot be resolved.

The Achievement model can therefore distinguish the *kind* of recognition instead of treating every award collection as an anonymous count.

## 9. Fundamental value versus game movement

The established evidence window affects **fundamental value**.

Verified games still affect **market price** through the event ledger:

[
MarketPrice_{t+1}=MarketPrice_t(1+VerifiedEventMove_t)
]

A two-touchdown game can immediately move a player's market price because it exceeded expectation. It does not also receive full authority to redefine multi-season fundamental efficiency.

That separation is deliberate:

- durable/multi-season evidence → fundamental value
- verified game surprise → market-price event
- sustained current-season performance → gradually becomes part of the next fundamental baseline

## 10. Model-epoch migration

The semantic change creates NFL market migration version `1.3-nfl-established-evidence-reset`.

The Sports workflow now refreshes NFL evidence **before** performing that one-time reset. A live ESPN player is migrated only after the new evidence-window version exists on the record. Players missed by the current event lookback remain migration-eligible for a later refresh instead of being permanently rebased from stale `NFL · UNIVERSAL` evidence.

Historical events remain for audit, while the migration timestamp forms a hard boundary that prevents old pre-migration game events from being replayed into the new market epoch.

## 11. Design objective

The model is designed so that different players can create value for different reasons without hard-coded rankings:

- established production and rate quality
- individual honors
- consistency
- career runway
- availability
- market demand/context
- verified event performance

A player is never assigned a manual target price because of his name. If a ranking looks wrong, the correction should be made to the evidence definition, source ingestion, normalization, or model formula so that the same rule applies to every NFL player.
