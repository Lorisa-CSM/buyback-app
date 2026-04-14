import re
from django import forms
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
            "second_payment_method",
            "second_check_number",
            "second_gift_card_last4",
            "second_payment_other_reason",
            "primary_payment_amount",
            "second_payment_amount",
            "payment_notes",
        ]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user',None)
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].widget = forms.Select(choices=PAYMENT_METHOD_CHOICES)
        self.fields["location"].widget = forms.Select(choices=LOCATION_CHOICES)
        self.fields["seller_email"].widget = forms.EmailInput()
        self.fields["second_payment_method"].widget = forms.Select(
            choices=[("", "Select Second Payment Method")] + PAYMENT_METHOD_CHOICES
        )

    # Lock fields after ISP is created
        if self.instance and self.instance.isp_number and not (self.user and self.user.is_staff):
            fields_to_lock = [
                'seller_first_name',
                'seller_last_name',
                'seller_address',
                'seller_city',
                'seller_state',
                'seller_zip',
                'seller_phone',
                'seller_email',
                'drivers_license_state',
                'drivers_license_number',
                'purchase_total_amount',
            ]

            for field in fields_to_lock:
                if field in self.fields:
                    self.fields[field].disabled = True    

        # Full lock if finalized (except admins)
        if self.instance and self.instance.workflow_status == 'final':
            if not (self.user and self.user.is_staff):
                for field in self.fields:
                    self.fields[field].disabled = True

    def clean_seller_phone(self):
        phone = self.cleaned_data.get("seller_phone", "").strip()
        digits = re.sub(r"\D", "", phone)

        if len(digits) != 10:
            raise forms.ValidationError("Enter a valid 10-digit phone number.")

        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"

    def clean(self):
        cleaned_data = super().clean()

        from decimal import Decimal

        purchase_total = cleaned_data.get("purchase_total_amount") or Decimal("0")
        primary_amount = cleaned_data.get("primary_payment_amount") or Decimal("0")
        second_amount = cleaned_data.get("second_payment_amount") or Decimal("0")
        is_split = cleaned_data.get("is_split_payment")

        # Prevent negative or zero amounts
        if purchase_total <= 0:
            self.add_error("purchase_total_amount", "Total must be greater than 0.")

        if is_split:
            if primary_amount <= 0:
                self.add_error("primary_payment_amount", "First payment must be greater than 0.")

            if second_amount <= 0:
                self.add_error("second_payment_amount", "Second payment must be greater than 0.")

            # Enforce total match
            if primary_amount + second_amount != purchase_total:
                self.add_error(
                    None,
                    "First + Second payment must equal the Purchase Total."
                )
        else:
            # If NOT split, force primary = total
            cleaned_data["primary_payment_amount"] = purchase_total
            cleaned_data["second_payment_amount"] = Decimal("0")

        payment_method = cleaned_data.get("payment_method")
        check_number = cleaned_data.get("check_number")
        gift_card_last4 = cleaned_data.get("gift_card_last4")
        payment_other_reason = cleaned_data.get("payment_other_reason")
        is_split_payment = cleaned_data.get("is_split_payment")
        payment_notes = cleaned_data.get("payment_notes")

        second_payment_method = cleaned_data.get("second_payment_method")
        second_check_number = cleaned_data.get("second_check_number")
        second_gift_card_last4 = cleaned_data.get("second_gift_card_last4")
        second_payment_other_reason = cleaned_data.get("second_payment_other_reason")

        purchase_total_amount = cleaned_data.get("purchase_total_amount")
        primary_payment_amount = cleaned_data.get("primary_payment_amount")
        second_payment_amount = cleaned_data.get("second_payment_amount")

        if payment_method == "check" and not check_number:
            self.add_error("check_number", "Check number is required when payment method is check.")

        if payment_method == "gift_card" and not gift_card_last4:
            self.add_error("gift_card_last4", "Last 4 gift card characters are required when payment method is gift card.")

        if payment_method == "other" and not payment_other_reason:
            self.add_error("payment_other_reason", "Reason is required when payment method is other.")

        if is_split_payment and not payment_notes:
            self.add_error("payment_notes", "Payment notes are required when split payment is used.")

        if is_split_payment and not second_payment_method:
            self.add_error("second_payment_method", "Second payment method is required when split payment is used.")

        if second_payment_method == "check" and not second_check_number:
            self.add_error("second_check_number", "Second check number is required when second payment method is check.")

        if second_payment_method == "gift_card" and not second_gift_card_last4:
            self.add_error("second_gift_card_last4", "Second gift card last 4 is required when second payment method is gift card.")

        if second_payment_method == "other" and not second_payment_other_reason:
            self.add_error("second_payment_other_reason", "Second payment reason is required when second payment method is other.")

        if is_split_payment and not primary_payment_amount:
            self.add_error("primary_payment_amount", "First payment amount is required when split payment is used.")

        if is_split_payment and not second_payment_amount:
            self.add_error("second_payment_amount", "Second payment amount is required when split payment is used.")

        if (
            is_split_payment
            and purchase_total_amount is not None
            and primary_payment_amount is not None
            and second_payment_amount is not None
        ):
            if primary_payment_amount + second_payment_amount != purchase_total_amount:
                self.add_error(
                    "second_payment_amount",
                    "First and second payment amounts must add up to the purchase total."
                )

        return cleaned_data


class PurchaseItemsForm(forms.ModelForm):
    class Meta:
        model = PurchaseItem
        fields = [
            "title",
            "quantity",
            "unit_cost",
            "retail_price",
        ]


PurchaseItemFormSet = forms.modelformset_factory(
    PurchaseItem,
    form=PurchaseItemsForm,
    extra=1,
    can_delete=True,
    fields=["title", "quantity", "unit_cost", "retail_price"],
)


class BulkCardForm(forms.Form):
    total_cost = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        label="Total Bulk Cost",
        min_value=0.01
    )
