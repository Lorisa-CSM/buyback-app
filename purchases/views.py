from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import PurchaseForm, PurchaseItemsForm, PurchaseItemFormSet, BulkCardForm
from .models import Purchase, PurchaseItem


def get_next_isp_number(buyer_code):
    buyer_code = buyer_code.upper().strip()
    year_code = datetime.now().strftime("%y")
    prefix = f"{year_code}{buyer_code}-"

    existing_isps = Purchase.objects.filter(
        isp_number__startswith=prefix
    ).values_list("isp_number", flat=True)

    max_number = 0

    for isp in existing_isps:
        try:
            seq = int(str(isp).split("-")[-1])
            if seq > max_number:
                max_number = seq
        except (ValueError, IndexError):
            continue

    return f"{prefix}{max_number + 1:04d}"


def get_next_item_sequence(purchase):
    max_seq = 0

    for sku in purchase.items.values_list("sku", flat=True):
        try:
            seq = int(str(sku).split("-")[-1])
            if seq > max_seq:
                max_seq = seq
        except (ValueError, IndexError):
            continue

    return max_seq + 1


def recalculate_purchase_totals(purchase):
    purchase = Purchase.objects.prefetch_related("items").get(id=purchase.id)
    items = purchase.items.all()

    allocated_total = sum(
        (item.line_total_cost or Decimal("0.00")) for item in items
    )
    difference = (purchase.purchase_total_amount or Decimal("0.00")) - allocated_total

    purchase.allocation_total_amount = allocated_total
    purchase.allocation_difference = difference

    if difference == Decimal("0.00"):
        purchase.reconciliation_status = "balanced"
    elif difference > Decimal("0.00"):
        purchase.reconciliation_status = "under"
    else:
        purchase.reconciliation_status = "over"

    purchase.save(update_fields=[
        "allocation_total_amount",
        "allocation_difference",
        "reconciliation_status",
    ])


def get_user_profile_flags(user):
    profile = getattr(user, "buyerprofile", None)

    return {
        "profile": profile,
        "buyer_code": profile.buyer_code.upper().strip() if profile else "",
        "can_view_reports": profile.can_view_reports if profile else False,
        "can_edit_all_purchases": profile.can_edit_all_purchases if profile else False,
        "can_reopen_purchases": profile.can_reopen_purchases if profile else False,
    }


def can_edit_purchase(access, purchase):
    return (
        access["profile"]
        and (
            purchase.buyer_initials == access["buyer_code"]
            or access["can_edit_all_purchases"]
        )
    )


def can_view_reports(access):
    return bool(access["can_view_reports"])


def can_reopen_purchase(access):
    return bool(access["can_reopen_purchases"])


def log_purchase_edit(
    purchase,
    user,
    action,
    field_name="",
    old_value="",
    new_value="",
    note="",
):
    purchase.edit_logs.create(
        edited_by=user,
        action=action,
        field_name=field_name,
        old_value=str(old_value or ""),
        new_value=str(new_value or ""),
        note=note or "",
    )


@login_required
def purchase_home(request):
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if request.method == "POST":
        form = PurchaseForm(request.POST, user=request.user)

        if form.is_valid():
            if not profile:
                messages.error(
                    request,
                    "No buyer profile is assigned to this user. Please contact an administrator."
                )
                return redirect("resume_purchase")

            purchase = form.save(commit=False)
            buyer_code = access["buyer_code"]

            purchase.buyer_initials = buyer_code
            purchase.isp_number = get_next_isp_number(buyer_code)
            purchase.allocation_total_amount = Decimal("0.00")
            purchase.allocation_difference = purchase.purchase_total_amount
            purchase.reconciliation_status = "under"
            purchase.workflow_status = "draft"
            purchase.save()

            log_purchase_edit(
                purchase=purchase,
                user=request.user,
                action="purchase_created",
                note="Purchase created.",
            )

            messages.success(request, "Purchase saved successfully.")
            return redirect("purchase_detail", purchase_id=purchase.id)
    else:
        form = PurchaseForm(user=request.user)

    return render(request, "purchases/purchase_form.html", {"form": form})


