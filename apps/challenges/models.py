from __future__ import unicode_literals

import random
import tempfile
import zipfile
from collections import namedtuple
from os.path import basename, isfile, join

import yaml

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField, JSONField
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import signals
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from base.models import (
    TimeStampedModel,
    create_post_model_field,
    model_field_name,
)

from base.utils import RandomFileName, get_slug, get_queue_name
from hosts.models import ChallengeHost
from participants.models import ParticipantTeam, Participant
from .aws_utils import restart_workers_signal_callback


@receiver(pre_save, sender="challenges.Challenge")
def save_challenge_slug(sender, instance, **kwargs):
    title = get_slug(instance.title)
    instance.slug = "{}-{}".format(title, instance.pk)


class Challenge(TimeStampedModel):

    """Model representing a hosted Challenge"""

    def __init__(self, *args, **kwargs):
        super(Challenge, self).__init__(*args, **kwargs)
        self._original_evaluation_script = self.evaluation_script

    title = models.CharField(max_length=100, db_index=True)
    short_description = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    terms_and_conditions = models.TextField(null=True, blank=True)
    submission_guidelines = models.TextField(null=True, blank=True)
    evaluation_details = models.TextField(null=True, blank=True)
    image = models.ImageField(
        upload_to=RandomFileName("logos"),
        null=True,
        blank=True,
        verbose_name="Logo",
    )
    start_date = models.DateTimeField(
        null=True, blank=True, verbose_name="Start Date (UTC)", db_index=True
    )
    end_date = models.DateTimeField(
        null=True, blank=True, verbose_name="End Date (UTC)", db_index=True
    )
    creator = models.ForeignKey(
        "hosts.ChallengeHostTeam", related_name="challenge_creator"
    )
    published = models.BooleanField(
        default=False, verbose_name="Publicly Available", db_index=True
    )
    is_registration_open = models.BooleanField(default=True)
    enable_forum = models.BooleanField(default=True)
    forum_url = models.URLField(max_length=100, blank=True, null=True)
    leaderboard_description = models.TextField(null=True, blank=True)
    anonymous_leaderboard = models.BooleanField(default=False)
    participant_teams = models.ManyToManyField(ParticipantTeam, blank=True)
    is_disabled = models.BooleanField(default=False, db_index=True)
    evaluation_script = models.FileField(
        default=False, upload_to=RandomFileName("evaluation_scripts")
    )  # should be zip format
    approved_by_admin = models.BooleanField(
        default=False, verbose_name="Approved By Admin", db_index=True
    )
    featured = models.BooleanField(
        default=False, verbose_name="Featured", db_index=True
    )
    allowed_email_domains = ArrayField(
        models.CharField(max_length=50, blank=True), default=[], blank=True
    )
    blocked_email_domains = ArrayField(
        models.CharField(max_length=50, blank=True), default=[], blank=True
    )
    banned_email_ids = ArrayField(
        models.TextField(null=True, blank=True),
        default=[],
        blank=True,
        null=True
    )
    remote_evaluation = models.BooleanField(
        default=False, verbose_name="Remote Evaluation", db_index=True
    )
    queue = models.CharField(
        max_length=200,
        default="",
        verbose_name="SQS queue name",
        db_index=True,
    )
    is_docker_based = models.BooleanField(
        default=False, verbose_name="Is Docker Based", db_index=True
    )
    slug = models.SlugField(max_length=200, null=True, unique=True)
    max_docker_image_size = models.BigIntegerField(
        default=42949672960, null=True, blank=True
    )  # Default is 40 GB
    max_concurrent_submission_evaluation = models.PositiveIntegerField(
        default=100000
    )
    aws_account_id = models.CharField(
        max_length=200, default="", null=True, blank=True
    )
    aws_access_key_id = models.CharField(
        max_length=200, default="", null=True, blank=True
    )
    aws_secret_access_key = models.CharField(
        max_length=200, default="", null=True, blank=True
    )
    aws_region = models.CharField(
        max_length=50, default="us-east-1", null=True, blank=True
    )
    use_host_credentials = models.BooleanField(default=False)
    cli_version = models.CharField(
        max_length=20, verbose_name="evalai-cli version", null=True, blank=True
    )
    # The number of active workers on Fargate of the challenge.
    workers = models.IntegerField(
        null=True, blank=True, default=None
    )
    # The task definition ARN for the challenge, used for updating and creating service.
    task_def_arn = models.CharField(null=True, blank=True, max_length=2048, default="")

    class Meta:
        app_label = "challenges"
        db_table = "challenge"

    def __str__(self):
        """Returns the title of Challenge"""
        return self.title

    def get_image_url(self):
        """Returns the url of logo of Challenge"""
        if self.image:
            return self.image.url
        return None

    def get_evaluation_script_path(self):
        """Returns the path of evaluation script"""
        if self.evaluation_script:
            return self.evaluation_script.url
        return None

    def get_start_date(self):
        """Returns the start date of Challenge"""
        return self.start_date

    def get_end_date(self):
        """Returns the end date of Challenge"""
        return self.end_date

    @property
    def is_active(self):
        """Returns if the challenge is active or not"""
        if self.start_date < timezone.now() and self.end_date > timezone.now():
            return True
        return False

    @classmethod
    def load_from_zip(cls, zip_file_path, challenge_host_team):
        from .utils import get_file_content
        from .serializers import (
            ChallengePhaseCreateSerializer,
            DatasetSplitSerializer,
            LeaderboardSerializer,
            ZipChallengePhaseSplitSerializer,
            ZipChallengeSerializer,
        )

        zip_ref = zipfile.ZipFile(zip_file_path, "r")
        BASE_LOCATION = tempfile.mkdtemp()
        zip_ref.extractall(BASE_LOCATION)
        zip_ref.close()

        for name in zip_ref.namelist():
            if (name.endswith(".yaml") or name.endswith(".yml")) and (
                not name.startswith("__MACOSX")
            ):  # Ignore YAML File in __MACOSX Directory
                yaml_file = name
                extracted_folder_name = yaml_file.split(basename(yaml_file))[0]
                break
        else:
            raise Exception('No yaml file found in zip root!')

        with open(
            join(BASE_LOCATION, yaml_file), "r"
        ) as stream:
            yaml_file_data = yaml.safe_load(stream)

        evaluation_script = yaml_file_data["evaluation_script"]
        evaluation_script_path = join(
            BASE_LOCATION,
            extracted_folder_name,
            evaluation_script,
        )

        # Check for evaluation script file in extracted zip folder.
        with open(evaluation_script_path, "rb") as challenge_evaluation_script:
            challenge_evaluation_script_file = ContentFile(
                challenge_evaluation_script.read(), evaluation_script_path
            )
        challenge_phases_data = yaml_file_data["challenge_phases"]

        for data in challenge_phases_data:
            test_annotation_file = data["test_annotation_file"]
            test_annotation_file_path = join(
                BASE_LOCATION,
                extracted_folder_name,
                test_annotation_file,
            )

        image = yaml_file_data.get("image")
        if image and (
            image.endswith(".jpg")
            or image.endswith(".jpeg")
            or image.endswith(".png")
        ):
            challenge_image_path = join(
                BASE_LOCATION, extracted_folder_name, image
            )
            if isfile(challenge_image_path):
                challenge_image_file = ContentFile(
                    get_file_content(challenge_image_path, "rb"), image
                )
            else:
                challenge_image_file = None
        else:
            challenge_image_file = None

        challenge_description_file_path = join(
            BASE_LOCATION,
            extracted_folder_name,
            yaml_file_data["description"],
        )
        if challenge_description_file_path.endswith(".html") and isfile(
            challenge_description_file_path
        ):
            yaml_file_data["description"] = get_file_content(
                challenge_description_file_path, "rb"
            ).decode("utf-8")

        challenge_evaluation_details_file_path = join(
            BASE_LOCATION,
            extracted_folder_name,
            yaml_file_data["evaluation_details"],
        )

        if challenge_evaluation_details_file_path.endswith(".html") and isfile(
            challenge_evaluation_details_file_path
        ):
            yaml_file_data["evaluation_details"] = get_file_content(
                challenge_evaluation_details_file_path, "rb"
            ).decode("utf-8")
        else:
            yaml_file_data["evaluation_details"] = None

        challenge_terms_and_cond_file_path = join(
            BASE_LOCATION,
            extracted_folder_name,
            yaml_file_data["terms_and_conditions"],
        )
        if challenge_terms_and_cond_file_path.endswith(".html") and isfile(
            challenge_terms_and_cond_file_path
        ):
            yaml_file_data["terms_and_conditions"] = get_file_content(
                challenge_terms_and_cond_file_path, "rb"
            ).decode("utf-8")
        else:
            yaml_file_data["terms_and_conditions"] = None

        submission_guidelines_file_path = join(
            BASE_LOCATION,
            extracted_folder_name,
            yaml_file_data["submission_guidelines"],
        )
        if submission_guidelines_file_path.endswith(".html") and isfile(
            submission_guidelines_file_path
        ):
            yaml_file_data["submission_guidelines"] = get_file_content(
                submission_guidelines_file_path, "rb"
            ).decode("utf-8")
        else:
            yaml_file_data["submission_guidelines"] = None

        serializer = ZipChallengeSerializer(
            data=yaml_file_data,
            context={
                "request": namedtuple('Request', 'method')(method='NOTGET'),
                "challenge_host_team": challenge_host_team,
                "image": challenge_image_file,
                "evaluation_script": challenge_evaluation_script_file,
            },
        )
        if serializer.is_valid():
            serializer.save()
            challenge = serializer.instance
            queue_name = get_queue_name(challenge.title)
            challenge.queue = queue_name
            challenge.save()
        else:
            raise Exception(serializer.errors)

        # Create Leaderboard
        yaml_file_data_of_leaderboard = yaml_file_data["leaderboard"]
        leaderboard_ids = {}
        for data in yaml_file_data_of_leaderboard:
            serializer = LeaderboardSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                leaderboard_ids[str(data["id"])] = serializer.instance.pk
            else:
                raise Exception(serializer.errors)

        # Create Challenge Phase
        challenge_phase_ids = {}
        for data in challenge_phases_data:
            # Check for challenge phase description file
            phase_description_file_path = join(
                BASE_LOCATION,
                extracted_folder_name,
                data["description"],
            )
            if phase_description_file_path.endswith(".html") and isfile(
                phase_description_file_path
            ):
                data["description"] = get_file_content(
                    phase_description_file_path, "rb"
                ).decode("utf-8")
            else:
                data["description"] = None

            test_annotation_file = data["test_annotation_file"]
            data["slug"] = "{}-{}-{}".format(
                challenge.title.split(" ")[0].lower(),
                data["codename"].replace(" ", "-").lower(),
                challenge.pk,
            )[:198]
            if test_annotation_file:
                test_annotation_file_path = join(
                    BASE_LOCATION,
                    extracted_folder_name,
                    test_annotation_file,
                )
            if isfile(test_annotation_file_path):
                with open(
                    test_annotation_file_path, "rb"
                ) as test_annotation_file:
                    challenge_test_annotation_file = ContentFile(
                        test_annotation_file.read(),
                        test_annotation_file_path,
                    )

            serializer = ChallengePhaseCreateSerializer(
                data=data,
                context={
                    "challenge": challenge,
                    "test_annotation": challenge_test_annotation_file,
                },
            )
            if serializer.is_valid():
                serializer.save()
                challenge_phase_ids[
                    str(data["id"])
                ] = serializer.instance.pk
            else:
                raise Exception(serializer.errors)

        # Create Dataset Splits
        yaml_file_data_of_dataset_split = yaml_file_data["dataset_splits"]
        dataset_split_ids = {}
        for data in yaml_file_data_of_dataset_split:
            serializer = DatasetSplitSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                dataset_split_ids[str(data["id"])] = serializer.instance.pk
            else:
                # Return error when dataset split name is not unique.
                raise Exception(serializer.errors)

        # Create Challenge Phase Splits
        challenge_phase_splits_data = yaml_file_data[
            "challenge_phase_splits"
        ]

        for data in challenge_phase_splits_data:
            challenge_phase = challenge_phase_ids[
                str(data["challenge_phase_id"])
            ]
            leaderboard = leaderboard_ids[str(data["leaderboard_id"])]
            dataset_split = dataset_split_ids[
                str(data["dataset_split_id"])
            ]
            visibility = data["visibility"]

            data = {
                "challenge_phase": challenge_phase,
                "leaderboard": leaderboard,
                "dataset_split": dataset_split,
                "visibility": visibility,
            }

            serializer = ZipChallengePhaseSplitSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
            else:
                raise Exception(serializer.errors)

        if not challenge.is_docker_based:
            # Add the Challenge Host as a test participant.
            emails = challenge_host_team.get_all_challenge_host_email()
            team_name = "Host_{}_Team".format(random.randint(1, 100000))
            participant_host_team = ParticipantTeam(
                team_name=team_name,
                created_by=challenge_host_team.created_by,
            )
            participant_host_team.save()
            for email in emails:
                user = User.objects.get(email=email)
                host = Participant(
                    user=user,
                    status=Participant.ACCEPTED,
                    team=participant_host_team,
                )
                host.save()
            challenge.participant_teams.add(participant_host_team)

        print("Success creating challenge")
        return challenge


