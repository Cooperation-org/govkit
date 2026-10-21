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


class EntityType(models.TextChoices):
    C_CORP = "C_CORP", "C corporation"
    PBC = "PBC", "Public benefit corporation"
    LLC = "LLC", "LLC"
    OTHER = "OTHER", "Other"


class Scope(models.TextChoices):
    """What the cap table here covers: the whole company, or only the founders pool of a
    cap table kept somewhere else (then the rest is one "held outside" line)."""

    WHOLE_COMPANY = "WHOLE_COMPANY", "The whole company"
    POOL_ONLY = "POOL_ONLY", "The founders pool only"


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
    entity_type = models.CharField(max_length=8, choices=EntityType.choices, blank=True)
    incorporated = models.BooleanField(default=False)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.WHOLE_COMPANY)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"CapTableSettings({self.org.slug}, pool {self.founders_pool_pct}%)"


class Instrument(models.TextChoices):
    SAFE = "safe", "SAFE"
    PRICED = "priced", "Priced"
    NOTE = "note", "Convertible note"


class ConversionTiming(models.TextChoices):
    """OCF ConversionTimingType: is a SAFE's cap a pre-money or a post-money number."""

    POST_MONEY = "POST_MONEY", "Post-money"
    PRE_MONEY = "PRE_MONEY", "Pre-money"


class StockClassType(models.TextChoices):
    COMMON = "COMMON", "Common"
    PREFERRED = "PREFERRED", "Preferred"


class StockClass(OrgScoped):
    """A class of stock (OCF ``StockClass``): what the charter authorizes, at what par."""

    name = models.CharField(max_length=80)
    class_type = models.CharField(
        max_length=9, choices=StockClassType.choices, default=StockClassType.COMMON
    )
    # Blank until the charter is recorded; OCF then says "NOT APPLICABLE".
    initial_shares_authorized = models.PositiveBigIntegerField(null=True, blank=True)
    # Per share, in the cap table's currency. Blank = no par value.
    par_value = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    votes_per_share = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))
    # OCF: higher number = repaid first; classes that rank equally share a number.
    seniority = models.PositiveIntegerField(default=1)
    board_approval_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-seniority", "name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["org", "name"], name="captable_stockclass_unique_name"),
        ]

    def __str__(self):
        return f"StockClass({self.org.slug}, {self.name})"


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
    # SAFE and note terms, named as OCF's SAFE_CONVERSION / CONVERTIBLE_NOTE_CONVERSION.
    # Fractions as OCF writes them: a 20% discount is 0.2, 8% interest is 0.08.
    conversion_valuation_cap = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    conversion_discount = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1"))],
    )
    conversion_timing = models.CharField(
        max_length=10, choices=ConversionTiming.choices, default=ConversionTiming.POST_MONEY
    )
    conversion_mfn = models.BooleanField(default=False)
    # Investors in this round may buy into the next one (a pro rata right).
    pro_rata = models.BooleanField(default=False)
    # Notes only.
    interest_rate = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1"))],
    )
    maturity_date = models.DateField(null=True, blank=True)
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

    stock_class = models.ForeignKey(StockClass, on_delete=models.PROTECT, related_name="holdings")
    quantity = models.PositiveBigIntegerField()
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["stock_class__name", "-quantity", "id"]
        constraints = [
            models.CheckConstraint(
                check=(models.Q(membership__isnull=False) & models.Q(holder__isnull=True))
                | (models.Q(membership__isnull=True) & models.Q(holder__isnull=False)),
                name="captable_holding_one_holder",
            ),
        ]

    def __str__(self):
        return f"StockHolding({self.org.slug}, {self.quantity} {self.stock_class.name})"


class ValuationType(models.TextChoices):
    # OCF ValuationType has only 409A; a round's price is recorded here too and exported
    # in the manifest comments.
    FOUR_09A = "409A", "409A"
    ROUND = "ROUND", "Round price"


class Valuation(OrgScoped):
    """What one share of a class was worth on a date (OCF ``Valuation``)."""

    stock_class = models.ForeignKey(StockClass, on_delete=models.PROTECT, related_name="valuations")
    effective_date = models.DateField()
    price_per_share = models.DecimalField(
        max_digits=20, decimal_places=10, validators=[MinValueValidator(Decimal("0"))]
    )
    valuation_type = models.CharField(
        max_length=5, choices=ValuationType.choices, default=ValuationType.FOUR_09A
    )
    provider = models.CharField(max_length=200, blank=True)
    board_approval_date = models.DateField(null=True, blank=True)
    # The report stays in the team's own files; this points at it.
    report_url = models.URLField(max_length=500, blank=True)
    # The company's gross assets on that date (QSBS records).
    gross_assets = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date", "-id"]

    def __str__(self):
        return f"Valuation({self.org.slug}, {self.stock_class.name}, {self.effective_date})"


class FranchiseTaxYear(OrgScoped):
    """The inputs a Delaware franchise tax figure for one tax year was computed from.

    Authorized shares and par values come from the stock classes; what the company
    states per year is its gross assets (Form 1120 Schedule L total assets) and, when it
    differs from the stock of record, its issued shares.
    """

    year = models.PositiveIntegerField()
    gross_assets = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    # Blank = the stock of record's total.
    issued_shares = models.PositiveBigIntegerField(null=True, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year"]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "year"], name="captable_franchisetax_one_per_year"
            ),
        ]

    def __str__(self):
        return f"FranchiseTaxYear({self.org.slug}, {self.year})"


class EventKind(models.TextChoices):
    ADD = "add", "Added"
    UPDATE = "update", "Changed"
    DELETE = "delete", "Removed"


class CapTableEvent(models.Model):
    """
    One change to the cap table. APPEND-ONLY, like apps.orgs.models.ChecklistEvent:
    every add, change and removal in apps.captable.services writes one, inside the same
    transaction, with the row before and after. A saved event is never changed or deleted.
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="cap_table_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    kind = models.CharField(max_length=6, choices=EventKind.choices)
    object_type = models.CharField(max_length=32)
    object_id = models.BigIntegerField(null=True, blank=True)
    label = models.CharField(max_length=255, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["org", "-at"])]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("A cap table event is never changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("A cap table event is never deleted.")

    def __str__(self):
        return f"CapTableEvent({self.org_id}, {self.kind} {self.object_type} {self.object_id})"