@login_required
def purchase_detail(request, purchase_id):
    purchase = get_object_or_404(
     Purchase.objects.prefetch_related("items", "edit_logs__edited_by__buyerprofile"),
         id=purchase_id
    )

    access = get_user_profile_flags(request.user)

    total_cost = sum((item.line_total_cost or Decimal("0.00")) for item in purchase.items.all())
    total_retail = sum(
        (item.quantity or 0) * (item.retail_price or Decimal("0.00"))
        for item in purchase.items.all()
    )
    total_profit = total_retail - total_cost
    margin_percent = ((total_profit / total_retail) * 100) if total_retail > 0 else None

    edit_logs = purchase.edit_logs.all()[:25]

    for log in edit_logs:
        if log.action == "purchase_created":
            log.description = "Created purchase"
        elif log.action == "item_added":
            log.description = f"Added product {log.new_value}"
        elif log.action == "bulk_cards_added":
            log.description = f"Added bulk cards: {log.new_value}"
        elif log.action == "bulk_items_saved":
            log.description = "Saved bulk product changes"
        elif log.action == "item_deleted":
            log.description = f"Deleted product {log.old_value}"
        elif log.action == "item_updated":
            log.description = f"Updated product from {log.old_value} to {log.new_value}"
        elif log.action == "purchase_header_updated":
            log.description = "Updated purchase details"
        elif log.action == "purchase_finalized":
            log.description = "Finalized purchase"
        elif log.action == "purchase_reopened":
            log.description = f"Reopened purchase: {log.note}" if log.note else "Reopened purchase"
        elif log.action == "purchase_exported":
            log.description = "Exported purchase to CSV"
        else:
            log.description = log.note or log.action.replace("_", " ").title()

    return render(request, "purchases/purchase_detail.html", {
        "purchase": purchase,
        "total_cost": total_cost,
        "total_retail": total_retail,
        "total_profit": total_profit,
        "margin_percent": margin_percent,
        "buyer": access["profile"],
        "access": access,
        "edit_logs": edit_logs,
    })


@login_required
def add_purchase_item(request, purchase_id):
    purchase = get_object_or_404(Purchase, id=purchase_id)
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("resume_purchase")

    if purchase.workflow_status == "finalized" and not access["can_edit_all_purchases"]:
        return redirect("purchase_detail", purchase_id=purchase.id)

    if not can_edit_purchase(access, purchase):
        messages.error(request, "You do not have permission to edit this purchase.")
        return redirect("purchase_detail", purchase_id=purchase.id)

    if request.method == "POST":
        form = PurchaseItemsForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.purchase = purchase

            next_seq = get_next_item_sequence(purchase)
            item.sku = f"{purchase.isp_number}-{next_seq:02d}"
            item.line_total_cost = item.quantity * item.unit_cost
            item.save()

            purchase.refresh_from_db()
            recalculate_purchase_totals(purchase)

            log_purchase_edit(
                purchase=purchase,
                user=request.user,
                action="item_added",
                field_name="item",
                new_value=f"{item.title}, qty={item.quantity}, cost={item.unit_cost}, retail ${item.retail.price}",
            )

            messages.success(request, "Product added successfully.")
            return redirect("purchase_detail", purchase_id=purchase.id)
    else:
        form = PurchaseItemsForm()

    return render(request, "purchases/add_item.html", {
        "purchase": purchase,
        "form": form,
    })


@login_required
def add_bulk_cards(request, purchase_id):
    purchase = get_object_or_404(Purchase, id=purchase_id)
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("resume_purchase")

    if purchase.workflow_status == "finalized" and not access["can_edit_all_purchases"]:
        return redirect("purchase_detail", purchase_id=purchase.id)

    if not can_edit_purchase(access, purchase):
        messages.error(request, "You do not have permission to edit this purchase.")
        return redirect("purchase_detail", purchase_id=purchase.id)

    if request.method == "POST":
        form = BulkCardForm(request.POST)

        if form.is_valid():
            total_cost = form.cleaned_data["total_cost"]

            retail_price = (total_cost / Decimal("0.65")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP
            )

            next_seq = get_next_item_sequence(purchase)

            item = PurchaseItem.objects.create(
                purchase=purchase,
                sku=f"{purchase.isp_number}-{next_seq:02d}",
                title="Bulk Cards",
                quantity=1,
                unit_cost=total_cost,
                retail_price=retail_price,
                line_total_cost=total_cost,
            )

            purchase.refresh_from_db()
            recalculate_purchase_totals(purchase)

            log_purchase_edit(
                purchase=purchase,
                user=request.user,
                action="bulk_cards_added",
                field_name="item",
                new_value=f"Bulk Cards, qty=1, cost={total_cost}, retail ${retail_price}",
            )

            messages.success(request, "Bulk cards added successfully.")
            return redirect("purchase_detail", purchase_id=purchase.id)
    else:
        form = BulkCardForm()

    return render(request, "purchases/add_bulk_cards.html", {
        "purchase": purchase,
        "form": form,
    })


