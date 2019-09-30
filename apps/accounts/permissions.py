from allauth.account.models import EmailAddress
from django.conf import settings
from rest_framework import permissions


class HasVerifiedEmail(permissions.BasePermission):
    """
    Permission class for if the user has verified the email or not
    """

    message = "Please verify your email first!"

    def has_permission(self, request, view):

        if request.user.is_anonymous:
            return True
        else:
            email_query = EmailAddress.objects.filter(user=request.user)
            require_verified = settings.ACCOUNT_EMAIL_REQUIRED
            if require_verified:
                email_query = email_query.filter(verified=True)
            if email_query.exists():
                return True
            else:
                return False
