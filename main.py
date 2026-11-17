#!/usr/bin/env python3
"""Forecast a launch day's Twitch audience, hour by hour, from its first hour.

The program reads one case file per run from the input directory (every
``*.in`` file there, in name order) and writes one forecast per case to the
output directory.  A case carries the day's first-hour viewer count (the
anchor) and a library of past launches with their full hourly curves.

Method
------
Everything is anchored on the observed first-hour print ``p``.  From the
case's own comparable launches we keep the ones whose release day was
already "live" in its first hour -- hour 1 at least 5% of that day's peak --
because a rolling midnight launch (the shape of this contest's final) is
already drawing a real audience at 00:00 UTC, while a simultaneous global
unlock sits near zero until its single start time and follows a completely
different curve.  For each forecast hour ``h`` we collect, across the kept
launches, the log ratio ``ln(viewers_h / viewers_1)`` and take its empirical
quantiles at the five required levels; the forecast is ``p`` times those
quantiles exponentiated.  The spread is therefore the spread the comparable
launches actually showed, widening over the day on its own, instead of a
parametric guess.  Neighbouring hours' quantiles are pooled (weights 1-2-1)
and the two tail quantiles are blended halfway toward a normal fit, so a
small library's noisiest estimates -- the late hours and the 5%/95% lines --
do not rest on one or two launches.

The program is deterministic: no randomness, no clock, no network, standard
library only.  Two runs on the same case write byte-identical output.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)

# A comparable launch joins the analogue library when its release-day first
# hour already held at least this share of its own day's peak viewers.
LIVE_SHARE_MIN = 0.05

# Fallbacks, in order, if a threshold leaves the library empty.
LIVE_SHARE_LADDER = (0.05, 0.02, 0.0)

# Normal quantiles, used only by the last-resort fallback below.
Z = (-1.644854, -0.674490, 0.0, 0.674490, 1.644854)

MAX_Q = 1_000_000_000.0


def parse_instant(text):
    """Parse an ISO 8601 UTC timestamp into an aware datetime."""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def release_day_curve(comparable):
    """Map hour-of-release-day (1..24) -> viewers for one comparable launch.

    Points are the comparable's hourly averages; a point starting exactly
    ``k`` whole hours after its ``day_start`` belongs to hour ``k + 1``.
    Returns an empty dict when the comparable has no usable release day.
    """
    try:
        start = parse_instant(comparable["day_start"])
    except (KeyError, TypeError, ValueError):
        return {}
    curve = {}
    for point in comparable.get("hourly") or []:
        viewers = point.get("viewers")
        if viewers is None or viewers <= 0:
            continue
        try:
            offset = (parse_instant(point["from"]) - start).total_seconds() / 3600.0
        except (KeyError, TypeError, ValueError):
            continue
        hour = int(round(offset)) + 1
        if abs(offset - round(offset)) < 1e-6 and 1 <= hour <= 24 and hour not in curve:
            curve[hour] = float(viewers)
    return curve


def build_library(comparables):
    """Release-day curves of the analogue launches, loosest threshold last."""
    curves = []
    for comparable in comparables or []:
        curve = release_day_curve(comparable)
        if curve.get(1, 0.0) > 0 and len(curve) >= 12:
            curves.append(curve)
    for threshold in LIVE_SHARE_LADDER:
        kept = [c for c in curves if c[1] / max(c.values()) >= threshold]
        if kept:
            return kept
    return []


def empirical_quantile(sorted_values, tau):
    """Quantile of a sorted sample, linear interpolation between points."""
    count = len(sorted_values)
    if count == 1:
        return sorted_values[0]
    position = tau * (count - 1)
    low = int(math.floor(position))
    high = min(low + 1, count - 1)
    fraction = position - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * fraction


def hour_log_ratios(library, hour):
    """Sorted log ratios ln(viewers_hour / viewers_1) across the library."""
    return sorted(
        math.log(curve[hour] / curve[1]) for curve in library if hour in curve
    )


def hour_quantiles(library, hour, anchor):
    """The five forecast quantiles for one hour, as viewer counts.

    Two stabilisers on top of the raw empirical quantiles, both aimed at
    the late hours, where a sample of ~30 launches estimates each hour in
    isolation and the extreme quantiles rest on one or two launches:

    - the hour's log quantiles are averaged with its neighbours' (weights
      1-2-1 over hours h-1, h, h+1 inside the forecast range), pooling the
      sample across the day's smooth shape;
    - the two tail quantiles are blended halfway toward a normal fit
      (median plus z times the sample's own standard deviation), so one
      outlying launch cannot set the 5% or 95% line on its own.
    """
    own = hour_log_ratios(library, hour)
    if not own:
        # No analogue launch has this hour: hold the anchor flat.
        return [anchor] * len(LEVELS)
    blended = [0.0] * len(LEVELS)
    weight_sum = 0.0
    for neighbour, weight in ((hour - 1, 1.0), (hour, 2.0), (hour + 1, 1.0)):
        if not 2 <= neighbour <= 24:
            continue
        sample = hour_log_ratios(library, neighbour)
        if not sample:
            continue
        for index, tau in enumerate(LEVELS):
            blended[index] += weight * empirical_quantile(sample, tau)
        weight_sum += weight
    blended = [total / weight_sum for total in blended]
    if len(own) > 1:
        mean = sum(own) / len(own)
        deviation = math.sqrt(sum((x - mean) ** 2 for x in own) / len(own))
        median = blended[2]
        for index in (0, len(LEVELS) - 1):
            normal_fit = median + Z[index] * deviation
            blended[index] = 0.5 * blended[index] + 0.5 * normal_fit
    values = []
    for log_q in blended:
        q = anchor * math.exp(log_q)
        values.append(min(max(q, 1e-9), MAX_Q))
    return values


def fallback_quantiles(hour, anchor):
    """Persistence with a widening normal spread, if no library exists."""
    spread = 0.15 * math.sqrt(max(hour - 1, 0))
    return [min(anchor * math.exp(z * spread), MAX_Q) for z in Z]


def first_hour_print(case):
    """Hour 1's viewer count from the case's ``known`` row (list or dict)."""
    known = case.get("known")
    if isinstance(known, list):
        known = known[0] if known else {}
    viewers = (known or {}).get("viewers") or 0
    return max(float(viewers), 1.0)


def forecast_case(case):
    """Build the forecast object for one parsed case."""
    anchor = first_hour_print(case)
    library = build_library(case.get("comparables"))
    rows = []
    for entry in sorted(case.get("forecast") or [], key=lambda r: r["hour"]):
        hour = int(entry["hour"])
        if library:
            values = hour_quantiles(library, hour, anchor)
        else:
            values = fallback_quantiles(hour, anchor)
        # Round first, then enforce the non-decreasing order on the rounded
        # numbers, so the written row can never step down by a cent.
        rounded = []
        for value in values:
            value = round(value, 2)
            if rounded and value < rounded[-1]:
                value = rounded[-1]
            rounded.append(value)
        rows.append({"hour": hour, "q": rounded})
    return {"hours": rows}


def main(argv):
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(output_dir, exist_ok=True)
    names = sorted(name for name in os.listdir(input_dir) if name.endswith(".in"))
    for name in names:
        with open(os.path.join(input_dir, name), "r", encoding="utf-8") as handle:
            case = json.load(handle)
        forecast = forecast_case(case)
        out_name = name[: -len(".in")] + ".out"
        with open(os.path.join(output_dir, out_name), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(forecast, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