@login_required
def add_purchase_items_bulk(request, purchase_id):
    purchase = get_object_or_404(
        Purchase.objects.prefetch_related("items"),
        id=purchase_id
    )
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("resume_purchase")

    if purchase.workflow_status == "finalized" and not access["can_edit_all_purchases"]:
        return redirect("purchase_detail", purchase_id=purchase.id)

    if not can_edit_purchase(access, purchase):
        messages.error(request, "You do not have permission to edit this purchase.")
        return redirect("purchase_detail", purchase_id=purchase.id)

    queryset = PurchaseItem.objects.filter(purchase=purchase).order_by("id")

    if request.method == "POST":
        formset = PurchaseItemFormSet(request.POST, queryset=queryset)

        if formset.is_valid():
            next_seq = get_next_item_sequence(purchase)
            items = formset.save(commit=False)

            deleted_descriptions = []
            for obj in formset.deleted_objects:
                deleted_descriptions.append(
                    f"{obj.sku} | {obj.title} | qty={obj.quantity} | cost={obj.unit_cost}"
                )
                obj.delete()

            saved_descriptions = []
            for item in items:
                is_blank_row = (
                not str(item.title or "").strip()
                and not item.quantity
                and not item.unit_cost
                and not item.retail_price
                )

                if is_blank_row:
                    continue

                item.purchase = purchase

                if not item.pk:
                    item.sku = f"{purchase.isp_number}-{next_seq:02d}"
                    next_seq += 1

                item.line_total_cost = item.quantity * item.unit_cost
                item.save()
                saved_descriptions.append(
                    f"{item.sku} | {item.title} | qty={item.quantity} | cost={item.unit_cost}"
                )

            purchase.refresh_from_db()
            recalculate_purchase_totals(purchase)

            if saved_descriptions:
                log_purchase_edit(
                    purchase=purchase,
                    user=request.user,
                    action="bulk_items_saved",
                    note="Bulk item save completed.",
                    new_value=" || ".join(saved_descriptions),
                )

            for deleted_item in deleted_descriptions:
                log_purchase_edit(
                    purchase=purchase,
                    user=request.user,
                    action="item_deleted",
                    field_name="item",
                    old_value=deleted_item,
                )

            messages.success(request, "Products saved successfully.")
            return redirect("purchase_detail", purchase_id=purchase.id)
    else:
        formset = PurchaseItemFormSet(queryset=queryset)

    items = purchase.items.all()
    total_retail = sum(
        (item.quantity or 0) * (item.retail_price or Decimal("0.00"))
        for item in items
    )
    total_cost = sum((item.line_total_cost or Decimal("0.00")) for item in items)
    total_profit = total_retail - total_cost
    avg_margin = ((total_profit / total_retail) * 100) if total_retail > 0 else None

    return render(request, "purchases/add_items_bulk.html", {
        "purchase": purchase,
        "formset": formset,
        "total_retail": total_retail,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "avg_margin": avg_margin,
    })


@login_required
def finalize_purchase(request, purchase_id):
    purchase = get_object_or_404(Purchase, id=purchase_id)
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("resume_purchase")

    if not can_edit_purchase(access, purchase):
        messages.error(request, "You do not have permission to finalize this purchase.")
        return redirect("purchase_detail", purchase_id=purchase.id)

    if (
        request.method == "POST"
        and purchase.reconciliation_status == "balanced"
        and purchase.items.exists()
    ):
        purchase.workflow_status = "finalized"
        purchase.finalized_at = timezone.now()
        purchase.save()

        log_purchase_edit(
            purchase=purchase,
            user=request.user,
            action="purchase_finalized",
            note="Purchase finalized.",
        )

    return redirect("purchase_detail", purchase_id=purchase.id)


@login_required
def delete_purchase_item(request, purchase_id, item_id):
    purchase = get_object_or_404(Purchase, id=purchase_id)
    item = get_object_or_404(PurchaseItem, id=item_id, purchase=purchase)
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("resume_purchase")

    if purchase.workflow_status == "finalized" and not access["can_edit_all_purchases"]:
        return redirect("purchase_detail", purchase_id=purchase.id)

    if not can_edit_purchase(access, purchase):
        messages.error(request, "You do not have permission to delete from this purchase.")
        return redirect("purchase_detail", purchase_id=purchase.id)

    if request.method == "POST":
        deleted_item = f"{item.title}, qty={item.quantity}, cost={item.unit_cost}, retail ${item.retail_price}",
        item.delete()
        purchase.refresh_from_db()
        recalculate_purchase_totals(purchase)

        log_purchase_edit(
            purchase=purchase,
            user=request.user,
            action="item_deleted",
            field_name="item",
            old_value=deleted_item,
        )

    return redirect("purchase_detail", purchase_id=purchase.id)


