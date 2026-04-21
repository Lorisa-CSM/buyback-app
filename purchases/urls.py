from django.contrib.auth import views as auth_views
from django.urls import path

from .views import (
    purchase_home,
    buyer_dashboard,
    resume_purchase,
    purchase_detail,
    add_purchase_item,
    add_purchase_items_bulk,
    add_bulk_cards,
    finalize_purchase,
    delete_purchase_item,
    edit_purchase_item,
    export_purchase_csv,
    export_filtered_finalized_csv,
    reopen_purchase,
    edit_purchase_header,
    download_purchase_order,
    admin_dashboard,
    post_login_redirect,
    export_accounting_report_csv,
    bulk_export_completed_purchases,
    admin_utilities,
    system_settings,
    manage_users,
    add_buyer,

)

urlpatterns = [
    # Core navigation
    path("", buyer_dashboard, name="buyer_dashboard"),
    path("admin-dashboard/", admin_dashboard, name="admin_dashboard"),

    # Auth
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="accounts_login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(next_page="accounts_login"),
        name="accounts_logout",
    ),
    path("post-login/", post_login_redirect, name="post_login_redirect"),

    # Purchase flow
    path("purchase/new/", purchase_home, name="purchase_home"),
    path("resume/", resume_purchase, name="resume_purchase"),
    path("purchase/<int:purchase_id>/", purchase_detail, name="purchase_detail"),
    path(
        "purchase/<int:purchase_id>/add-item/",
        add_purchase_item,
        name="add_purchase_item",
    ),
    path(
        "purchase/<int:purchase_id>/add-items/",
        add_purchase_items_bulk,
        name="add_purchase_items_bulk",
    ),
    path(
        "purchase/<int:purchase_id>/bulk/",
        add_bulk_cards,
        name="add_bulk_cards",
    ),
    path(
        "purchase/<int:purchase_id>/finalize/",
        finalize_purchase,
        name="finalize_purchase",
    ),
    path(
        "purchase/<int:purchase_id>/reopen/",
        reopen_purchase,
        name="reopen_purchase",
    ),
    path(
        "purchase/<int:purchase_id>/delete-item/<int:item_id>/",
        delete_purchase_item,
        name="delete_purchase_item",
    ),
    path(
        "purchase/<int:purchase_id>/edit-item/<int:item_id>/",
        edit_purchase_item,
        name="edit_purchase_item",
    ),
    path(
        "purchase/<int:purchase_id>/edit/",
        edit_purchase_header,
        name="edit_purchase_header",
    ),
    path(
        "purchase/<int:purchase_id>/download-order/",
        download_purchase_order,
        name="download_purchase_order",
    ),

    # Exports (admin only)
    path(
        "exports/finalized-orders/",
        export_filtered_finalized_csv,
        name="export_filtered_finalized_csv",
    ),
    path(
        "exports/accounting/",
        export_accounting_report_csv,
        name="export_accounting_report_csv",
    ),
    path(
        "exports/purchase/<int:purchase_id>/",
        export_purchase_csv,
        name="export_purchase_csv",
    ),
    path(
        "exports/completed/bulk/",
        bulk_export_completed_purchases,
        name="bulk_export_completed_purchases",
    ),

    # ========================
    # ADMIN UTILITIES
    # ========================
    path("admin/utilities/", admin_utilities, name="admin_utilities"),
    path("admin/utilities/users/", manage_users, name="manage_users"),
    path("admin/utilities/add-buyer/", add_buyer, name="add_buyer"),
    path("admin/utilities/settings/", system_settings, name="system_settings"),

]