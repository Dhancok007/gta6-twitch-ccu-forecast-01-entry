# GTA VI launch-day Twitch CCU forecaster

Entry for make.fun's `gta6-twitch-ccu-forecast-01`: forecast, hour by hour,
how many people watch Grand Theft Auto VI live on Twitch over the 24 UTC
hours of its release day (2026-11-19), as five quantiles per hour, given
everything recorded up to the end of the first hour.

## Run

```
MAKEFUN_INPUT_DIR=/input MAKEFUN_OUTPUT_DIR=/output python3 main.py
```

Reads every `*.in` case in the input directory (schema
`makefun/twitch-ccu-case/v1`) and writes one `<id>.out` per case:
`{"hours": [{"hour": 2, "q": [q05, q25, q50, q75, q95]}, ...]}` for hours
2–24. Python 3.13, standard library only, no network. Deterministic: two
runs on the same case are byte-identical. Runs in well under a second.

## Method

Everything is anchored on the case's observed first-hour print `p`.

1. **Analogue library.** Each comparable launch in the case is reduced to
   its release-day curve (hourly viewers, hours 1–24 of its `day_start`).
   A comparable joins the library only if its first hour was already live —
   hour 1 at least 5% of its own day's peak. That keeps rolling
   midnight-local launches (the shape of GTA VI's console release: New
   Zealand, then Europe, then the UK, then the US across the UTC day) and
   drops simultaneous-global unlocks, whose first hour sits near zero and
   whose day curve is a different animal entirely.
2. **Empirical quantiles.** For each forecast hour, the library's log
   ratios `ln(viewers_h / viewers_1)` are collected and their empirical
   quantiles at 0.05/0.25/0.5/0.75/0.95 are taken; the forecast is
   `p · exp(quantile)`. The spread is the spread real launches showed,
   widening over the day on its own.
3. **Stabilisers.** Neighbouring hours' log quantiles are pooled with
   weights 1-2-1, and the two tail quantiles are blended halfway toward a
   normal fit (median + z · sample standard deviation), so the late hours
   and the 5%/95% lines do not rest on one or two outlying launches.

## Validation

Leave-one-out over the 29 rolling-class launches in the kit's comparable
set (each launch forecast from its own first hour, library = the other 28,
scored with the contest's log-scale pinball loss): **0.3089**, against
0.3595 for the house persistence baseline on the same bed. The kit ships no
local grader, so this cross-validation over the comparables is the local
score; the server grades the practice cases.
