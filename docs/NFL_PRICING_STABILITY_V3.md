# NFL Pricing Stability v3

TalentX NFL fundamentals separate durable player value from short-term game movement.

## Position normalization

Raw football statistics are not directly comparable across positions. Quarterback passing yards, running-back scrimmage production, receiver production, defensive production, offensive-line availability, and special-teams production are first converted into percentiles inside comparable position groups.

Those normalized 0–100 values then feed one universal NFL TalentX price scale. Position itself does not receive a price premium.

## Early-season fundamentals

A one- or two-game current-season sample should not silently replace an established player's durable career value. Recent production and efficiency ramp into the fundamental over the first six estimated games of the season:

- 1 game: 1/6 current-season weight
- 2 games: 2/6
- 3 games: 3/6
- 4 games: 4/6
- 5 games: 5/6
- 6+ games: full current-season weight

Recent production is shrunk toward the player's career-production percentile during this ramp. Efficiency is shrunk toward a neutral 50th-percentile prior. Verified game outcomes remain separate and can still move the market price immediately.

## Rookie and no-debut players

Professional games, not roster tenure alone, control how quickly a Rookie IPO anchor fades. If an upstream catalog step removed the saved rookie pricing block but factual NFL draft metadata remains, TalentX can reconstruct the IPO anchor from the same formula inputs rather than collapsing a drafted player to a generic roster-only price.

For a drafted player who still has zero NFL games, the maximum remaining draft-anchor influence also decays with time since the draft:

- draft year: 100%
- one year later: 60%
- two years later: 30%
- three years later: 10%
- four or more years later: 0%

This prevents both extremes: a second-year player with no debut should not be treated like an undrafted unknown, but a player cannot keep rookie valuation indefinitely without professional production.

## Pricing principle

Fundamental value reflects normalized production, achievements, career runway, and availability. Game-to-game price movement reflects verified performance versus that individual player's expectation. The model does not add a fame premium, starter premium, or random price noise.