signals.post_save.connect(
    model_field_name(field_name="evaluation_script")(create_post_model_field),
    sender=Challenge,
    weak=False,
)
signals.post_save.connect(
    model_field_name(field_name="evaluation_script")(restart_workers_signal_callback),
    sender=Challenge,
    weak=False,
)


class DatasetSplit(TimeStampedModel):
    name = models.CharField(max_length=100)
    codename = models.CharField(max_length=100)

    def __str__(self):
        return self.name

    class Meta:
        app_label = "challenges"
        db_table = "dataset_split"


class ChallengePhase(TimeStampedModel):

    """Model representing a Challenge Phase"""

    def __init__(self, *args, **kwargs):
        super(ChallengePhase, self).__init__(*args, **kwargs)
        self._original_test_annotation = self.test_annotation

    name = models.CharField(max_length=100, db_index=True)
    description = models.TextField()
    leaderboard_public = models.BooleanField(default=False)
    start_date = models.DateTimeField(
        null=True, blank=True, verbose_name="Start Date (UTC)", db_index=True
    )
    end_date = models.DateTimeField(
        null=True, blank=True, verbose_name="End Date (UTC)", db_index=True
    )
    challenge = models.ForeignKey("Challenge")
    is_public = models.BooleanField(default=False)
    is_submission_public = models.BooleanField(default=False)
    test_annotation = models.FileField(
        upload_to=RandomFileName("test_annotations"), default=False
    )
    max_submissions_per_day = models.PositiveIntegerField(
        default=100000, db_index=True
    )
    max_submissions_per_month = models.PositiveIntegerField(
        default=100000, db_index=True
    )
    max_submissions = models.PositiveIntegerField(
        default=100000, db_index=True
    )
    max_concurrent_submissions_allowed = models.PositiveIntegerField(default=3)
    codename = models.CharField(max_length=100, default="Phase Code Name")
    dataset_split = models.ManyToManyField(
        DatasetSplit, blank=True, through="ChallengePhaseSplit"
    )
    allowed_email_ids = ArrayField(
        models.TextField(null=True, blank=True),
        default=[],
        blank=True,
        null=True,
    )
    slug = models.SlugField(max_length=200, null=True, unique=True)

    class Meta:
        app_label = "challenges"
        db_table = "challenge_phase"
        unique_together = (("codename", "challenge"),)

    def __str__(self):
        """Returns the name of Phase"""
        return self.name

    def get_start_date(self):
        """Returns the start date of Phase"""
        return self.start_date

    def get_end_date(self):
        """Returns the end date of Challenge"""
        return self.end_date

    @property
    def is_active(self):
        """Returns if the challenge is active or not"""
        if self.start_date < timezone.now() and self.end_date > timezone.now():
            return True
        return False

    def save(self, *args, **kwargs):

        # If the max_submissions_per_day is less than the max_concurrent_submissions_allowed.
        if (
            self.max_submissions_per_day
            < self.max_concurrent_submissions_allowed
        ):
            self.max_concurrent_submissions_allowed = (
                self.max_submissions_per_day
            )

        challenge_phase_instance = super(ChallengePhase, self).save(
            *args, **kwargs
        )
        return challenge_phase_instance


