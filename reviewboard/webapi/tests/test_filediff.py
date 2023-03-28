from reviewboard.scmtools.core import PRE_CREATION
        review_request.repository = self.create_repository(name='test-repo')
        review_request.repository = self.create_repository(name='test-repo')

    @webapi_test_template
    def test_get_with_diff_data(self):
        """Testing the GET <URL> API with diff data result"""
        repository = self.create_repository(tool_name='Git')
        review_request = self.create_review_request(
            repository=repository,
            publish=True)

        diffset = self.create_diffset(review_request)
        filediff = self.create_filediff(
            diffset,
            source_file='newfile.py',
            source_revision=PRE_CREATION,
            dest_file='newfile.py',
            dest_detail='20e43bb7c2d9f3a31768404ac71121804d806f7c',
            diff=(
                b"diff --git a/newfile.py b/newfile.py\n"
                b"new file mode 100644\n"
                b"index 0000000000000000000000000000000000000000.."
                b"8eaa5c1eacb55c43f5e00ed9dcd0c8da901f0c85\n"
                b"--- /dev/null\n"
                b"+++ b/newfile.py\n"
                b"@@ -0,0 +1 @@\n"
                b"+print('hello, world!')\n"
            ))

        rsp = self.api_get(
            get_filediff_item_url(filediff, review_request),
            HTTP_ACCEPT='application/vnd.reviewboard.org.diff.data+json',
            expected_status=200,
            expected_mimetype='application/json')

        self.assertEqual(
            rsp,
            {
                'diff_data': {
                    'binary': False,
                    'changed_chunk_indexes': [0],
                    'chunks': [
                        {
                            'change': 'insert',
                            'collapsable': False,
                            'index': 0,
                            'lines': [
                                [
                                    1,
                                    '',
                                    '',
                                    [],
                                    1,
                                    'print(&#x27;hello, world!&#x27;)',
                                    [],
                                    False,
                                ],
                            ],
                            'meta': {
                                'left_headers': [],
                                'right_headers': [],
                                'whitespace_chunk': False,
                                'whitespace_lines': [],
                            },
                            'numlines': 1,
                        },
                    ],
                    'new_file': True,
                    'num_changes': 1,
                },
                'stat': 'ok',
            })

    @webapi_test_template
    def test_get_with_diff_data_and_syntax_highlighting(self):
        """Testing the GET <URL> API with diff data result and
        ?syntax-highlighting=1
        """
        repository = self.create_repository(tool_name='Git')
        review_request = self.create_review_request(
            repository=repository,
            publish=True)

        diffset = self.create_diffset(review_request)
        filediff = self.create_filediff(
            diffset,
            source_file='newfile.py',
            source_revision=PRE_CREATION,
            dest_file='newfile.py',
            dest_detail='20e43bb7c2d9f3a31768404ac71121804d806f7c',
            diff=(
                b"diff --git a/newfile.py b/newfile.py\n"
                b"new file mode 100644\n"
                b"index 0000000000000000000000000000000000000000.."
                b"8eaa5c1eacb55c43f5e00ed9dcd0c8da901f0c85\n"
                b"--- /dev/null\n"
                b"+++ b/newfile.py\n"
                b"@@ -0,0 +1 @@\n"
                b"+print('hello, world!')\n"
            ))

        rsp = self.api_get(
            ('%s?syntax-highlighting=1'
             % get_filediff_item_url(filediff, review_request)),
            HTTP_ACCEPT='application/vnd.reviewboard.org.diff.data+json',
            expected_status=200,
            expected_mimetype='application/json')

        self.assertEqual(
            rsp,
            {
                'diff_data': {
                    'binary': False,
                    'changed_chunk_indexes': [0],
                    'chunks': [
                        {
                            'change': 'insert',
                            'collapsable': False,
                            'index': 0,
                            'lines': [
                                [
                                    1,
                                    '',
                                    '',
                                    [],
                                    1,
                                    '<span class="nb">print</span>'
                                    '<span class="p">(</span>'
                                    '<span class="s1">&#39;hello, '
                                    'world!&#39;</span>'
                                    '<span class="p">)</span>',
                                    [],
                                    False,
                                ],
                            ],
                            'meta': {
                                'left_headers': [],
                                'right_headers': [],
                                'whitespace_chunk': False,
                                'whitespace_lines': [],
                            },
                            'numlines': 1,
                        },
                    ],
                    'new_file': True,
                    'num_changes': 1,
                },
                'stat': 'ok',
            })