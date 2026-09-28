"""GridWeave Auction & Energy Market Subsystem (Person 2).

Provides the complete market clearing, bid validation, allocation optimization,
critical load protection, explainable decision tracing, and fairness mechanisms.
"""
from gridweave.auction.constraints import (
    ConstraintValidator,
    ConstraintViolation,
    EmergencyPolicy,
    EmergencyPolicyType,
)
from gridweave.auction.engine import AuctionEngine, MarketPhase
from gridweave.auction.fairness import BuildingFairnessRecord, FairnessTracker, jains_fairness_index
from gridweave.auction.metrics import MarketMetrics, MarketMetricsCalculator
from gridweave.auction.result import DecisionTrace, MarketResult
from gridweave.auction.scoring import BidScorer, ScoreBreakdown
from gridweave.auction.strategies import (
    AllocationStrategy,
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
    PriorityAllocationStrategy,
    ProportionalAllocationStrategy,
    StrategyResult,
)
from gridweave.auction.validator import BidValidationError, BidValidator, ValidationReport

__all__ = [
    "AllocationStrategy",
    "AuctionEngine",
    "BidScorer",
    "BidValidationError",
    "BidValidator",
    "BuildingFairnessRecord",
    "ConstraintValidator",
    "ConstraintViolation",
    "DecisionTrace",
    "EmergencyPolicy",
    "EmergencyPolicyType",
    "FairnessTracker",
    "GreedyAllocationStrategy",
    "MarketMetrics",
    "MarketMetricsCalculator",
    "MarketPhase",
    "MarketResult",
    "OptimizedAllocationStrategy",
    "PriorityAllocationStrategy",
    "ProportionalAllocationStrategy",
    "ScoreBreakdown",
    "StrategyResult",
    "ValidationReport",
    "jains_fairness_index",
]
