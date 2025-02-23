"""
A subclass of the Django TestCase which handles
often-needed features, like creating an authenticated
test client.
"""
from __future__ import unicode_literals

import logging
from os.path import abspath, dirname, join
import shutil
import subprocess
from tempfile import mkdtemp
import uuid

from django.conf import settings
from django.contrib.auth.models import User, Permission
from django.core.files.storage import default_storage
from django.test import Client
from django.test.testcases import TestCase
from django.test.utils import override_settings

from learningresources.api import (
    create_repo,
    create_course,
    create_resource,
    update_description_path
)
from learningresources.models import Repository, StaticAsset
from search.utils import recreate_index, refresh_index, remove_index

log = logging.getLogger(__name__)
# Using the md5 hasher speeds up tests.
hashers = ('django.contrib.auth.hashers.MD5PasswordHasher',)


@override_settings(PASSWORD_HASHERS=hashers)
class LoreTestCase(TestCase):
    """Handle often-needed things in tests."""
    # pylint: disable=too-many-instance-attributes
    USERNAME = 'test'
    PASSWORD = 'test'
    USERNAME_NO_REPO = 'test2'

    ADD_REPO_PERM = 'add_{}'.format(
        Repository._meta.model_name  # pylint: disable=protected-access
    )

    def login(self, username):
        """assumes the password is the same"""
        self.client.login(
            username=username,
            password=self.PASSWORD
        )

    def logout(self):
        """just a client logout"""
        self.client.logout()

    def assert_status_code(self, url, code, return_body=False):
        """Asserts the status code of GET attempt"""
        resp = self.client.get(url, follow=True)
        self.assertTrue(resp.status_code == code)
        if return_body:
            return resp.content.decode("utf-8")

    def create_resource(self, **kwargs):
        """Creates a learning resource with extra fields"""
        learn_res = create_resource(
            course=self.course,
            parent=kwargs.get('parent'),
            resource_type=kwargs.get('resource_type', "example"),
            title=kwargs.get('title', "other silly example"),
            content_xml=kwargs.get('content_xml', "<blah>other blah</blah>"),
            mpath=kwargs.get('mpath', "/otherblah"),
            url_name=kwargs.get('url_name'),
            dpath=''
        )
        learn_res.xa_nr_views = kwargs.get('xa_nr_views', 0)
        learn_res.xa_nr_attempts = kwargs.get('xa_nr_attempts', 0)
        learn_res.xa_avg_grade = kwargs.get('xa_avg_grade', 0)
        learn_res.save()
        update_description_path(learn_res)
        return learn_res

    def setUp(self):
        """set up"""
        super(LoreTestCase, self).setUp()
        recreate_index()
        self.user = User.objects.create_user(
            username=self.USERNAME, password=self.PASSWORD
        )
        add_repo_perm = Permission.objects.get(codename=self.ADD_REPO_PERM)
        self.user.user_permissions.add(add_repo_perm)
        # user without permission to add a repo
        self.user_norepo = User.objects.create_user(
            username=self.USERNAME_NO_REPO, password=self.PASSWORD
        )

        self.repo = create_repo(
            name="test repo",
            description="just a test",
            user_id=self.user.id,
        )
        self.course = create_course(
            org="test-org",
            repo_id=self.repo.id,
            course_number="infinity",
            run="Febtober",
            user_id=self.user.id,
        )
        self.resource = self.create_resource(
            course=self.course,
            parent=None,
            resource_type="example",
            title="silly example",
            content_xml="<blah>blah</blah>",
            mpath="/blah",
            url_name="url_name1",
        )

        self.toy_resource_count = 18  # Resources in toy course.
        self.toy_asset_count = 5  # Static assets in toy course.
        self.client = Client()

        self.login(username=self.USERNAME)
        refresh_index()

    def tearDown(self):
        """Clean up Elasticsearch and static assets between tests."""
        for static_asset in StaticAsset.objects.all():
            default_storage.delete(static_asset.asset)
        remove_index()

    def _make_archive(self, path, make_zip=False, ext=None):
        """
        Create an archive of specified type from path with given extension, and
        add it to the django default_storage.

        Args:
            path (str): Path to folder to copy
            make_zip (bool): If True, make zip file, else make tar.gz
            ext (str): Extension to use, if None use default.
                .zip for zip and .tar.gz for gzipped tar)

        Returns:
            str: default_storage path to copy
        """
        archive_type = 'zip' if make_zip else 'gztar'
        if make_zip and ext is None:
            ext = '.zip'
        elif not make_zip and ext is None:
            ext = '.tar.gz'

        temp_dir = mkdtemp()
        copy_path = join(temp_dir, 'course')
        self.addCleanup(shutil.rmtree, temp_dir)
        # Copy a symlink dereferenced version and archive it, using
        # subprocess here instead of shutil.copytree because of:
        # https://bugs.python.org/issue21697
        subprocess.call(['cp', '-LR', path, copy_path])
        new_archive = shutil.make_archive(
            join(temp_dir, 'archive'), archive_type, copy_path
        )

        copied_path = default_storage.save(
            '{prefix}/{random}{ext}'.format(
                prefix=settings.IMPORT_PATH_PREFIX,
                random=str(uuid.uuid4()),
                ext=ext
            ),
            open(new_archive, 'rb')
        )
        self.addCleanup(default_storage.delete, copied_path)
        return copied_path

    def get_course_zip(self):
        """
        Get the path to the demo course. Creates a copy, because the
        importer deletes the file during cleanup.

        Returns:
            unicode: absolute path to zip file
        """
        path = join(
            abspath(dirname(__file__)), "testdata", "courses", "simple"
        )
        return self._make_archive(path, True)

    def get_course_multiple_zip(self):
        """
        Get the path to the demo course. Creates a copy, because the
        importer deletes the file during cleanup.

        Returns:
            unicode: absolute path to zip file
        """
        path = join(
            abspath(dirname(__file__)), "testdata", "courses", "two_courses"
        )
        return self._make_archive(path, False)

    def get_course_single_tarball(self):
        """
        Get the path to a course with course.xml in the root
        of the archive.
        Returns:
            unicode: absolute path to tarball.
        """
        path = join(abspath(dirname(__file__)), "testdata", "courses", "toy")
        return self._make_archive(path, False, '.tgz')
