from django.contrib import admin

from .models import CapTableSettings, Investment, Round, StockHolding


@admin.register(CapTableSettings)
class CapTableSettingsAdmin(admin.ModelAdmin):
    list_display = ["org", "founders_pool_pct", "currency", "legal_name"]


@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "org",
        "date",
        "instrument",
        "pre_money_valuation",
        "post_money_valuation",
    ]
    list_filter = ["org", "instrument"]


@admin.register(Investment)
class InvestmentAdmin(admin.ModelAdmin):
    list_display = ["round", "membership", "holder", "amount", "date"]
    list_filter = ["org"]


@admin.register(StockHolding)
class StockHoldingAdmin(admin.ModelAdmin):
    list_display = ["org", "membership", "holder", "stock_class", "quantity"]
    list_filter = ["org"]
