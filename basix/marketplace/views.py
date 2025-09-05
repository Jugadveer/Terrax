from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
import requests, time, hashlib, hmac
from django.http import JsonResponse
from django.conf import settings
from django.http import JsonResponse
from .models import Listing

SUMSUB_SECRET_KEY = "your_secret_key"
SUMSUB_APP_TOKEN = "your_app_token"
SUMSUB_BASE_URL = "https://api.sumsub.com"


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("marketplace")  # go to marketplace/dashboard
        else:
            messages.error(request, "Invalid username or password")
            return redirect("landing")
    return redirect("landing")  # fallback

def logout_view(request):
    logout(request)
    return redirect("landing")

from .models import Profile

def signup_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email")
        password1 = request.POST.get("password1")
        password2 = request.POST.get("password2")
        phone = request.POST.get("phone")
        wallet = request.POST.get("wallet_address")

        if password1 != password2:
            messages.error(request, "Passwords do not match")
            return redirect("landing")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already taken")
            return redirect("landing")

        user = User.objects.create_user(username=username, email=email, password=password1)

        # ✅ Create profile manually
        Profile.objects.create(user=user, phone=phone, wallet_address=wallet)

        login(request, user)
        messages.success(request, "Account created successfully")
        return redirect("marketplace")
    
    return redirect("landing")



# def generate_signature(method, url, ts, body=""):
#     msg = f"{ts}{method}{url}{body}".encode()
#     return hmac.new(SUMSUB_SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()

# def get_applicant_token(request):
#     ts = str(int(time.time()))
#     url = "/resources/accessTokens?userId=" + str(request.user.id) + "&levelName=basic-kyc-level"

#     signature = generate_signature("POST", url, ts)

#     headers = {
#         "X-App-Token": SUMSUB_APP_TOKEN,
#         "X-App-Access-Sig": signature,
#         "X-App-Access-Ts": ts,
#         "Accept": "application/json",
#     }

#     r = requests.post(SUMSUB_BASE_URL + url, headers=headers)
#     return JsonResponse(r.json())


# def kyc_verification(request):
#     # Fetch applicant token from API
#     r = requests.get(reverse("get_applicant_token"), request=request)
#     applicant_token = r.json().get("token", "")
#     return render(request, "kyc_verification.html", {"applicant_token": applicant_token})


from django.http import JsonResponse
from .models import Listing, ListingImage, ListingDocument

def save_listing_step(request, step):
    if request.method == 'POST':
        listing_id = request.POST.get('listing_id')
        if listing_id:
            listing = Listing.objects.get(id=listing_id)
        else:
            listing = Listing.objects.create(owner=request.user)
            listing_id = listing.id

        if step == 1:
            for img in request.FILES.getlist('images'):
                ListingImage.objects.create(listing=listing, image=img)
            video = request.FILES.get('video')
            if video:
                listing.video = video
        elif step == 2:
            listing.title = request.POST.get('title')
            listing.description = request.POST.get('description')
            listing.location = request.POST.get('location')
            listing.property_type = request.POST.get('property_type')
        elif step == 3:
            kyc_doc = request.FILES.get('kyc_doc')
            if kyc_doc:
                listing.kyc_doc = kyc_doc

            for doc in request.FILES.getlist('ownership_docs'):
                ListingDocument.objects.create(listing=listing, document=doc)

            for doc in request.FILES.getlist('other_docs'):
                ListingDocument.objects.create(listing=listing, document=doc)

        listing.save()
        return JsonResponse({'success': True, 'listing_id': listing_id})

    return JsonResponse({'success': False, 'error': 'Invalid request'})

def submit_listing(request):
    if request.method == 'POST':
        listing_id = request.POST.get('listing_id')
        if not listing_id:
            return JsonResponse({'success': False, 'error': 'Missing listing ID'})

        try:
            listing = Listing.objects.get(id=listing_id)
        except Listing.DoesNotExist:
            return JsonResponse({'success': False, 'error': f'Listing {listing_id} not found'})

        listing.status = 'submitted'
        listing.save()
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Invalid request'})




def home(request):
    return render(request, 'landing.html')


def marketplace(request):
    return render(request, 'marketplace.html')


