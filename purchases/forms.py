import re
from decimal import Decimal

from django import forms
from django.forms import modelformset_factory

from .models import Purchase, PurchaseItem


PAYMENT_METHOD_CHOICES = [
    ("cash", "Cash"),
    ("check", "Check"),
    ("gift_card", "Gift Card"),
    ("other", "Other"),
]

LOCATION_CHOICES = [
    ("", "Select Your Location"),
    ("Apex", "Apex"),
    ("Kannapolis", "Kannapolis"),
]


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = [
            "location",
            "seller_first_name",
            "seller_last_name",
            "seller_address",
            "seller_city",
            "seller_state",
            "seller_zip",
            "seller_phone",
            "seller_email",
            "drivers_license_state",
            "drivers_license_number",
            "purchase_total_amount",
            "payment_method",
            "check_number",
            "gift_card_last4",
            "payment_other_reason",
            "is_split_payment",
            "primary_payment_amount",
            "second_payment_method",
            "second_check_number",
            "second_gift_card_last4",
            "second_payment_other_reason",
            "second_payment_amount",
            "payment_notes",
        ]
        widgets = {
            "seller_email": forms.EmailInput(),
            "payment_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        self.fields["location"].widget = forms.Select(choices=LOCATION_CHOICES)
        self.fields["payment_method"].widget = forms.Select(choices=PAYMENT_METHOD_CHOICES)
        self.fields["second_payment_method"].widget = forms.Select(
            choices=[("", "Select Second Payment Method")] + PAYMENT_METHOD_CHOICES
        )

        for field_name, field in self.fields.items():
            field.required = False

        required_fields = [
            "location",
            "seller_first_name",
            "seller_last_name",
            "seller_address",
            "seller_city",
            "seller_state",
            "seller_zip",
            "seller_phone",
            "seller_email",
            "drivers_license_state",
            "drivers_license_number",
            "purchase_total_amount",
            "payment_method",
        ]
        for field_name in required_fields:
            self.fields[field_name].required = True

        is_admin_editor = bool(
            self.user
            and getattr(self.user, "buyerprofile", None)
            and self.user.buyerprofile.can_edit_all_purchases
        )

        if self.instance and self.instance.pk and self.instance.isp_number and not is_admin_editor:
            locked_fields = [
                "seller_first_name",
                "seller_last_name",
                "seller_address",
                "seller_city",
                "seller_state",
                "seller_zip",
                "seller_phone",
                "seller_email",
                "drivers_license_state",
                "drivers_license_number",
                "purchase_total_amount",
            ]
            for field_name in locked_fields:
                if field_name in self.fields:
                    self.fields[field_name].disabled = True

        if (
            self.instance
            and self.instance.pk
            and self.instance.workflow_status == "finalized"
            and not is_admin_editor
        ):
            for field in self.fields.values():
                field.disabled = True

    def clean_seller_phone(self):
        phone = (self.cleaned_data.get("seller_phone") or "").strip()
        digits = re.sub(r"\D", "", phone)

        if len(digits) != 10:
            raise forms.ValidationError("Enter a valid 10-digit phone number.")

        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"

    def clean_gift_card_last4(self):
        value = (self.cleaned_data.get("gift_card_last4") or "").strip()
        if value and len(value) > 4:
            raise forms.ValidationError("Enter only the last 4 characters.")
        return value.upper()

    def clean_second_gift_card_last4(self):
        value = (self.cleaned_data.get("second_gift_card_last4") or "").strip()
        if value and len(value) > 4:
            raise forms.ValidationError("Enter only the last 4 characters.")
        return value.upper()

    def clean(self):
        cleaned = super().clean()

        purchase_total = cleaned.get("purchase_total_amount") or Decimal("0.00")
        payment_method = cleaned.get("payment_method")
        check_number = (cleaned.get("check_number") or "").strip()
        gift_card_last4 = (cleaned.get("gift_card_last4") or "").strip()
        payment_other_reason = (cleaned.get("payment_other_reason") or "").strip()

        is_split = cleaned.get("is_split_payment") or False
        primary_amount = cleaned.get("primary_payment_amount") or Decimal("0.00")
        second_method = cleaned.get("second_payment_method")
        second_check_number = (cleaned.get("second_check_number") or "").strip()
        second_gift_card_last4 = (cleaned.get("second_gift_card_last4") or "").strip()
        second_payment_other_reason = (cleaned.get("second_payment_other_reason") or "").strip()
        second_amount = cleaned.get("second_payment_amount") or Decimal("0.00")
        payment_notes = (cleaned.get("payment_notes") or "").strip()

        if purchase_total <= 0:
            self.add_error("purchase_total_amount", "Total must be greater than 0.")

        if not is_split:
            cleaned["primary_payment_amount"] = purchase_total
            cleaned["second_payment_amount"] = Decimal("0.00")
            cleaned["second_payment_method"] = ""
            cleaned["second_check_number"] = ""
            cleaned["second_gift_card_last4"] = ""
            cleaned["second_payment_other_reason"] = ""
        else:
            if primary_amount <= 0:
                self.add_error("primary_payment_amount", "First payment must be greater than 0.")
            if second_amount <= 0:
                self.add_error("second_payment_amount", "Second payment must be greater than 0.")
            if not second_method:
                self.add_error("second_payment_method", "Second payment method is required.")
            if not payment_notes:
                self.add_error("payment_notes", "Payment notes are required for split payment.")
            if primary_amount + second_amount != purchase_total:
                self.add_error(
                    "second_payment_amount",
                    "First and second payment amounts must equal the purchase total.",
                )

        if payment_method == "check" and not check_number:
            self.add_error("check_number", "Check number is required when payment method is check.")

        if payment_method == "gift_card" and not gift_card_last4:
            self.add_error(
                "gift_card_last4",
                "Last 4 gift card characters are required when payment method is gift card.",
            )

        if payment_method == "other" and not payment_other_reason:
            self.add_error(
                "payment_other_reason",
                "Reason is required when payment method is other.",
            )

        if second_method == "check" and not second_check_number:
            self.add_error(
                "second_check_number",
                "Second check number is required when second payment method is check.",
            )

        if second_method == "gift_card" and not second_gift_card_last4:
            self.add_error(
                "second_gift_card_last4",
                "Second gift card last 4 is required when second payment method is gift card.",
            )

        if second_method == "other" and not second_payment_other_reason:
            self.add_error(
                "second_payment_other_reason",
                "Second payment reason is required when second payment method is other.",
            )

        return cleaned


class PurchaseItemsForm(forms.ModelForm):
    class Meta:
        model = PurchaseItem
        fields = [
            "title",
            "quantity",
            "unit_cost",
            "retail_price",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field_name, field in self.fields.items():
            field.required = False

        self.fields["quantity"].initial = 1

    def clean(self):
        cleaned = super().clean()

        title = (cleaned.get("title") or "").strip()
        quantity = cleaned.get("quantity")
        unit_cost = cleaned.get("unit_cost")
        retail_price = cleaned.get("retail_price")

        is_blank_row = not title and not quantity and unit_cost in (None, "") and retail_price in (None, "")
        if is_blank_row:
            cleaned["_skip_row"] = True
            return cleaned

        if not title:
            self.add_error("title", "Title is required.")

        if quantity is None or quantity <= 0:
            self.add_error("quantity", "Quantity must be greater than 0.")

        if unit_cost is None or unit_cost < 0:
            self.add_error("unit_cost", "Unit cost must be 0 or greater.")

        if retail_price is None or retail_price < 0:
            self.add_error("retail_price", "Retail price must be 0 or greater.")

        return cleaned


PurchaseItemFormSet = modelformset_factory(
    PurchaseItem,
    form=PurchaseItemsForm,
    extra=1,
    can_delete=True,
)


class BulkCardForm(forms.Form):
    total_cost = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        label="Total Bulk Cost",
        min_value=Decimal("0.01"),
    )