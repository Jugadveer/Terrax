from django.db import models
from django.contrib.auth.models import User

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone = models.CharField(max_length=15, blank=True)
    wallet_address = models.CharField(max_length=100, blank=True, null=True)
    kyc_verified = models.BooleanField(default=False)

    def __str__(self):
        return self.user.username



class Listing(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
    ]
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    property_type = models.CharField(max_length=50, blank=True, null=True)
    size = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    video = models.FileField(upload_to='listing_videos/', blank=True, null=True)
    kyc_doc = models.FileField(upload_to='kyc_docs/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return self.title or f"Listing #{self.id}"


class ListingImage(models.Model):
    listing = models.ForeignKey('Listing', on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='listing_images/')

class ListingDocument(models.Model):
    listing = models.ForeignKey('Listing', on_delete=models.CASCADE, related_name='documents')
    document = models.FileField(upload_to='listing_docs/')
