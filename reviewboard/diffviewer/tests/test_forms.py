import base64
import json

from django.test.client import RequestFactory
from django.utils import six
from djblets.siteconfig.models import SiteConfiguration
from reviewboard.diffviewer.errors import (DiffParserError, DiffTooBigError,
                                           EmptyDiffError)
from reviewboard.diffviewer.forms import (UploadCommitForm, UploadDiffForm,
                                          ValidateCommitForm)
from reviewboard.diffviewer.models import DiffSet, DiffSetHistory
from reviewboard.scmtools.errors import FileNotFoundError
class UploadCommitFormTests(SpyAgency, TestCase):
    """Unit tests for UploadCommitForm."""

    fixtures = ['test_scmtools']

    _default_form_data = {
        'base_commit_id': '1234',
        'basedir': '/',
        'commit_id': 'r1',
        'parent_id': 'r0',
        'commit_message': 'Message',
        'author_name': 'Author',
        'author_email': 'author@example.org',
        'author_date': '1970-01-01 00:00:00+0000',
        'committer_name': 'Committer',
        'committer_email': 'committer@example.org',
        'committer_date': '1970-01-01 00:00:00+0000',
    }

    def setUp(self):
        super(UploadCommitFormTests, self).setUp()

        self.repository = self.create_repository(tool_name='Git')
        self.spy_on(self.repository.get_file_exists,
                    call_fake=lambda *args, **kwargs: True)
        self.diffset = DiffSet.objects.create_empty(repository=self.repository)

    def test_create(self):
        """Testing UploadCommitForm.create"""
        diff = SimpleUploadedFile('diff',
                                  self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                  content_type='text/x-patch')

        form = UploadCommitForm(
            diffset=self.diffset,
            data=self._default_form_data.copy(),
            files={
                'diff': diff,
            })

        self.assertTrue(form.is_valid())
        commit = form.create()

        self.assertEqual(self.diffset.files.count(), 1)
        self.assertEqual(self.diffset.commits.count(), 1)
        self.assertEqual(commit.files.count(), 1)
        self.assertEqual(set(self.diffset.files.all()),
                         set(commit.files.all()))

    def test_clean_parent_diff_path(self):
        """Testing UploadCommitForm.clean() for a subsequent commit with a
        parent diff
        """
        diff = SimpleUploadedFile('diff',
                                  self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                  content_type='text/x-patch')
        parent_diff = SimpleUploadedFile('parent_diff',
                                         self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                         content_type='text/x-patch')

        form = UploadCommitForm(
            diffset=self.diffset,
            data=self._default_form_data.copy(),
            files={
                'diff': diff,
                'parent_diff': parent_diff,
            })

        self.assertTrue(form.is_valid())
        form.create()

        form = UploadCommitForm(
            diffset=self.diffset,
            data=dict(
                self._default_form_data,
                **{
                    'parent_id': 'r1',
                    'commit_id': 'r2',
                }
            ),
            files={
                'diff': diff,
                'parent_diff': parent_diff,
            })

        self.assertTrue(form.is_valid())
        self.assertNotIn('parent_diff', form.errors)

    def test_clean_published_diff(self):
        """Testing UploadCommitForm.clean() for a DiffSet that has already been
        published
        """
        diff = SimpleUploadedFile('diff',
                                  self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                  content_type='text/x-patch')

        form = UploadCommitForm(
            diffset=self.diffset,
            data=self._default_form_data,
            files={
                'diff': diff,
            })

        self.assertTrue(form.is_valid())
        form.create()

        self.diffset.history = DiffSetHistory.objects.create()
        self.diffset.save(update_fields=('history_id',))

        form = UploadCommitForm(
            diffset=self.diffset,
            data=dict(
                self._default_form_data,
                parent_id='r1',
                commit_id='r0',
            ),
            files={
                'diff_path': SimpleUploadedFile(
                    'diff',
                    self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                    content_type='text/x-patch'),
            })

        self.assertFalse(form.is_valid())
        self.assertNotEqual(form.non_field_errors, [])

    def test_clean_author_date(self):
        """Testing UploadCommitForm.clean_author_date"""
        diff = SimpleUploadedFile('diff',
                                  self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                  content_type='text/x-patch')

        form = UploadCommitForm(
            diffset=self.diffset,
            data=dict(self._default_form_data, **{
                'author_date': 'Jan 1 1970',
            }),
            files={
                'diff': diff,
            })

        self.assertFalse(form.is_valid())
        self.assertIn('author_date', form.errors)

    def test_clean_committer_date(self):
        """Testing UploadCommitForm.clean_committer_date"""
        diff = SimpleUploadedFile('diff',
                                  self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                  content_type='text/x-patch')

        form = UploadCommitForm(
            diffset=self.diffset,
            data=dict(self._default_form_data, **{
                'committer_date': 'Jun 1 1970',
            }),
            files={
                'diff': diff,
            })

        self.assertFalse(form.is_valid())
        self.assertIn('committer_date', form.errors)

    def test_clean_no_committer(self):
        """Testing UploadCommitForm.clean when no committer_ fields are present
        """
        field_names = {
            'committer_date',
            'committer_email',
            'committer_name',
        }

        diff = SimpleUploadedFile('diff',
                                  self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                  content_type='text/x-patch')

        form_data = self._default_form_data.copy()

        for field in field_names:
            del form_data[field]

        form = UploadCommitForm(
            diffset=self.diffset,
            data=form_data,
            files={
                'diff': diff,
            })

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            field: ['This field is required.']
            for field in field_names
        })

    def test_clean_commiter_unsupported(self):
        """Testing UploadCommitForm.clean when committer_ fields are present
        for a SCMTool that doesn't support them
        """
        self.repository.tool = Tool.objects.get(name='Mercurial')
        self.repository.save()

        diff = SimpleUploadedFile('diff',
                                  self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                  content_type='text/x-patch')

        form = UploadCommitForm(
            diffset=self.diffset,
            data=self._default_form_data.copy(),
            files={
                'diff': diff,
            })

        self.assertTrue(form.is_valid())

        self.assertNotIn('committer_date', form.cleaned_data)
        self.assertNotIn('committer_email', form.cleaned_data)
        self.assertNotIn('committer_name', form.cleaned_data)


        diff_file = SimpleUploadedFile('diff',
                                       self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
        diff_file = SimpleUploadedFile('diff',
                                       self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
        self.assertEqual(filediff.diff, self.DEFAULT_GIT_FILEDIFF_DATA_DIFF)
        self.assertEqual(f.source_revision, revisions[0].decode('utf-8'))
        self.assertEqual(f.dest_detail, revisions[1].decode('utf-8'))
        self.assertEqual(f.source_revision, revisions[0].decode('utf-8'))
        self.assertEqual(f.dest_detail, revisions[2].decode('utf-8'))


class ValidateCommitFormTests(SpyAgency, TestCase):
    """Unit tests for ValidateCommitForm."""

    fixtures = ['test_scmtools']

    _PARENT_DIFF_DATA = (
        b'diff --git a/README b/README\n'
        b'new file mode 100644\n'
        b'index 0000000..94bdd3e\n'
        b'--- /dev/null\n'
        b'+++ b/README\n'
        b'@@ -0,0 +2 @@\n'
        b'+blah blah\n'
        b'+blah blah\n'
    )

    @classmethod
    def setUpClass(cls):
        super(ValidateCommitFormTests, cls).setUpClass()

        cls.request_factory = RequestFactory()

    def setUp(self):
        super(ValidateCommitFormTests, self).setUp()

        self.repository = self.create_repository(tool_name='Git')
        self.request = self.request_factory.get('/')
        self.diff = SimpleUploadedFile('diff',
                                       self.DEFAULT_GIT_FILEDIFF_DATA_DIFF,
                                       content_type='text/x-patch')

    def test_clean_already_validated(self):
        """Testing ValidateCommitForm.clean for a commit that has already been
        validated
        """
        validation_info = self._base64_json({
            'r1': {
                'parent_id': 'r0',
                'tree': {
                    'added': [],
                    'removed': [],
                    'modified': [],
                },
            },
        })

        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r1',
                'parent_id': 'r0',
                'validation_info': validation_info,
            },
            files={
                'diff': self.diff,
            })

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'validation_info': ['This commit was already validated.'],
        })

    def test_clean_parent_not_validated(self):
        """Testing ValidateCommitForm.clean for a commit whose parent has not
        been validated
        """
        validation_info = self._base64_json({
            'r1': {
                'parent_id': 'r0',
                'tree': {
                    'added': [],
                    'removed': [],
                    'modified': [],
                },
            },
        })

        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r3',
                'parent_id': 'r2',
                'validation_info': validation_info,
            },
            files={
                'diff': self.diff,
            })

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'validation_info': ['The parent commit was not validated.'],
        })

    def test_clean_parent_diff_subsequent_commit(self):
        """Testing ValidateCommitForm.clean with a non-empty parent diff for
        a subsequent commit
        """
        validation_info = self._base64_json({
            'r1': {
                'parent_id': 'r0',
                'tree': {
                    'added': [],
                    'removed': [],
                    'modified': [],
                },
            },
        })

        parent_diff = SimpleUploadedFile('diff',
                                         self._PARENT_DIFF_DATA,
                                         content_type='text/x-patch')

        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r2',
                'parent_id': 'r1',
                'validation_info': validation_info,
            },
            files={
                'diff': self.diff,
                'parent_diff': parent_diff,
            })

        self.assertTrue(form.is_valid())

    def test_clean_validation_info(self):
        """Testing ValidateCommitForm.clean_validation_info"""
        validation_info = self._base64_json({
            'r1': {
                'parent_id': 'r0',
                'tree': {
                    'added': [],
                    'removed': [],
                    'modified': [],
                },
            },
        })

        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r2',
                'parent_id': 'r1',
                'validation_info': validation_info,
            },
            files={
                'diff': self.diff,
            })

        self.assertTrue(form.is_valid())

    def test_clean_validation_info_invalid_base64(self):
        """Testing ValidateCommitForm.clean_validation_info with
        non-base64-encoded data"""
        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r2',
                'parent_id': 'r1',
                'validation_info': 'This is not base64!',
            },
            files={
                'diff': self.diff,
            })

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'validation_info': [
                'Could not parse validation info "This is not base64!": '
                'Incorrect padding',
            ],
        })

    def test_clean_validation_info_invalid_json(self):
        """Testing ValidateCommitForm.clean_validation_info with base64-encoded
        non-json data
        """
        validation_info = base64.b64encode(b'Not valid json.')
        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r2',
                'parent_id': 'r1',
                'validation_info': validation_info,
            },
            files={
                'diff': self.diff,
            })

        self.assertFalse(form.is_valid())

        # Python 2 and 3 differ in the error contents you'll get when
        # attempting to load non-JSON data.
        if six.PY3:
            expected_error = 'Expecting value: line 1 column 1 (char 0)'
        else:
            expected_error = 'No JSON object could be decoded'

        self.assertEqual(form.errors, {
            'validation_info': [
                'Could not parse validation info "%s": %s'
                % (validation_info.decode('utf-8'), expected_error),
            ],
        })

    def test_validate_diff(self):
        """Testing ValidateCommitForm.validate_diff"""
        self.spy_on(self.repository.get_file_exists,
                    call_fake=lambda *args, **kwargs: True)
        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r1',
                'parent_id': 'r2',
            },
            files={
                'diff': self.diff,
            })

        self.assertTrue(form.is_valid())
        form.validate_diff()

    def test_validate_diff_subsequent_commit(self):
        """Testing ValidateCommitForm.validate_diff for a subsequent commit"""
        diff_content = (
            b'diff --git a/foo b/foo\n'
            b'index %s..%s 100644\n'
            b'--- a/foo\n'
            b'+++ b/foo\n'
            b'@@ -0,0 +1,2 @@\n'
            b'+This is not a new file.\n'
            % (b'a' * 40, b'b' * 40)
        )
        diff = SimpleUploadedFile('diff', diff_content,
                                  content_type='text/x-patch')

        validation_info = self._base64_json({
            'r1': {
                'parent_id': 'r0',
                'tree': {
                    'added': [{
                        'filename': 'foo',
                        'revision': 'a' * 40,
                    }],
                    'removed': [],
                    'modified': [],
                },
            },
        })

        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r2',
                'parent_id': 'r1',
                'validation_info': validation_info,
            },
            files={
                'diff': diff,
            })

        self.assertTrue(form.is_valid())
        form.validate_diff()

    def test_validate_diff_missing_files(self):
        """Testing ValidateCommitForm.validate_diff for a subsequent commit
        with missing files
        """
        validation_info = self._base64_json({
            'r1': {
                'parent_id': 'r0',
                'tree': {
                    'added': [],
                    'removed': [],
                    'modified': [],
                },
            },
        })

        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r2',
                'parent_id': 'r1',
                'validation_info': validation_info,
            },
            files={
                'diff': self.diff,
            })

        self.assertTrue(form.is_valid())

        with self.assertRaises(FileNotFoundError):
            form.validate_diff()

    def test_validate_diff_empty(self):
        """Testing ValidateCommitForm.validate_diff for an empty diff"""
        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r1',
                'parent_id': 'r0',
            },
            files={
                'diff': SimpleUploadedFile('diff', b' ',
                                           content_type='text/x-patch'),
            })

        self.assertTrue(form.is_valid())

        with self.assertRaises(EmptyDiffError):
            form.validate_diff()

    def test_validate_diff_too_big(self):
        """Testing ValidateCommitForm.validate_diff for a diff that is too
        large
        """
        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r1',
                'parent_id': 'r0',
            },
            files={
                'diff': self.diff,
            })

        self.assertTrue(form.is_valid())

        with self.assertRaises(DiffTooBigError):
            with self.siteconfig_settings({'diffviewer_max_diff_size': 1},
                                          reload_settings=False):
                form.validate_diff()

    def test_validate_diff_parser_error(self):
        """Testing ValidateCommitForm.validate_diff for an invalid diff"""
        form = ValidateCommitForm(
            repository=self.repository,
            request=self.request,
            data={
                'commit_id': 'r1',
                'parent_id': 'r0',
            },
            files={
                'diff': SimpleUploadedFile('diff', b'asdf',
                                           content_type='text/x-patch'),
            })

        self.assertTrue(form.is_valid())

        with self.assertRaises(DiffParserError):
            form.validate_diff()

    def _base64_json(self, data):
        """Return a Base64-encoded JSON payload.

        Args:
            data (object):
                The data to encode to JSON.

        Returns:
            bytes:
            The Base64-encoded JSON payload.
        """
        return base64.b64encode(json.dumps(data).encode('utf-8'))