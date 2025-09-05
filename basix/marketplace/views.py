from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages

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



def home(request):
    return render(request, 'landing.html')


def marketplace(request):
    return render(request, 'marketplace.html')
