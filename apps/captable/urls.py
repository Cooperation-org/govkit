from django.urls import path

from . import views

app_name = "captable"

urlpatterns = [
    path("", views.index, name="index"),
    path("settings/", views.save_settings, name="save_settings"),
    path("rounds/add/", views.add_round, name="add_round"),
    path("rounds/<int:pk>/", views.save_round, name="save_round"),
    path("rounds/<int:pk>/delete/", views.delete_round, name="delete_round"),
    path("investments/add/", views.add_investment, name="add_investment"),
    path("investments/<int:pk>/", views.save_investment, name="save_investment"),
    path("investments/<int:pk>/delete/", views.delete_investment, name="delete_investment"),
    path("stock/add/", views.add_holding, name="add_holding"),
    path("stock/<int:pk>/", views.save_holding, name="save_holding"),
    path("stock/<int:pk>/delete/", views.delete_holding, name="delete_holding"),
    path("classes/add/", views.add_stock_class, name="add_stock_class"),
    path("classes/<int:pk>/", views.save_stock_class, name="save_stock_class"),
    path("classes/<int:pk>/delete/", views.delete_stock_class, name="delete_stock_class"),
    path("valuations/add/", views.add_valuation, name="add_valuation"),
    path("valuations/<int:pk>/", views.save_valuation, name="save_valuation"),
    path("valuations/<int:pk>/delete/", views.delete_valuation, name="delete_valuation"),
    path("franchise-tax/", views.save_franchise_tax, name="save_franchise_tax"),
]
