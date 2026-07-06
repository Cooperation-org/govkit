from django.contrib import admin

from .models import Ballot, Vote


class BallotInline(admin.TabularInline):
    model = Ballot
    extra = 0
    autocomplete_fields = ("membership",)


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ("id", "org", "question", "opened_at", "closed_at")
    list_filter = ("org",)
    search_fields = ("question",)
    inlines = [BallotInline]


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ("id", "org", "vote", "membership", "choice", "cast_at")
    list_filter = ("org",)
    autocomplete_fields = ("membership",)
