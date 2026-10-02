from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean


@dataclass
class Transaction:
    account_id: str
    merchant: str
    amount: float
    currency: str
    occurred_at: datetime
    country: str


@dataclass
class AccountProfile:
    account_id: str
    home_country: str
    average_purchase: float
    trusted_merchants: set[str]
    last_seen_at: datetime


@dataclass
class RiskSignal:
    name: str
    score: float
    reason: str


def normalize_currency_code(currency: str) -> str:
    return currency.strip().upper()


def convert_to_usd(amount: float, currency: str, rates: dict[str, float]) -> float:
    normalized = normalize_currency_code(currency)
    if normalized == "USD":
        return amount
    if normalized not in rates:
        raise ValueError(f"Missing exchange rate for {normalized}")
    return amount * rates[normalized]


def recent_velocity(transactions: list[Transaction], now: datetime, window_minutes: int = 30) -> int:
    window_start = now - timedelta(minutes=window_minutes)
    return sum(1 for transaction in transactions if transaction.occurred_at >= window_start)


def average_amount(transactions: list[Transaction], rates: dict[str, float]) -> float:
    if not transactions:
        return 0.0

    converted = [
        convert_to_usd(transaction.amount, transaction.currency, rates)
        for transaction in transactions
    ]
    return mean(converted)


class RiskScorer:
    def __init__(self, rates: dict[str, float], velocity_limit: int = 6):
        self.rates = rates
        self.velocity_limit = velocity_limit

    def amount_signal(self, transaction: Transaction, profile: AccountProfile) -> RiskSignal:
        amount_usd = convert_to_usd(transaction.amount, transaction.currency, self.rates)
        if profile.average_purchase <= 0:
            return RiskSignal("amount", 0.2, "No purchase history is available")

        ratio = amount_usd / profile.average_purchase
        if ratio >= 5:
            return RiskSignal("amount", 0.9, "Purchase is far above the account average")
        if ratio >= 2:
            return RiskSignal("amount", 0.5, "Purchase is moderately above the account average")
        return RiskSignal("amount", 0.1, "Purchase amount is close to the account average")

    def merchant_signal(self, transaction: Transaction, profile: AccountProfile) -> RiskSignal:
        merchant_key = transaction.merchant.strip().lower()
        trusted = {merchant.strip().lower() for merchant in profile.trusted_merchants}
        if merchant_key in trusted:
            return RiskSignal("merchant", 0.05, "Merchant has been seen before")
        return RiskSignal("merchant", 0.4, "Merchant is new for this account")

    def location_signal(self, transaction: Transaction, profile: AccountProfile) -> RiskSignal:
        if transaction.country == profile.home_country:
            return RiskSignal("location", 0.05, "Transaction country matches the account profile")
        return RiskSignal("location", 0.6, "Transaction country differs from the account profile")

    def velocity_signal(self, history: list[Transaction], now: datetime) -> RiskSignal:
        count = recent_velocity(history, now)
        if count > self.velocity_limit:
            return RiskSignal("velocity", 0.85, "Recent transaction count exceeds the velocity limit")
        if count > self.velocity_limit // 2:
            return RiskSignal("velocity", 0.35, "Recent transaction count is elevated")
        return RiskSignal("velocity", 0.05, "Recent transaction count is normal")

    def score(
        self,
        transaction: Transaction,
        profile: AccountProfile,
        history: list[Transaction],
        now: datetime,
    ) -> tuple[float, list[RiskSignal]]:
        signals = [
            self.amount_signal(transaction, profile),
            self.merchant_signal(transaction, profile),
            self.location_signal(transaction, profile),
            self.velocity_signal(history, now),
        ]

        weighted_score = (
            signals[0].score * 0.35
            + signals[1].score * 0.2
            + signals[2].score * 0.25
            + signals[3].score * 0.2
        )
        return min(weighted_score, 1.0), signals


class ReviewQueue:
    def __init__(self, manual_review_threshold: float = 0.65):
        self.manual_review_threshold = manual_review_threshold
        self.pending: list[tuple[Transaction, float, list[RiskSignal]]] = []

    def submit(self, transaction: Transaction, score: float, signals: list[RiskSignal]) -> bool:
        if score < self.manual_review_threshold:
            return False

        self.pending.append((transaction, score, signals))
        return True

    def drain_highest_risk(self, limit: int = 10) -> list[tuple[Transaction, float, list[RiskSignal]]]:
        ordered = sorted(self.pending, key=lambda item: item[1], reverse=True)
        selected = ordered[:limit]
        self.pending = ordered[limit:]
        return selected


def evaluate_transaction(
    transaction: Transaction,
    profile: AccountProfile,
    history: list[Transaction],
    rates: dict[str, float],
    queue: ReviewQueue,
    now: datetime,
) -> dict[str, object]:
    scorer = RiskScorer(rates)
    score, signals = scorer.score(transaction, profile, history, now)
    queued = queue.submit(transaction, score, signals)

    return {
        "account_id": transaction.account_id,
        "risk_score": round(score, 3),
        "queued_for_review": queued,
        "signals": [signal.name for signal in signals if signal.score >= 0.3],
    }