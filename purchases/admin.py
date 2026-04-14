from django.contrib import admin
from .models import Purchase, PurchaseItem, BuyerProfile, PurchaseEditLog
import csv
from django.http import HttpResponse
from django.contrib import messages, admin
from django.urls import reverse
from django.utils.html import format_html

@admin.register(PurchaseEditLog)
class PurchaseEditLogAdmin(admin.ModelAdmin):
    list_display = ("purchase", "action", "edited_by", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("purchase__isp_number", "edited_by__username", "note", "old_value", "new_value")

@admin.register(BuyerProfile)
class BuyerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "buyer_code")
    search_fields = ("user__username","user__first_name","user__last_name","buyer_code")

def export_selected_finalized_purchases(modeladmin, request, queryset):
        finalized_purchases = queryset.filter(workflow_status="finalized")
        skipped_count = queryset.count() - finalized_purchases.count()

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="finalized_purchases_export.csv"'

        writer = csv.writer(response)
        writer.writerow(["Product name", "SKU", "Retail price", "Cost price", "Location"])

        exported_rows = 0

        for purchase in finalized_purchases:
            for item in purchase.items.all():
                writer.writerow([
                    item.title,
                    item.sku,
                    item.retail_price,
                    item.unit_cost,
                    purchase.location,
                ])
                exported_rows += 1

        if skipped_count > 0:
            modeladmin.message_user(
                request,
                f"Exported {exported_rows} items from finalized purchases. {skipped_count} non-finalized purchase(s) were skipped.",
                level=messages.WARNING,
            )
        else:
            modeladmin.message_user(
                request,
                f"Exported {exported_rows} items from finalized purchases.",
                level=messages.SUCCESS,
            )

        return response

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    actions = [export_selected_finalized_purchases]

    list_display = (
        "isp_number",
        "buyer_initials",
        "seller_first_name",
        "seller_last_name",
        "purchase_total_amount",
        "reconciliation_status",
        "workflow_status",
        "created_at",
        "download_order_link",
    )
    search_fields = (
        "isp_number",
        "seller_first_name",
        "seller_last_name",
        "seller_phone",
        "seller_email",
    )

    def download_order_link(self, obj):
        if obj.workflow_status == "finalized":
            url = reverse("download_purchase_order", args=[obj.id])
            return format_html('<a href="{}" target="_blank">Download Full Order</a>', url)
        return "Only available for finalized purchases"

@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "purchase",
        "title",
        "quantity",
        "unit_cost",
        "retail_price",
        "line_total_cost",
        "created_at",
    )
    search_fields = (
        "sku",
        "title",
        "purchase__isp__number",
    )