"""changeledger — Cost per accepted change calculator.

No commercial tool computes this metric today. This tool does.

The formula:
    cost per accepted change = (model + infra + human engineering + review + rework) / accepted changes

"Accepted changes" means merged PRs minus reverts and hotfixes within a 14-day window.
This is the denominator your dashboard is missing.
"""

__version__ = "0.1.0"