signals.post_save.connect(
    model_field_name(field_name="test_annotation")(create_post_model_field),
    sender=ChallengePhase,
    weak=False,
)
signals.post_save.connect(
    model_field_name(field_name="test_annotation")(restart_workers_signal_callback),
    sender=ChallengePhase,
    weak=False,
)


class Leaderboard(TimeStampedModel):

    schema = JSONField()

    def __str__(self):
        return "{}".format(self.id)

    class Meta:
        app_label = "challenges"
        db_table = "leaderboard"


class ChallengePhaseSplit(TimeStampedModel):

    # visibility options
    HOST = 1
    OWNER_AND_HOST = 2
    PUBLIC = 3

    VISIBILITY_OPTIONS = (
        (HOST, "host"),
        (OWNER_AND_HOST, "owner and host"),
        (PUBLIC, "public"),
    )

    challenge_phase = models.ForeignKey("ChallengePhase")
    dataset_split = models.ForeignKey("DatasetSplit")
    leaderboard = models.ForeignKey("Leaderboard")
    visibility = models.PositiveSmallIntegerField(
        choices=VISIBILITY_OPTIONS, default=PUBLIC
    )
    leaderboard_decimal_precision = models.PositiveIntegerField(default=2)
    is_leaderboard_order_descending = models.BooleanField(default=True)

    def __str__(self):
        return "{0} : {1}".format(
            self.challenge_phase.name, self.dataset_split.name
        )

    class Meta:
        app_label = "challenges"
        db_table = "challenge_phase_split"