@login_required
def edit_purchase_item(request, purchase_id, item_id):
    purchase = get_object_or_404(Purchase, id=purchase_id)
    item = get_object_or_404(PurchaseItem, id=item_id, purchase=purchase)
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("resume_purchase")

    if purchase.workflow_status == "finalized" and not access["can_edit_all_purchases"]:
        return redirect("purchase_detail", purchase_id=purchase.id)

    if not can_edit_purchase(access, purchase):
        messages.error(request, "You do not have permission to edit this purchase.")
        return redirect("purchase_detail", purchase_id=purchase.id)

    if request.method == "POST":
        original_item = {
            "sku": item.sku,
            "title": item.title,
            "quantity": item.quantity,
            "unit_cost": item.unit_cost,
        }

        form = PurchaseItemsForm(request.POST, instance=item)
        if form.is_valid():
            item = form.save(commit=False)
            item.purchase = purchase
            item.line_total_cost = item.quantity * item.unit_cost
            item.save()

            purchase.refresh_from_db()
            recalculate_purchase_totals(purchase)

            old_item = (
                f"{original_item['title']}, {original_item['quantity']},"
                f"cost={original_item['unit_cost']}"
            )
            new_item = (
                f"{item.title}, qty={item.quantity}, "
                f"cost={item.unit_cost}, retail ${item.retail_price}"
            )
            
            log_purchase_edit(
                purchase=purchase,
                user=request.user,
                action="item_updated",
                field_name="item",
                old_value=old_item,
                new_value=new_item,
            )

            messages.success(request, "Product updated successfully.")
            return redirect("purchase_detail", purchase_id=purchase.id)
    else:
        form = PurchaseItemsForm(instance=item)

    return render(request, "purchases/edit_item.html", {
        "purchase": purchase,
        "item": item,
        "form": form,
    })


@login_required
def resume_purchase(request):
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("accounts_login")

    query = request.GET.get("q", "").strip()
    purchases = Purchase.objects.none()

    if query:
        if access["can_view_reports"]:
            purchases = (
                Purchase.objects.filter(isp_number__icontains=query) |
                Purchase.objects.filter(seller_last_name__icontains=query) |
                Purchase.objects.filter(seller_first_name__icontains=query)
            ).distinct().order_by("-created_at")
        else:
            purchases = (
                Purchase.objects.filter(buyer_initials=access["buyer_code"], isp_number__icontains=query) |
                Purchase.objects.filter(buyer_initials=access["buyer_code"], seller_last_name__icontains=query) |
                Purchase.objects.filter(buyer_initials=access["buyer_code"], seller_first_name__icontains=query)
            ).distinct().order_by("-created_at")

    return render(request, "purchases/resume_purchase.html", {
        "query": query,
        "purchases": purchases,
    })


@login_required
def edit_purchase_header(request, purchase_id):
    purchase = get_object_or_404(Purchase, id=purchase_id)
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("resume_purchase")

    if purchase.workflow_status == "finalized" and not access["can_edit_all_purchases"]:
        return redirect("purchase_detail", purchase_id=purchase.id)

    if purchase.buyer_initials != access["buyer_code"] and not access["can_edit_all_purchases"]:
        messages.error(request, "You do not have permission to edit this purchase.")
        return redirect("purchase_detail", purchase_id=purchase.id)

    if request.method == "POST":
        original_purchase = {
            "seller_first_name": purchase.seller_first_name,
            "seller_last_name": purchase.seller_last_name,
            "purchase_total_amount": purchase.purchase_total_amount,
            "location": purchase.location,
            "payment_method": getattr(purchase, "payment_method", ""),
            "second_payment_method": getattr(purchase, "second_payment_method", ""),
        }

        form = PurchaseForm(request.POST, instance=purchase, user=request.user)

        if form.is_valid():
            purchase = form.save(commit=False)
            purchase.buyer_initials = access["buyer_code"]
            purchase.save()

            purchase.refresh_from_db()
            recalculate_purchase_totals(purchase)

            log_purchase_edit(
                purchase=purchase,
                user=request.user,
                action="purchase_header_updated",
                old_value=str(original_purchase),
                new_value=(
                    f"seller_first_name={purchase.seller_first_name}, "
                    f"seller_last_name={purchase.seller_last_name}, "
                    f"purchase_total_amount={purchase.purchase_total_amount}, "
                    f"location={purchase.location}, "
                    f"payment_method={getattr(purchase, 'payment_method', '')}, "
                    f"second_payment_method={getattr(purchase, 'second_payment_method', '')}"
                ),
            )

            messages.success(request, "Purchase details updated successfully.")
            return redirect("purchase_detail", purchase_id=purchase.id)
    else:
        form = PurchaseForm(instance=purchase, user=request.user)

    return render(request, "purchases/edit_purchase_header.html", {
        "purchase": purchase,
        "form": form,
    })


