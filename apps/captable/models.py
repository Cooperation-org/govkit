"""
The cap table: the whole company, the pie inside it, and the money that bought in.

Shape (projects repo, Internal/10-20-2026-workersvc-cap-table-model.md):

* A FOUNDERS POOL is a set percent of the company. The org's pie runs inside it, so a
  person's percent of the company is their pie share times the pool percent. Nothing
  here stores a founder's share: it is always derived from apps.pie.services.compute_pie.
* The rest is the INVESTOR TRANCHE, sold round by round. An investment buys
  ``amount / post-money valuation`` of the company. What is not sold yet shows as its
  own line.
* STOCK HOLDINGS are the legal shares a few holders of record keep. They are a separate
  record, never added into the economic 100%.

Economics only. Governance stays with apps.orgs.weighting (work weight), untouched.

Field names follow the Open Cap Format where it has one (apps/captable/ocf.py maps the
rest onto it).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.orgs.models import ExternalHolder, Membership, Org
from apps.orgs.scoping import OrgScoped

DEFAULT_FOUNDERS_POOL_PCT = Decimal("55.00")


class CapTableSettings(models.Model):
    """The one number that frames the table: how much of the company the pool is."""

    org = models.OneToOneField(Org, on_delete=models.CASCADE, related_name="cap_table_settings")
    founders_pool_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=DEFAULT_FOUNDERS_POOL_PCT,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    # ISO 4217, as OCF Monetary.currency.
    currency = models.CharField(max_length=3, default="USD")
    # The legal entity, as OCF ``Issuer``. Blank until the entity is formed.
    legal_name = models.CharField(max_length=255, blank=True)
    formation_date = models.DateField(null=True, blank=True)
    # ISO 3166-1 alpha-2 and the subdivision code (e.g. US, DE).
    country_of_formation = models.CharField(max_length=2, blank=True)
    country_subdivision_of_formation = models.CharField(max_length=6, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"CapTableSettings({self.org.slug}, pool {self.founders_pool_pct}%)"


class Instrument(models.TextChoices):
    SAFE = "safe", "SAFE"
    PRICED = "priced", "Priced"


class Round(OrgScoped):
    """One financing (OCF ``Financing``): a name, a date, an instrument, a valuation.

    A post-money SAFE states its post-money valuation; a priced round usually states its
    pre-money. Either is enough: the other is derived from what the round raised, which
    is always the sum of its investments, never a typed total.
    """

    name = models.CharField(max_length=120)
    date = models.DateField()
    instrument = models.CharField(max_length=8, choices=Instrument.choices, default=Instrument.SAFE)
    pre_money_valuation = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    post_money_valuation = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    # Priced rounds only: what one share cost, so an export can state a share count.
    price_per_share = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(pre_money_valuation__isnull=False)
                | models.Q(post_money_valuation__isnull=False),
                name="captable_round_has_valuation",
            ),
        ]

    def __str__(self):
        return f"Round({self.org.slug}, {self.name}, {self.date})"


class _Held(OrgScoped):
    """Held by exactly one of: a member of the org, or an outside holder."""

    membership = models.ForeignKey(
        Membership, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    holder = models.ForeignKey(
        ExternalHolder, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    @property
    def holder_label(self) -> str:
        if self.membership_id:
            return self.membership.user.get_short_name()
        return self.holder.display_name


class Investment(_Held):
    """Money one holder put into one round (OCF ``investment_amount``)."""

    round = models.ForeignKey(Round, on_delete=models.PROTECT, related_name="investments")
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    date = models.DateField(null=True, blank=True, help_text="Blank means the round's date.")

    class Meta:
        ordering = ["round__date", "round_id", "id"]
        constraints = [
            models.CheckConstraint(
                check=(models.Q(membership__isnull=False) & models.Q(holder__isnull=True))
                | (models.Q(membership__isnull=True) & models.Q(holder__isnull=False)),
                name="captable_investment_one_holder",
            ),
        ]

    def __str__(self):
        return f"Investment({self.round}, {self.amount})"


class StockHolding(_Held):
    """Legal shares of record (OCF ``TX_STOCK_ISSUANCE``): who holds how many of what."""

    stock_class = models.CharField(max_length=80, default="Common")
    quantity = models.PositiveBigIntegerField()
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["stock_class", "-quantity", "id"]
        constraints = [
            models.CheckConstraint(
                check=(models.Q(membership__isnull=False) & models.Q(holder__isnull=True))
                | (models.Q(membership__isnull=True) & models.Q(holder__isnull=False)),
                name="captable_holding_one_holder",
            ),
        ]

    def __str__(self):
        return f"StockHolding({self.org.slug}, {self.quantity} {self.stock_class})"
