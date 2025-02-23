"""
Forms for LORE.
"""

from __future__ import unicode_literals

import logging
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.forms import (
    Form, FileField, ValidationError, ModelForm,
)
from django.db import transaction

from importer.tasks import import_file
from learningresources.models import Repository
from rest.tasks import track_task, IMPORT_TASK_TYPE

log = logging.getLogger(__name__)


class UploadForm(Form):
    """
    Form for allowing a user to upload an OLX course archive
    """
    course_file = FileField()

    def __init__(self, *args, **kwargs):
        """
        Fill in choice fields.
        """
        super(UploadForm, self).__init__(*args, **kwargs)

    def clean_course_file(self):
        """Only certain extensions are allowed."""
        upload = self.cleaned_data["course_file"]
        log.debug("checking filename %s", upload.name)
        for ext in (".zip", ".tar.gz", ".tgz"):
            if upload.name.endswith(ext):
                log.debug("the filename is good")
                upload.ext = ext
                log.debug("setting upload.ext to %s", ext)
                return upload
        log.debug("got to end, so the file is bad")
        raise ValidationError("Unsupported file type.")

    def save(self, user_id, repo_id, session):
        """
        Receives the request.FILES from the view.

        Args:
            user_id (int): primary key of the user uploading the course.
            repo_id (int): primary key of repository we're uploading to.
            session (SessionStore): User's session to store task data.
        """
        # Assumes a single file, because we only accept
        # one at a time.
        uploaded_file = self.cleaned_data["course_file"]

        # Save the uploaded file into a temp file.
        path = default_storage.save(
            '{prefix}/{random}{ext}'.format(
                prefix=settings.IMPORT_PATH_PREFIX,
                random=str(uuid.uuid4()),
                ext=uploaded_file.ext
            ),
            uploaded_file
        )
        task = import_file.delay(path, repo_id, user_id)

        repo_slug = Repository.objects.get(id=repo_id).slug
        # Save task data in session so we can keep track of it.
        track_task(session, task, IMPORT_TASK_TYPE, {
            "repo_slug": repo_slug,
            "path": path
        })


class RepositoryForm(ModelForm):
    """
    Form for the Repository object.
    """
    class Meta:  # pylint: disable=missing-docstring
        model = Repository
        fields = ("name", "description")

    # pylint: disable=signature-differs
    # The ModelForm.save() accepts "commit" and this doesn't, because
    # we always set commit=False then add the user because created_by is
    # not part of the form, and shouldn't be.
    @transaction.atomic
    def save(self, user):
        """
        Save a newly-created form.
        """
        repo = super(RepositoryForm, self).save(commit=False)
        repo.created_by = user
        repo.save()
        return repo
