"""BR-level win/tie/loss classification from ISK efficiency.

Win metric for the BR: ISK efficiency = our_destroyed / (our_destroyed + our_lost)
  win  if efficiency >= 0.52
  tie  if 0.48 <= efficiency < 0.52
  loss if efficiency < 0.48
  None if denominator is 0
"""

from __future__ import annotations

WIN_THRESHOLD = 0.52
TIE_THRESHOLD = 0.48  # lower boundary of tie band


def classify_br_result(our_destroyed: float, our_lost: float) -> str | None:
    """Classify a BR as win/tie/loss based on ISK efficiency.

    ISK efficiency = our_destroyed / (our_destroyed + our_lost)
    win  if efficiency >= 0.52
    tie  if 0.48 <= efficiency < 0.52
    loss if efficiency < 0.48
    None if denominator is 0
    """
    denom = our_destroyed + our_lost
    if denom == 0.0:
        return None
    eff = our_destroyed / denom
    if eff >= WIN_THRESHOLD:
        return "win"
    if eff >= TIE_THRESHOLD:
        return "tie"
    return "loss"