@login_required
def buyer_dashboard(request):
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("accounts_login")

    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all").strip().lower()
    buyer_code = access["buyer_code"]

    purchases = Purchase.objects.filter(
        buyer_initials=buyer_code
    ).prefetch_related("items").order_by("-created_at")

    if query:
        purchases = (
            purchases.filter(isp_number__icontains=query) |
            purchases.filter(seller_last_name__icontains=query) |
            purchases.filter(seller_first_name__icontains=query)
        ).distinct().order_by("-created_at")

    all_needs_attention_purchases = purchases.filter(
        reconciliation_status__in=["under", "over"]
    ).order_by("-created_at")

    all_draft_purchases = purchases.filter(
        workflow_status="draft",
        reconciliation_status="balanced"
    ).order_by("-created_at")

    all_finalized_purchases = purchases.filter(
        workflow_status="finalized"
    ).order_by("-created_at")

    all_draft_kpi_purchases = purchases.filter(
        workflow_status="draft"
    )

    total_purchases = purchases.count()
    draft_purchases_count = all_draft_kpi_purchases.count()
    finalized_purchases_count = all_finalized_purchases.count()
    needs_attention_count = all_needs_attention_purchases.count()

    cutoff = timezone.now() - timedelta(days=30)
    finalized_last_30 = all_finalized_purchases.filter(
        finalized_at__isnull=False,
        finalized_at__gte=cutoff
    )

    total_revenue_30d = Decimal("0.00")
    total_cost_30d = Decimal("0.00")

    for purchase in finalized_last_30:
        for item in purchase.items.all():
            qty = item.quantity or 0
            retail = item.retail_price or Decimal("0.00")
            line_cost = item.line_total_cost or Decimal("0.00")

            total_revenue_30d += Decimal(qty) * retail
            total_cost_30d += line_cost

    avg_margin_30d = None
    if total_revenue_30d > 0:
        avg_margin_30d = ((total_revenue_30d - total_cost_30d) / total_revenue_30d) * 100
        avg_margin_30d = f"{avg_margin_30d:.1f}%"

    if status_filter == "draft":
        draft_purchases = all_draft_purchases
        finalized_purchases = Purchase.objects.none()
        needs_attention_purchases = Purchase.objects.none()
    elif status_filter == "finalized":
        draft_purchases = Purchase.objects.none()
        finalized_purchases = all_finalized_purchases
        needs_attention_purchases = Purchase.objects.none()
    elif status_filter == "attention":
        draft_purchases = Purchase.objects.none()
        finalized_purchases = Purchase.objects.none()
        needs_attention_purchases = all_needs_attention_purchases
    else:
        status_filter = "all"
        draft_purchases = all_draft_purchases
        finalized_purchases = all_finalized_purchases
        needs_attention_purchases = all_needs_attention_purchases

    return render(request, "purchases/dashboard.html", {
        "query": query,
        "status_filter": status_filter,
        "draft_purchases": draft_purchases,
        "finalized_purchases": finalized_purchases,
        "needs_attention_purchases": needs_attention_purchases,
        "total_purchases": total_purchases,
        "draft_purchases_count": draft_purchases_count,
        "finalized_purchases_count": finalized_purchases_count,
        "needs_attention_count": needs_attention_count,
        "avg_margin_30d": avg_margin_30d,
        "buyer": profile,
        "access": access,
    })


@login_required
def post_login_redirect(request):
    access = get_user_profile_flags(request.user)

    if access["can_view_reports"]:
        return redirect("admin_dashboard")

    return redirect("buyer_dashboard")


