from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class PurchaseEditLog(models.Model):
    purchase = models.ForeignKey(
        "Purchase",
        on_delete=models.CASCADE,
        related_name="edit_logs",
    )
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=50)
    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        purchase_number = getattr(self.purchase, "isp_number", "Unknown Purchase")
        return f"{purchase_number} - {self.action} - {self.created_at:%Y-%m-%d %H:%M}"


class BuyerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    buyer_code = models.CharField(max_length=10, unique=True)

    can_view_reports = models.BooleanField(default=False)
    can_edit_all_purchases = models.BooleanField(default=False)
    can_reopen_purchases = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} - {self.buyer_code}"


def generate_unique_buyer_code(user):
    first = (user.first_name[:1] if user.first_name else "").upper()
    last = (user.last_name[:1] if user.last_name else "").upper()

    base_code = f"{first}{last}".strip()

    if not base_code:
        base_code = user.username[:2].upper()

    candidate = base_code
    counter = 2

    while BuyerProfile.objects.filter(buyer_code=candidate).exists():
        candidate = f"{base_code}{counter}"
        counter += 1

    return candidate


@receiver(post_save, sender=User)
def create_buyer_profile(sender, instance, created, **kwargs):
    if created:
        BuyerProfile.objects.create(
            user=instance,
            buyer_code=generate_unique_buyer_code(instance)
        )


class Purchase(models.Model):
    isp_number = models.CharField(max_length=20, unique=True)
    buyer_initials = models.CharField(max_length=10)

    seller_first_name = models.CharField(max_length=100)
    seller_last_name = models.CharField(max_length=100)
    seller_address = models.CharField(max_length=255)
    seller_city = models.CharField(max_length=100)
    seller_state = models.CharField(max_length=50)
    seller_zip = models.CharField(max_length=20)
    seller_phone = models.CharField(max_length=20)
    seller_email = models.EmailField()

    drivers_license_state = models.CharField(max_length=50)
    drivers_license_number = models.CharField(max_length=50)

    location = models.CharField(max_length=20)
    purchase_total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    allocation_total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    allocation_difference = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    reconciliation_status = models.CharField(max_length=20, default="under")
    workflow_status = models.CharField(max_length=20, default="draft")

    payment_method = models.CharField(max_length=20)
    check_number = models.CharField(max_length=50, blank=True)
    gift_card_last4 = models.CharField(max_length=4, blank=True)
    payment_other_reason = models.CharField(max_length=255, blank=True)
    is_split_payment = models.BooleanField(default=False)
    primary_payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    second_payment_method = models.CharField(max_length=20, blank=True)
    second_check_number = models.CharField(max_length=50, blank=True)
    second_gift_card_last4 = models.CharField(max_length=4, blank=True)
    second_payment_other_reason = models.CharField(max_length=255, blank=True)
    second_payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    payment_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    exported_at = models.DateTimeField(blank=True, null=True)
    exported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="exported_purchases",
    )
    export_batch_name = models.CharField(max_length=50, blank=True, default="")

    reopened_at = models.DateTimeField(blank=True, null=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reopened_purchases",
    )
    reopen_reason = models.TextField(blank=True)

    export_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["workflow_status"]),
            models.Index(fields=["reconciliation_status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["finalized_at"]),
            models.Index(fields=["exported_at"]),
            models.Index(fields=["buyer_initials"]),
            models.Index(fields=["location"]),
        ]

    def __str__(self):
        return self.isp_number


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    sku = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    retail_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.sku