"""
Views for the all apps
"""
from __future__ import unicode_literals

import logging
import mimetypes
import json
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.servers.basehttp import FileWrapper
from django.core.urlresolvers import reverse
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import (
    render,
    redirect,
)
from guardian.decorators import permission_required_or_403
from guardian.shortcuts import get_perms
from statsd.defaults.django import statsd

from learningresources.api import (
    get_repo,
    get_repos,
    NotFound,
    PermissionDenied as LorePermissionDenied,
)
from learningresources.models import (
    Repository,
    StaticAsset,
    STATIC_ASSET_PREFIX,
)
from rest.pagination import LorePagination
from roles.permissions import RepoPermission
from search.sorting import LoreSortingFields
from ui.forms import UploadForm, RepositoryForm

log = logging.getLogger(__name__)


@login_required
@permission_required_or_403(
    # pylint: disable=protected-access
    # the following string is "learningresources.import_course"
    # (unless the Repository model has been moved)
    '{}.{}'.format(
        Repository._meta.app_label,
        RepoPermission.import_course[0]
    ),
    (Repository, 'slug', 'repo_slug')
)
def upload(request, repo_slug):
    """
    Upload a OLX archive.
    """
    repo = get_repo(repo_slug, request.user.id)
    form = UploadForm()
    if request.method == "POST":
        form = UploadForm(
            data=request.POST, files=request.FILES)
        if form.is_valid():
            try:
                form.save(request.user.id, repo.id, request.session)
                return redirect("/repositories/{0}/".format(repo_slug))
            except ValueError as ex:
                # Coverage exception reasoning: After successful upload,
                # extraction, and validation, any error *should* be
                # "Duplicate course," and if it's not, it will be re-raised
                # and we'll code for it then.
                if "Duplicate course" not in ex.args:  # pragma: no cover
                    raise ex
                form.add_error("course_file", "Duplicate course")
    return render(
        request,
        "upload.html",
        {'form': form, "repo": repo},
    )


@login_required
def welcome(request):
    """
    Greet the user, show available repositories.
    """
    return render(
        request,
        "welcome.html",
        {
            "repos": get_repos(request.user.id),
            "support_email": settings.EMAIL_SUPPORT,
        }
    )


@login_required
@permission_required_or_403(
    # pylint: disable=protected-access
    # the following string is "learningresources.add_repo"
    # (unless the Repository model has been moved)
    '{}.add_{}'.format(
        Repository._meta.app_label,
        Repository._meta.model_name
    )
)
def create_repo(request):
    """
    Create a new repository.
    """
    form = RepositoryForm()
    if request.method == "POST":
        form = RepositoryForm(data=request.POST)
        if form.is_valid():
            repo = form.save(request.user)
            return redirect(reverse("repositories", args=(repo.slug,)))
    return render(
        request,
        "create_repo.html",
        {"form": form},
    )


@statsd.timer('lore.repository_view')
@login_required
def repository_view(request, repo_slug):
    """
    View for repository page.
    """
    try:
        repo = get_repo(repo_slug, request.user.id)
    except NotFound:
        raise Http404
    except LorePermissionDenied:
        raise PermissionDenied

    exports = request.session.get(
        'learning_resource_exports', {}).get(repo.slug, [])

    sortby = dict(request.GET.copy()).get('sortby', [])
    if (len(sortby) > 0 and
            sortby[0] in LoreSortingFields.all_sorting_fields()):
        sortby_field = sortby[0]
    else:
        # default value
        sortby_field = LoreSortingFields.DEFAULT_SORTING_FIELD

    sorting_options = {
        "current": LoreSortingFields.get_sorting_option(
            sortby_field),
        "all": LoreSortingFields.all_sorting_options_but(
            sortby_field)
    }

    try:
        page_size = int(request.GET.get(LorePagination.page_size_query_param))
    except (ValueError, KeyError, TypeError):
        page_size = LorePagination.page_size

    if page_size <= 0:
        page_size = LorePagination.page_size
    elif page_size > LorePagination.max_page_size:
        page_size = LorePagination.max_page_size

    context = {
        "repo": repo,
        "perms_on_cur_repo": get_perms(request.user, repo),
        "sorting_options_json": json.dumps(sorting_options),
        "exports_json": json.dumps(exports),
        "page_size_json": json.dumps(page_size)
    }

    return render(
        request,
        "repository.html",
        context
    )


@login_required
def repository_data_view(request, repo_slug):
    """
    View for repository page.
    """
    try:
        repo = get_repo(repo_slug, request.user.id)
    except NotFound:
        raise Http404
    except LorePermissionDenied:
        raise PermissionDenied

    context = {
        "repo": repo
    }

    return render(
        request,
        "data.html",
        context
    )


@login_required
def serve_static_assets(request, path):
    """
    View to serve media files in case settings.DEFAULT_FILE_STORAGE
    is django.core.files.storage.FileSystemStorage
    """
    # first check if the user has access to the file
    media_path = os.path.join(STATIC_ASSET_PREFIX, path)
    file_path = os.path.join(settings.MEDIA_ROOT, media_path)
    static_assets = StaticAsset.objects.filter(asset=media_path)
    if not static_assets:
        raise Http404()
    static_asset = static_assets.first()
    if (RepoPermission.view_repo[0] not in
            get_perms(request.user, static_asset.course.repository)):
        raise PermissionDenied()
    filename = os.path.basename(file_path)
    response = StreamingHttpResponse(
        FileWrapper(open(file_path, 'rb')),
        content_type=mimetypes.guess_type(file_path)[0]
    )
    response['Content-Length'] = os.path.getsize(file_path)
    response['Content-Disposition'] = "attachment; filename=%s" % filename
    return response


@login_required
def serve_resource_exports(request, path):
    """
    View to serve media files in case settings.DEFAULT_FILE_STORAGE
    is django.core.files.storage.FileSystemStorage
    """
    media_path = os.path.join(settings.EXPORT_PATH_PREFIX, path)
    file_path = os.path.join(settings.MEDIA_ROOT, media_path)

    # There is only one path that will work here, make sure it matches exactly.
    expected_filename = "{name}_exports.tar.gz".format(
        name=request.user.username
    )
    expected_path = os.path.join(
        settings.MEDIA_ROOT, settings.EXPORT_PATH_PREFIX, expected_filename)
    if expected_path != file_path:
        raise PermissionDenied()
    if not os.path.exists(file_path):
        raise Http404()

    filename = os.path.basename(file_path)
    response = StreamingHttpResponse(
        FileWrapper(open(file_path, 'rb')),
        content_type=mimetypes.guess_type(file_path)[0]
    )
    response['Content-Length'] = os.path.getsize(file_path)
    response['Content-Disposition'] = "attachment; filename={filename}".format(
        filename=filename
    )
    return response
