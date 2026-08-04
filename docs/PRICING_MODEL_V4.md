# TalentX Pricing Model v4

## Goal

Pricing Model v4 makes athlete, musician, actor, and creator valuations comparable on one universal 0–100 career-value scale without pretending that the same inputs mean the same thing in every profession.

## Category-specific fundamental models

### Athlete

- 35% verified current performance
- 25% documented achievements
- 15% sustained consistency
- 15% future potential
- 10% availability

Audience demand is not part of the athlete fundamental score. It is limited to the small market adjustment so popularity cannot replace production.

### Music

- 25% current commercial performance
- 25% catalog strength and longevity
- 20% awards and documented achievements
- 20% global audience demand
- 10% release momentum and future pipeline

### Actor

- 25% recent project performance
- 25% career body of work
- 20% awards and critical recognition
- 20% audience demand and bankability
- 10% upcoming project pipeline

### Creator

- 25% audience reach
- 25% engagement quality
- 20% audience growth
- 15% consistency and retention
- 15% commercial and brand strength

## Cross-category calibration

The universal career score is:

- 70% absolute category-specific evidence score
- 30% percentile-calibrated position within the same profession

The peer component is bounded to a 45–95 range, so being first in a weak or tiny cohort cannot create a perfect score by itself.

## Athlete input corrections

Pricing Model v4 removes the custom playing-time signal from athlete performance and availability.

- Performance = 70% recent production + 30% efficiency
- Achievements = 70% career production + 30% documented awards
- Consistency = 65% career production + 35% recent production
- Availability = neutral 75 for active careers and 55 for inactive careers until verified games-available coverage is normalized across leagues
- Draft bonuses only apply before a professional debut; they do not permanently inflate established-player potential

## Evidence controls

Evidence tier controls the maximum unsupported career score and the confidence adjustment applied to fundamental value.

- Verified override: maximum 100
- Strong evidence-enriched record: maximum 97
- Standard record: maximum 94
- Curated benchmark prior: maximum 95
- Under review: maximum 82
- Roster-only provisional record: fundamental value capped at $62 and market price capped at $65

The ordered current-seed list is used as a temporary benchmark prior only for curated prototype records. Once profession-specific evidence is available, verified evidence replaces the benchmark prior.

## Market price

Market price is separated from fundamental value.

- Audience demand adjustment
- Current momentum adjustment
- Availability/risk adjustment

These adjustments are calculated directly from saved evidence and contain no random price jitter. The combined market adjustment is capped at ±6%, preventing hype from overpowering documented career value.

After a full evidence build, every listing begins with a 0.00% change and a flat chart. A shared price changes only after a completed game with a player-level box score, or when another supported evidence event is added in a future feed. A virtual trade changes only that user's browser price. Hourly runs leave all unrelated listings unchanged.

### Completed-game movement

- Each completed game is identified by its stable provider and event ID and is processed once.
- The player's individual box-score production and efficiency are compared with that player's saved season baseline (80% production, 20% efficiency when comparable efficiency data is present).
- Season and career evidence continues to set fundamental value and supplies a small anchor toward the model target.
- A normal above- or below-expectation game creates a modest move; outcome adds only a small adjustment.
- A single game is capped at 2.5%, preventing one result from overwhelming established value.
- The hourly job searches the prior 48 hours, so delayed runs can catch games that a short hourly window would miss.
- Processed-event history is retained across weekly rebuilds, preventing the same game from being priced twice.

## Regression tests

The validation suite now checks:

- Every category weight set totals 100%
- Athlete performance remains the strongest input
- Provisional records remain under their caps
- Curated and low-confidence records cannot enter unsupported tiers
- Market adjustments remain within ±6%
- Repricing is reproducible from the saved evidence
- Full builds cannot manufacture a daily change or chart movement
- Live games cannot move prices before their box scores are final
- A completed game creates one bounded price point and a repeated event ID creates none
- No-game refreshes preserve both price and chart history
- Anthony Edwards prices above Amen Thompson and Tyrese Maxey
- Taylor Swift and Beyoncé price above Gracie Abrams
- MrBeast prices above Marques Brownlee
- Zendaya prices above Pedro Pascal
- Established NFL starters price above weak or fringe comparison records
- Rookie draft metadata and Rookie IPO transitions remain functional

## Current limitation

The music, actor, and creator models now have correct profession-specific architecture, but their dedicated evidence ingestion feeds still need to be built. Until then, current-seed order acts as a transparent temporary prior rather than allowing randomly generated prototype metrics to determine elite rankings.