@login_required
def admin_dashboard(request):
    access = get_user_profile_flags(request.user)
    profile = access["profile"]

    if not profile:
        messages.error(
            request,
            "No buyer profile is assigned to this user. Please contact an administrator."
        )
        return redirect("accounts_login")

    if not can_view_reports(access):
        messages.error(request, "You do not have permission to view reports.")
        return redirect("buyer_dashboard")

    today = timezone.now().date()
    default_start = today - timedelta(days=30)

    date_from = request.GET.get("date_from") or default_start.isoformat()
    date_to = request.GET.get("date_to") or today.isoformat()
    buyer_filter = request.GET.get("buyer", "").strip()
    location_filter = request.GET.get("location", "").strip()
    status_filter = request.GET.get("status", "").strip().lower()
    payment_filter = request.GET.get("payment_method", "").strip().lower()
    export_status = request.GET.get("export_status", "").strip().lower()
    query = request.GET.get("q", "").strip()

    purchases = Purchase.objects.prefetch_related("items").filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).order_by("-created_at")

    if buyer_filter:
        purchases = purchases.filter(buyer_initials__iexact=buyer_filter)

    if location_filter:
        purchases = purchases.filter(location__iexact=location_filter)

    if status_filter == "fix_required":
        purchases = purchases.filter(reconciliation_status__in=["under", "over"])
    elif status_filter == "in_progress":
        purchases = purchases.filter(workflow_status="draft", reconciliation_status="balanced")
    elif status_filter == "completed":
        purchases = purchases.filter(workflow_status="finalized")

    if payment_filter:
        purchases = purchases.filter(
            Q(payment_method=payment_filter) | Q(second_payment_method=payment_filter)
        )

    if export_status == "exported":
        purchases = purchases.filter(exported_at__isnull=False)
    elif export_status == "not_exported":
        purchases = purchases.filter(exported_at__isnull=True)

    if query:
        purchases = purchases.filter(
            Q(isp_number__icontains=query) |
            Q(seller_first_name__icontains=query) |
            Q(seller_last_name__icontains=query) |
            Q(buyer_initials__icontains=query)
        )

    all_purchases = purchases.order_by("-created_at")

    fix_required_purchases = all_purchases.filter(
        reconciliation_status__in=["under", "over"]
    )

    in_progress_purchases = all_purchases.filter(
        workflow_status="draft",
        reconciliation_status="balanced"
    )

    completed_purchases = all_purchases.filter(
        workflow_status="finalized"
    )

    total_purchases = all_purchases.count()
    fix_required_count = fix_required_purchases.count()
    in_progress_count = in_progress_purchases.count()
    completed_count = completed_purchases.count()

    total_purchase_amount = sum(
        (purchase.purchase_total_amount or Decimal("0.00")) for purchase in all_purchases
    )

    total_revenue = Decimal("0.00")
    total_cost = Decimal("0.00")

    for purchase in completed_purchases:
        for item in purchase.items.all():
            qty = item.quantity or 0
            retail = item.retail_price or Decimal("0.00")
            line_cost = item.line_total_cost or Decimal("0.00")
            total_revenue += Decimal(qty) * retail
            total_cost += line_cost

    avg_margin = None
    if total_revenue > 0:
        avg_margin = ((total_revenue - total_cost) / total_revenue) * 100
        avg_margin = f"{avg_margin:.1f}%"

    buyer_summary = {}
    for purchase in completed_purchases:
        buyer_code = purchase.buyer_initials or "Unknown"
        if buyer_code not in buyer_summary:
            buyer_summary[buyer_code] = {
                "buyer_code": buyer_code,
                "purchase_count": 0,
                "purchase_total": Decimal("0.00"),
                "revenue": Decimal("0.00"),
                "cost": Decimal("0.00"),
            }

        buyer_summary[buyer_code]["purchase_count"] += 1
        buyer_summary[buyer_code]["purchase_total"] += purchase.purchase_total_amount or Decimal("0.00")

        for item in purchase.items.all():
            qty = item.quantity or 0
            retail = item.retail_price or Decimal("0.00")
            line_cost = item.line_total_cost or Decimal("0.00")
            buyer_summary[buyer_code]["revenue"] += Decimal(qty) * retail
            buyer_summary[buyer_code]["cost"] += line_cost

    buyer_summary_rows = []
    for row in buyer_summary.values():
        revenue = row["revenue"]
        cost = row["cost"]
        margin = None
        if revenue > 0:
            margin = ((revenue - cost) / revenue) * 100
            margin = f"{margin:.1f}%"

        buyer_summary_rows.append({
            "buyer_code": row["buyer_code"],
            "purchase_count": row["purchase_count"],
            "purchase_total": row["purchase_total"],
            "avg_margin": margin,
        })

    buyer_summary_rows.sort(key=lambda x: x["purchase_count"], reverse=True)

    location_summary = {}
    for purchase in completed_purchases:
        location = purchase.location or "Unknown"
        if location not in location_summary:
            location_summary[location] = {
                "location": location,
                "purchase_count": 0,
                "purchase_total": Decimal("0.00"),
                "revenue": Decimal("0.00"),
                "cost": Decimal("0.00"),
            }

        location_summary[location]["purchase_count"] += 1
        location_summary[location]["purchase_total"] += purchase.purchase_total_amount or Decimal("0.00")

        for item in purchase.items.all():
            qty = item.quantity or 0
            retail = item.retail_price or Decimal("0.00")
            line_cost = item.line_total_cost or Decimal("0.00")
            location_summary[location]["revenue"] += Decimal(qty) * retail
            location_summary[location]["cost"] += line_cost

    location_summary_rows = []
    for row in location_summary.values():
        revenue = row["revenue"]
        cost = row["cost"]
        margin = None
        if revenue > 0:
            margin = ((revenue - cost) / revenue) * 100
            margin = f"{margin:.1f}%"

        location_summary_rows.append({
            "location": row["location"],
            "purchase_count": row["purchase_count"],
            "purchase_total": row["purchase_total"],
            "avg_margin": margin,
        })

    location_summary_rows.sort(key=lambda x: x["purchase_count"], reverse=True)

    buyer_choices = Purchase.objects.values_list("buyer_initials", flat=True).distinct().order_by("buyer_initials")
    location_choices = Purchase.objects.values_list("location", flat=True).distinct().order_by("location")

    return render(request, "purchases/admin_dashboard.html", {
        "buyer": profile,
        "access": access,
        "date_from": date_from,
        "date_to": date_to,
        "buyer_filter": buyer_filter,
        "location_filter": location_filter,
        "status_filter": status_filter,
        "payment_filter": payment_filter,
        "query": query,
        "all_purchases": all_purchases,
        "fix_required_purchases": fix_required_purchases,
        "in_progress_purchases": in_progress_purchases,
        "completed_purchases": completed_purchases,
        "total_purchases": total_purchases,
        "fix_required_count": fix_required_count,
        "in_progress_count": in_progress_count,
        "completed_count": completed_count,
        "total_purchase_amount": total_purchase_amount,
        "avg_margin": avg_margin,
        "buyer_summary_rows": buyer_summary_rows,
        "location_summary_rows": location_summary_rows,
        "buyer_choices": buyer_choices,
        "location_choices": location_choices,
        "export_status": export_status,
    })


