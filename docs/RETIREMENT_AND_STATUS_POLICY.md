# TalentX Retirement and Career-Status Policy

## Core principle

A person's career value does not automatically become zero when active competition or active creative output ends. Retirement changes the valuation model.

## Athlete lifecycle

Supported athlete statuses:

1. Active
2. Injured / temporarily inactive
3. Free agent
4. Suspended
5. Retirement announced
6. Retired — Legacy
7. Status under review

A rumor must never directly change the official status. Status changes require a source record, timestamp, confidence score, and audit entry.

## Retirement workflow

1. Detect a possible retirement event.
2. Temporarily pause new orders if the event could materially affect price.
3. Verify the event through an approved official or licensed source.
4. Record the event in `talent_status_history`.
5. Change status to `Retirement announced`.
6. Recalculate the fundamental value using the Legacy Career Model.
7. Reopen the listing with an event marker and explanation.
8. Move status to `Retired — Legacy` on the effective retirement date.

## Active Career Model

Suggested athlete weights:

- 30% current performance
- 25% future potential
- 20% career achievements
- 15% audience demand
- 10% availability and durability

## Legacy Career Model

Suggested weights:

- 35% career legacy and achievements
- 25% continuing audience demand
- 20% post-career activity
- 15% Hall of Fame or historical recognition
- 5% market liquidity

Post-career activity may include coaching, broadcasting, ownership, business activity, endorsements, public appearances, and continued cultural relevance.

## Price behavior after retirement

A retirement event can cause a price decline, no material change, or an increase. The outcome depends on the recalculated legacy fundamentals and market demand.

TalentX should use:

- a nonzero price floor;
- limited opening volatility;
- a visible model-transition event;
- an official status source;
- a valuation range when confidence is low.

## Other career categories

### Music

Statuses may include:

- Active
- Recently active
- Touring
- On hiatus
- Group inactive
- Legacy artist
- Status under review

### Actors

Statuses may include:

- Active
- Upcoming project
- Currently filming
- On hiatus
- Retired — Legacy
- Status under review

### Creators

Statuses may include:

- Active
- Recently active
- On hiatus
- Legacy
- Status under review

## Safety rule

No profile may enter the Current market solely because an old database contains the person's name. Current-market eligibility requires a status source and verification timestamp in production.
