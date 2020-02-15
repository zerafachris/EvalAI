import os

from allauth.account.models import EmailAddress

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction

from challenges.models import Challenge
from hosts.models import ChallengeHost, ChallengeHostTeam


def get_team_and_host():
    email = "hack@g2net.eu"
    users = User.objects.filter(email=email)
    if users.exists():
        host_user = users.get()
        host_team = ChallengeHostTeam.objects.get(created_by=host_user)
    else:
        host_user = User.objects.create_user(
            email=email,
            username="g2net_hack",
            password="qwerty12345",
            is_staff=True,
            is_superuser=True,
        )
        EmailAddress.objects.create(user=host_user, email=email, verified=True, primary=True)

        # Create challenge host team with challenge host
        host_team = ChallengeHostTeam.objects.create(
            team_name="g2net_hack",
            created_by=host_user,
        )
    return host_user, host_team


def add_challenge():
    host_user, host_team = get_team_and_host()
    ChallengeHost.objects.create(user=host_user, team_name=host_team, status=ChallengeHost.SELF, permissions=ChallengeHost.ADMIN)
    challenge_zip = open(
        os.path.join(settings.BASE_DIR, 'g2net_challenge/challenge_config.zip'), 'rb'
    )
    challenge = Challenge.load_from_zip(challenge_zip, host_team)
    challenge.approved_by_admin = True
    challenge.save()


def run(*args):
    print("Adding challenge")
    with transaction.atomic():
        add_challenge()