@login_required
def export_purchase_csv(request, purchase_id):
    access = get_user_profile_flags(request.user)

    if not can_view_reports(access):
        messages.error(request, "You do not have permission to export purchases.")
        return redirect("buyer_dashboard")

    purchase = get_object_or_404(Purchase, id=purchase_id)

    if purchase.workflow_status != "finalized":
        return redirect("purchase_detail", purchase_id=purchase.id)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{purchase.isp_number}.csv"'

    writer = csv.writer(response)
    writer.writerow(["Title", "SKU", "Qty", "Cost", "Retail Price"])

    for item in purchase.items.all():
        writer.writerow([
            item.title,
            item.sku,
            item.quantity,
            item.unit_cost,
            item.retail_price,
        ])

    purchase.exported_at = timezone.now()
    purchase.exported_by = request.user
    purchase.export_count += 1
    purchase.save()

    log_purchase_edit(
        purchase=purchase,
        user=request.user,
        action="purchase_exported",
        note=f"Single purchase CSV exported. Export count is now {purchase.export_count}.",
    )

    return response


@login_required
def export_filtered_finalized_csv(request):
    access = get_user_profile_flags(request.user)

    if not can_view_reports(access):
        messages.error(request, "You do not have permission to export purchases.")
        return redirect("buyer_dashboard")

    today = timezone.now().date()
    default_start = today - timedelta(days=30)

    date_from = request.GET.get("date_from") or default_start.isoformat()
    date_to = request.GET.get("date_to") or today.isoformat()
    buyer_filter = request.GET.get("buyer", "").strip()
    location_filter = request.GET.get("location", "").strip()
    payment_filter = request.GET.get("payment_method", "").strip().lower()
    export_status = request.GET.get("export_status", "").strip().lower()
    query = request.GET.get("q", "").strip()

    purchases = Purchase.objects.prefetch_related("items").filter(
        workflow_status="finalized",
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).order_by("-created_at")

    if buyer_filter:
        purchases = purchases.filter(buyer_initials__iexact=buyer_filter)

    if location_filter:
        purchases = purchases.filter(location__iexact=location_filter)

    if payment_filter:
        purchases = purchases.filter(
            Q(payment_method=payment_filter) | Q(second_payment_method=payment_filter)
        )

    if export_status == "exported":
        purchases = purchases.filter(exported_at__isnull=False)
    elif export_status == "not_exported":
        purchases = purchases.filter(exported_at__isnull=True)

    if query:
        purchases = purchases.filter(
            Q(isp_number__icontains=query) |
            Q(seller_first_name__icontains=query) |
            Q(seller_last_name__icontains=query) |
            Q(buyer_initials__icontains=query)
        )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="finalized_purchases_export.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "ISP Number",
        "Finalized Date",
        "Buyer",
        "Location",
        "Seller First Name",
        "Seller Last Name",
        "SKU",
        "Title",
        "Quantity",
        "Unit Cost",
        "Retail Price",
        "Line Total Cost",
        "Primary Payment Method",
        "Primary Payment Amount",
        "Second Payment Method",
        "Second Payment Amount",
    ])

    exported_purchase_ids = []

    for purchase in purchases:
        for item in purchase.items.all():
            writer.writerow([
                purchase.isp_number,
                purchase.finalized_at.strftime("%Y-%m-%d %H:%M") if purchase.finalized_at else "",
                purchase.buyer_initials,
                purchase.location,
                purchase.seller_first_name,
                purchase.seller_last_name,
                item.sku,
                item.title,
                item.quantity,
                item.unit_cost,
                item.retail_price,
                item.line_total_cost,
                purchase.payment_method,
                purchase.primary_payment_amount,
                purchase.second_payment_method,
                purchase.second_payment_amount,
            ])

        exported_purchase_ids.append(purchase.id)

    if exported_purchase_ids:
        timestamp = timezone.now()
        Purchase.objects.filter(id__in=exported_purchase_ids).update(
            exported_at=timestamp,
            exported_by=request.user,
        )

        for purchase in purchases:
            log_purchase_edit(
                purchase=purchase,
                user=request.user,
                action="purchase_exported",
                note="Included in filtered finalized CSV export.",
            )

    return response