class LeaderboardData(TimeStampedModel):

    challenge_phase_split = models.ForeignKey("ChallengePhaseSplit")
    submission = models.ForeignKey("jobs.Submission")
    leaderboard = models.ForeignKey("Leaderboard")
    result = JSONField()
    error = JSONField(null=True, blank=True)

    def __str__(self):
        return "{0} : {1}".format(self.challenge_phase_split, self.submission)

    class Meta:
        app_label = "challenges"
        db_table = "leaderboard_data"


class ChallengeConfiguration(TimeStampedModel):
    """
    Model to store zip file for challenge creation.
    """

    user = models.ForeignKey(User)
    challenge = models.OneToOneField(Challenge, null=True, blank=True)
    zip_configuration = models.FileField(
        upload_to=RandomFileName("zip_configuration_files/challenge_zip")
    )
    is_created = models.BooleanField(default=False, db_index=True)
    stdout_file = models.FileField(
        upload_to=RandomFileName("zip_configuration_files/challenge_zip"),
        null=True,
        blank=True,
    )
    stderr_file = models.FileField(
        upload_to=RandomFileName("zip_configuration_files/challenge_zip"),
        null=True,
        blank=True,
    )

    class Meta:
        app_label = "challenges"
        db_table = "challenge_zip_configuration"


class StarChallenge(TimeStampedModel):
    """
    Model to star a challenge
    """

    user = models.ForeignKey(User)
    challenge = models.ForeignKey(Challenge)
    is_starred = models.BooleanField(default=False, db_index=True)

    class Meta:
        app_label = "challenges"
        db_table = "starred_challenge"


class UserInvitation(TimeStampedModel):
    """
    Model to store invitation status
    """

    ACCEPTED = "accepted"
    PENDING = "pending"

    STATUS_OPTIONS = ((ACCEPTED, ACCEPTED), (PENDING, PENDING))
    email = models.EmailField(max_length=200)
    invitation_key = models.CharField(max_length=200)
    status = models.CharField(
        max_length=30, choices=STATUS_OPTIONS, db_index=True
    )
    challenge = models.ForeignKey(Challenge, related_name="challenge")
    user = models.ForeignKey(User)
    invited_by = models.ForeignKey(ChallengeHost)

    class Meta:
        app_label = "challenges"
        db_table = "invite_user_to_challenge"

    def __str__(self):
        """Returns the email of the user"""
        return self.email
