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
from django.shortcuts import get_object_or_404


def save_listing_step(request, step):
    listing_id = request.POST.get("listing_id")
    listing = None

    if listing_id:
        listing = get_object_or_404(Listing, id=listing_id, owner=request.user)

    # Step 1: Media (images, video)
    if step == 1:
        if not listing:
            listing = Listing.objects.create(owner=request.user, status="draft")

        if request.FILES.getlist("images"):
            for img in request.FILES.getlist("images"):
                ListingImage.objects.create(listing=listing, image=img)

        if request.FILES.get("video"):
            listing.video = request.FILES["video"]
            listing.save()

    # Step 2: Property Details
    elif step == 2:
        if listing:
            listing.title = request.POST.get("title")
            listing.description = request.POST.get("description")
            listing.location = request.POST.get("location")
            listing.property_type = request.POST.get("property_type")
            listing.size = request.POST.get("size") or None
            listing.bedrooms = request.POST.get("bedrooms") or None
            listing.year_built = request.POST.get("year_built") or None
            listing.ownership_type = request.POST.get("ownership_type")
            listing.save()

    # Step 3: Compliance
    elif step == 3 and listing:
        if request.FILES.get("title_deed"):
            listing.title_deed = request.FILES["title_deed"]
        if request.FILES.get("tax_certificate"):
            listing.tax_certificate = request.FILES["tax_certificate"]
        if request.FILES.get("utility_bills"):
            listing.utility_bills = request.FILES["utility_bills"]
        if request.FILES.get("kyc_doc"):
            listing.kyc_doc = request.FILES["kyc_doc"]
        if request.FILES.getlist("other_docs"):
            listing.other_docs = request.FILES.getlist("other_docs")
        listing.save()

    # Step 4: Pricing
    elif step == 4 and listing:
        listing.price = request.POST.get("price") or None
        listing.fractionalisation = request.POST.get("fractionalisation") == "true"
        listing.let_agent_suggest = request.POST.get("let_agent_suggest") == "true"
        listing.save()

    # # Step 5: Preview - nothing to save, just confirmation
    elif step == 5:
        pass

    return JsonResponse({"success": True, "listing_id": listing.id if listing else None})




# def submit_listing(request):
#     if request.method == 'POST':
#         listing_id = request.POST.get('listing_id')
#         if not listing_id:
#             return JsonResponse({'success': False, 'error': 'Missing listing ID'})

#         try:
#             listing = Listing.objects.get(id=listing_id)
#         except Listing.DoesNotExist:
#             return JsonResponse({'success': False, 'error': f'Listing {listing_id} not found'})

#         listing.status = 'submitted'
#         listing.save()
#         return JsonResponse({'success': True})

#     return JsonResponse({'success': False, 'error': 'Invalid request'})



def submit_listing(request):
    if request.method == 'POST':
        listing_id = request.POST.get('listing_id')
        if not listing_id:
            return JsonResponse({'success': False, 'error': 'Missing listing ID'})

        listing = get_object_or_404(Listing, id=listing_id)

        try:
            # 🔗 Upload files + metadata to IPFS
            metadata_cid = handle_property_upload(request.FILES, request.POST)

            # Save CID + mark submitted
            listing.metadata_cid = metadata_cid
            listing.status = 'submitted'
            listing.save()

            return JsonResponse({'success': True, 'metadata_cid': metadata_cid})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': 'Invalid request'})


from django.http import JsonResponse
from .utils.ipfs import handle_property_upload

def upload_listing_to_ipfs(request):
    if request.method == "POST":
        try:
            metadata_cid = handle_property_upload(request.FILES, request.POST)
            return JsonResponse({"success": True, "metadata_cid": metadata_cid})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request"})



def home(request):
    return render(request, 'landing.html')


def marketplace(request):
    return render(request, 'marketplace.html')