@login_required
def reopen_purchase(request, purchase_id):
    access = get_user_profile_flags(request.user)

    if not can_reopen_purchase(access):
        messages.error(request, "You do not have permission to reopen purchases.")
        return redirect("buyer_dashboard")

    purchase = get_object_or_404(Purchase, id=purchase_id)

    if request.method == "POST" and purchase.workflow_status == "finalized":
        reopen_reason = request.POST.get("reopen_reason", "").strip()

        if not reopen_reason:
            return redirect("purchase_detail", purchase_id=purchase.id)

        purchase.workflow_status = "draft"
        purchase.finalized_at = None
        purchase.reopened_at = timezone.now()
        purchase.reopened_by = request.user
        purchase.reopen_reason = reopen_reason
        purchase.save()

        log_purchase_edit(
            purchase=purchase,
            user=request.user,
            action="purchase_reopened",
            note=reopen_reason,
        )

    return redirect("purchase_detail", purchase_id=purchase.id)




@login_required
def download_purchase_order(request, purchase_id):
    access = get_user_profile_flags(request.user)

    if not can_view_reports(access):
        messages.error(request, "You do not have permission to view purchase orders.")
        return redirect("buyer_dashboard")

    purchase = get_object_or_404(Purchase, id=purchase_id)

    if purchase.workflow_status != "finalized":
        return redirect("purchase_detail", purchase_id=purchase.id)

    return render(request, "purchases/download_purchase_order.html", {
        "purchase": purchase,
        "buyer": access["profile"],
        "access": access,
    })

@login_required
def export_accounting_report_csv(request):
    access = get_user_profile_flags(request.user)

    if not can_view_reports(access):
        messages.error(request, "You do not have permission to export accounting reports.")
        return redirect("buyer_dashboard")

    today = timezone.localdate()
    default_start = today - timedelta(days=30)

    date_from = request.GET.get("date_from") or default_start.isoformat()
    date_to = request.GET.get("date_to") or today.isoformat()
    payment_filter = request.GET.get("payment_method", "").strip().lower()

    purchases = Purchase.objects.filter(
        workflow_status="finalized",
        finalized_at__date__gte=date_from,
        finalized_at__date__lte=date_to,
    ).order_by("-finalized_at")

    if payment_filter:
        purchases = purchases.filter(
            Q(payment_method__iexact=payment_filter) |
            Q(second_payment_method__iexact=payment_filter)
        )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="accounting_report.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Date/Time Finalized",
        "ISP #",
        "Location",
        "Seller First Name",
        "Seller Last Name",
        "Order Total",
        "Paid via Cash",
        "Paid via Check",
        "Check #",
        "Paid via Gift Card",
        "Gift Card #",
        "Paid via Other",
        "Other Explained",
    ])

    for purchase in purchases:
        cash_amount = Decimal("0.00")
        check_amount = Decimal("0.00")
        check_number = ""
        gift_card_amount = Decimal("0.00")
        gift_card_number = ""
        other_amount = Decimal("0.00")
        other_explained = ""

        payment_1 = (purchase.payment_method or "").strip().lower()
        amount_1 = purchase.primary_payment_amount or Decimal("0.00")

        payment_2 = (purchase.second_payment_method or "").strip().lower()
        amount_2 = purchase.second_payment_amount or Decimal("0.00")

        if payment_1 == "cash":
            cash_amount += amount_1
        elif payment_1 == "check":
            check_amount += amount_1
            check_number = purchase.check_number or ""
        elif payment_1 == "gift_card":
            gift_card_amount += amount_1
            gift_card_number = purchase.gift_card_last4 or ""
        elif payment_1:
            other_amount += amount_1
            other_explained = purchase.payment_other_reason or payment_1

        if payment_2 == "cash":
            cash_amount += amount_2
        elif payment_2 == "check":
            check_amount += amount_2
            if not check_number:
                check_number = purchase.second_check_number or ""
        elif payment_2 == "gift_card":
            gift_card_amount += amount_2
            if not gift_card_number:
                gift_card_number = purchase.second_gift_card_last4 or ""
        elif payment_2:
            other_amount += amount_2
            if other_explained:
                other_explained = f"{other_explained}; {purchase.second_payment_other_reason or payment_2}"
            else:
                other_explained = purchase.second_payment_other_reason or payment_2

        writer.writerow([
            timezone.localtime(purchase.finalized_at).strftime("%Y-%m-%d %I:%M %p") if purchase.finalized_at else "",
            purchase.isp_number,
            purchase.location,
            purchase.seller_first_name,
            purchase.seller_last_name,
            purchase.purchase_total_amount or Decimal("0.00"),
            cash_amount,
            check_amount,
            check_number,
            gift_card_amount,
            gift_card_number,
            other_amount,
            other_explained,
        ])

    return response