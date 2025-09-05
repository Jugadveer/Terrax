from django.contrib import admin
from .models import Profile, Listing, ListingImage, ListingDocument

class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'wallet_address')
    search_fields = ('user__username', 'phone', 'wallet_address')

admin.site.register(Profile, ProfileAdmin)

admin.site.register(Listing)
admin.site.register(ListingImage)
admin.site.register(ListingDocument)