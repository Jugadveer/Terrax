from django.shortcuts import render

def home(request):
    return render(request, 'landing.html')


def marketplace(request):
    return render(request, 'marketplace.html')
