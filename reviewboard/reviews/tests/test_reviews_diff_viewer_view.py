from __future__ import annotations

from reviewboard.testing.testcase import BaseFileDiffAncestorTests, TestCase
    def test_interdiff(self) -> None:
        self.assertEqual(files[0]['orig_filename'], '/newfile')
        self.assertEqual(files[1]['orig_filename'], '/readme')
    def test_interdiff_new_file(self) -> None:
        self.assertEqual(files[0]['orig_filename'], '/newfile')
    def test_with_filenames_option(self) -> None:
    def test_with_filenames_option_normalized(self) -> None:


class CommitsTests(BaseFileDiffAncestorTests):
    """Tests for ReviewsDiffViewerView with commit history."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.set_up_filediffs()

        review = self.create_review(review_request=self.review_request,
                                    publish=True)
        self.cumulative_comment = self.create_diff_comment(
            review=review,
            filediff=self.diffset.cumulative_files[0])

        commit1 = self.diff_commits[1]
        commit2 = self.diff_commits[2]

        # Comment from the base commit to a tip
        self.commit_comment1 = self.create_diff_comment(
            review=review,
            filediff=commit2.files.get(dest_file='bar'))

        # Comment from one commit to another
        self.commit_comment2 = self.create_diff_comment(
            review=review,
            filediff=commit2.files.get(dest_file='bar'))
        self.commit_comment2.base_filediff_id = \
            commit1.files.get(dest_file='bar').pk
        self.commit_comment2.save(update_fields=['extra_data'])

    def test_comment_data_with_cumulative_diff(self) -> None:
        """Testing ReviewsDiffViewerView comment data with cumulative diff"""
        response = self.client.get(
            local_site_reverse(
                'view-diff-revision',
                kwargs={
                    'review_request_id': self.review_request.display_id,
                    'revision': self.diffset.revision,
                }))
        self.assertEqual(response.status_code, 200)

        files = response.context['diff_context']['files']

        for f in files:
            comment_blocks = f['serialized_comment_blocks']

            if f['id'] == self.cumulative_comment.filediff_id:
                self.assertEqual(len(comment_blocks), 1)

                comments = list(comment_blocks.values())[0]
                self.assertEqual(comments[0]['comment_id'],
                                 self.cumulative_comment.pk)
            else:
                # All other files in the diff should show no comments on them.
                self.assertEqual(comment_blocks, {})

    def test_comment_data_with_commit_range1(self) -> None:
        """Testing ReviewsDiffViewerView comment data with a commit range from
        the base commit
        """
        response = self.client.get(
            local_site_reverse(
                'view-diff-revision',
                kwargs={
                    'review_request_id': self.review_request.display_id,
                    'revision': self.diffset.revision,
                }),
            {
                'tip-commit-id': self.diff_commits[2].pk,
            })
        self.assertEqual(response.status_code, 200)

        files = response.context['diff_context']['files']

        for f in files:
            comment_blocks = f['serialized_comment_blocks']

            if f['id'] == self.commit_comment1.filediff_id:
                self.assertEqual(len(comment_blocks), 1)

                comments = list(comment_blocks.values())[0]
                self.assertEqual(comments[0]['comment_id'],
                                 self.commit_comment1.pk)
            else:
                # All other files in the diff should show no comments on them.
                self.assertEqual(comment_blocks, {})

    def test_comment_data_with_commit_range2(self) -> None:
        """Testing ReviewsDiffViewerView comment data with a commit range in
        the middle
        """
        response = self.client.get(
            local_site_reverse(
                'view-diff-revision',
                kwargs={
                    'review_request_id': self.review_request.display_id,
                    'revision': self.diffset.revision,
                }),
            {
                'base-commit-id': self.diff_commits[1].pk,
                'tip-commit-id': self.diff_commits[2].pk,
            })
        self.assertEqual(response.status_code, 200)

        files = response.context['diff_context']['files']

        for f in files:
            comment_blocks = f['serialized_comment_blocks']

            if f['id'] == self.commit_comment2.filediff_id:
                self.assertEqual(len(comment_blocks), 1)

                comments = list(comment_blocks.values())[0]
                self.assertEqual(comments[0]['comment_id'],
                                 self.commit_comment2.pk)
            else:
                # All other files in the diff should show no comments on them.
                self.assertEqual(comment_blocks, {})