"""PSYGRID Option Engine.

Intraday intelligence and option-trading decision engine for NIFTY and
BANKNIFTY. Consumes the external PSYGRID live-data API and produces
strictly-typed TRADE_READY / NO_TRADE signals. See docs/ARCHITECTURE.md.

This package places no broker orders (docs/SAFETY.md).
"""

__version__ = "0.1.0"
