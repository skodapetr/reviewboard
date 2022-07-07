"""The Review Board e-mail message class and methods for generating e-mails."""

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from djblets.mail.message import EmailMessage as DjbletsEmailMessage
from djblets.mail.utils import (build_email_address,
                                build_email_address_for_user)
from djblets.siteconfig.models import SiteConfiguration

from reviewboard.accounts.pages import AuthenticationPage
from reviewboard.admin.server import build_server_url, get_server_url
from reviewboard.notifications.email.hooks import \
    filter_email_recipients_from_hooks
from reviewboard.notifications.email.utils import (
    build_recipients,
    recipients_to_addresses)
from reviewboard.reviews.models import Group
from reviewboard.reviews.signals import (review_request_published,
                                         review_published, reply_published,
                                         review_request_closed)


logger = logging.getLogger(__name__)


MAX_FILENAME_HEADERS_LENGTH = 8192

#: The number of additional characters each ``X-ReviewBoard-Diff-For`` has.
#:
#: We calculate the length the value of each header at runtime. However,
#: ``X-ReviewBoard-Diff-For: `` is present before the value, and the line
#: terminates with a ``\r\n``.
HEADER_ADDITIONAL_CHARACTERS_LENGTH = (len(b'\r\n') +
                                       len(b'X-ReviewBoard-Diff-For: '))


class EmailMessage(DjbletsEmailMessage):
    """An e-mail message.

    This class only differs from Djblets'
    :py:class:`~djblets.email.message.EmailMessage` by using the site
    configuration to generate some e-mail settings.
    """

    def __init__(self, *args, **kwargs):
        siteconfig = SiteConfiguration.objects.get_current()
        auto_generated = siteconfig.get('mail_enable_autogenerated_header')
        from_spoofing = siteconfig.get('mail_from_spoofing')

        super(EmailMessage, self).__init__(
            auto_generated=auto_generated,
            prevent_auto_responses=True,
            from_spoofing=from_spoofing,
            *args,
            **kwargs)


def _ensure_unicode(text):
    """Return a unicode object for the given text.

    Args:
        text (bytes or unicode):
            The text to decode.

    Returns:
        unicode: The decoded text.
    """
    if isinstance(text, bytes):
        text = text.decode('utf-8')

    return text


def _get_server_base_url():
    """Return the base URL of the server (without a trailing /).

    Returns:
        unicode:
        The server base URL without a trailing slash.

        For a site at :samp:`{scheme}://example.com/site-root`, this function
        will return :samp:`{scheme}://example.com`.
    """
    return build_server_url('/')[:-1]


def prepare_base_review_request_mail(user, review_request, subject,
                                     in_reply_to, to_field, cc_field,
                                     template_name_base, context=None,
                                     extra_headers=None):
    """Return a customized review request e-mail.

    This is intended to be called by one of the ``prepare_{type}_mail``
    functions in this file. This method builds up a common context that all
    review request-related e-mails will use to render their templates, as well
    as handling user preferences regarding e-mail and add adding additional
    headers.

    Args:
        user (django.contrib.auth.models.User):
            The user who is sending the e-mail.

        review_request (reviewboard.reviews.models.review_request.ReviewRequest):
            The review request this e-mail is regarding.

        subject (unicode):
            The e-mail subject line.

        in_reply_to (unicode):
            The e-mail message ID this message is in response to or ``None``.

        to_field (set):
            The set of :py:class:`~django.contrib.auth.models.User` and
            :py:class`~reviewboard.reviews.models.group.Group`s to this e-mail
            will be sent to.

        cc_field (set):
            The set of :py:class:`~django.contrib.auth.models.User` and
            :py:class`~reviewboard.reviews.models.group.Group`s to be CC'ed on
            the e-mail.

        template_name_base (unicode):
            The name of the template to use to generate the e-mail without its
            extension. The plain-text version of the e-mail will append
            ``.txt`` to this and and the rich-text version of the e-mail will
            append ``.html``.

        context (dict, optional):
            Optional additional template rendering context.

        extra_headers (dict, optional):
            Optional additional headers to include.

    Returns:
        EmailMessage:
        The prepared e-mail message.
    """
    user_email = build_email_address_for_user(user)
    to_field = recipients_to_addresses(to_field, review_request.id)
    cc_field = recipients_to_addresses(cc_field, review_request.id) - to_field

    if not user.should_send_own_updates():
        to_field.discard(user_email)
        cc_field.discard(user_email)

    if not to_field and not cc_field:
        # This e-mail would have no recipients, so we won't send it.
        return None

    if not context:
        context = {}

    context.update({
        'user': user,
        'site_url': _get_server_base_url(),
        'review_request': review_request,
    })
    local_site = review_request.local_site

    if local_site:
        context['local_site_name'] = local_site.name

    text_body = render_to_string(template_name='%s.txt' % template_name_base,
                                 context=context)
    html_body = render_to_string(template_name='%s.html' % template_name_base,
                                 context=context)
    server_url = get_server_url(local_site=local_site)

    headers = MultiValueDict({
        'X-ReviewBoard-URL': [server_url],
        'X-ReviewRequest-URL': [
            build_server_url(review_request.get_absolute_url(),
                             local_site=local_site)
        ],
        'X-ReviewGroup': [', '.join(
            review_request.target_groups.values_list('name', flat=True)
        )],
    })

    if extra_headers:
        if not isinstance(extra_headers, MultiValueDict):
            extra_headers = MultiValueDict(
                (key, [value])
                for key, value in extra_headers.items()
            )

        headers.update(extra_headers)

    if review_request.repository:
        headers['X-ReviewRequest-Repository'] = review_request.repository.name

    latest_diffset = review_request.get_latest_diffset()

    if latest_diffset:
        modified_files = set()

        for filediff in latest_diffset.files.all():
            if not filediff.is_new:
                modified_files.add(filediff.source_file)

            if not filediff.deleted:
                modified_files.add(filediff.dest_file)

        # The following code segment deals with the case where the client adds
        # a significant amount of files with large names. We limit the number
        # of headers; when more than 8192 characters are reached, we stop
        # adding filename headers.
        current_header_length = 0

        for filename in modified_files:
            current_header_length += (HEADER_ADDITIONAL_CHARACTERS_LENGTH +
                                      len(filename))

            if current_header_length > MAX_FILENAME_HEADERS_LENGTH:
                logger.warning(
                    'Unable to store all filenames in the '
                    'X-ReviewBoard-Diff-For headers when sending e-mail for '
                    'review request %s: The header size exceeds the limit of '
                    '%s. Remaining headers have been omitted.',
                    review_request.display_id,
                    MAX_FILENAME_HEADERS_LENGTH)
                break

            headers.appendlist('X-ReviewBoard-Diff-For', filename)

    if settings.DEFAULT_FROM_EMAIL:
        sender = build_email_address(full_name=user.get_full_name(),
                                     email=settings.DEFAULT_FROM_EMAIL)
    else:
        sender = None

    return EmailMessage(subject=subject.strip(),
                        text_body=text_body.encode('utf-8'),
                        html_body=html_body.encode('utf-8'),
                        from_email=user_email,
                        sender=sender,
                        to=list(to_field),
                        cc=list(cc_field),
                        in_reply_to=in_reply_to,
                        headers=headers)


def prepare_password_changed_mail(user):
    """Return an e-mail notifying the user that their password changed.

    Args:
        user (django.contrib.auth.models.User):
            The user whose password changed.

    Returns:
        EmailMessage:
        The generated message.
    """
    server_url = get_server_url()

    context = {
        'api_token_url': AuthenticationPage.get_absolute_url(),
        'has_api_tokens': user.webapi_tokens.exists(),
        'server_url': server_url,
        'user': user,
    }

    user_email = build_email_address_for_user(user)
    text_body = render_to_string(
        template_name='notifications/password_changed.txt',
        context=context)
    html_body = render_to_string(
        template_name='notifications/password_changed.html',
        context=context)

    return EmailMessage(
        subject='Password changed for user "%s" on %s' % (user.username,
                                                          server_url),
        text_body=text_body,
        html_body=html_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        sender=settings.DEFAULT_FROM_EMAIL,
        to=(user_email,))


def prepare_reply_published_mail(user, reply, review, review_request):
    """Return an e-mail representing the supplied reply to a review.

    Args:
        user (django.contrib.auth.models.User):
            The user who published the reply.

        reply (reviewboard.reviews.models.review.Review):
            The review reply to send an e-mail about.

        review (reviewboard.reviews.models.review.Review):
            The review that was replied to.

        review_request (reviewboard.reviews.models.review_request.ReviewRequest):
            The review request.

    Returns:
        EmailMessage:
        The generated e-mail message.
    """
    from reviewboard.reviews.views import build_diff_comment_fragments

    extra_context = {
        'user': reply.user,
        'review': review,
        'reply': reply,
        'site_url': _get_server_base_url(),
    }

    extra_context['comment_entries'] = build_diff_comment_fragments(
        reply.comments.order_by('filediff', 'first_line'),
        extra_context,
        'notifications/email_diff_comment_fragment.html')[1]

    to_field, cc_field = build_recipients(
        user=reply.user,
        review_request=review_request,
        extra_recipients=review_request.review_participants)

    to_field, cc_field = filter_email_recipients_from_hooks(
        to_field, cc_field, reply_published,
        reply=reply,
        user=user,
        review=review,
        review_request=review_request)

    summary = _ensure_unicode(review_request.summary)

    return prepare_base_review_request_mail(
        user, review_request,
        'Re: Review Request %d: %s' % (review_request.display_id, summary),
        review.email_message_id, to_field, cc_field,
        'notifications/reply_email', extra_context)


def prepare_review_published_mail(user, review, review_request, request,
                                  to_owner_only=False):
    """Return an e-mail representing the supplied review.

    Args:
        user (django.contrib.auth.models.User):
            The user who published the review.

        review (reviewboard.reviews.models.review.Review):
            The review to send an e-mail about.

        review_request (reviewboard.reviews.models.review_request.ReviewRequest):
            The review request that was reviewed.

        to_owner_only (bool):
            Whether or not the review should be sent to the submitter only.

    Returns:
        EmailMessage:
        The generated e-mail message.
    """
    from reviewboard.reviews.views import build_diff_comment_fragments

    review.ordered_comments = review.comments.order_by('filediff',
                                                       'first_line')
    has_issues = (review.ship_it and
                  review.has_comments(only_issues=True))
    extra_context = {
        'user': review.user,
        'review': review,
        'has_issues': has_issues,
        'request': request,
        'site_url': _get_server_base_url(),
    }

    extra_headers = {}

    if review.ship_it:
        extra_headers['X-ReviewBoard-ShipIt'] = '1'

        if review.ship_it_only:
            extra_headers['X-ReviewBoard-ShipIt-Only'] = '1'

    extra_context['comment_entries'] = build_diff_comment_fragments(
        review.ordered_comments, extra_context,
        'notifications/email_diff_comment_fragment.html')[1]

    limit_to = None

    if to_owner_only:
        limit_to = {review_request.submitter, review.user}

    to_field, cc_field = build_recipients(review.user, review_request,
                                          limit_recipients_to=limit_to)

    to_field, cc_field = filter_email_recipients_from_hooks(
        to_field, cc_field, review_published,
        user=user,
        review=review,
        to_owner_only=to_owner_only,
        review_request=review_request)

    summary = _ensure_unicode(review_request.summary)

    return prepare_base_review_request_mail(
        review.user, review_request,
        'Re: Review Request %d: %s' % (review_request.display_id, summary),
        review_request.email_message_id, to_field, cc_field,
        'notifications/review_email', extra_context,
        extra_headers=extra_headers)


def prepare_review_request_mail(user, review_request, changedesc=None,
                                close_type=None):
    """Return an e-mail representing the supplied review request.

    Args:
        user (django.contrib.auth.models.User):
            The user who triggered the e-mail (i.e., they published or
            closed he review request).

        review_request (reviewboard.reviews.models.ReviewRequest):
            The review request to send an e-mail about.

        changedesc (reviewboard.changedescs.models.ChangeDescription):
            An optional change description showing what has changed in the
            review request, possibly with explanatory text from the
            submitter. This is created when saving a draft on a public review
            request and will be ``None`` when publishing initially. This is
            used by the template to add contextual (updated) flags to inform
            people what has changed.

        close_type (unicode):
            How the review request was closed or ``None`` if it was
            published. If this is not ``None`` it must be one of:

            * :py:attr:`~reviewboard.reviews.models.ReviewRequest.SUBMITTED`
            * :py:attr:`~reviewboard.reviews.models.ReviewRequest.DISCARDED`

    Returns:
        EmailMessage:
        The e-mail message representing the review request.
    """
    if not user:
        user = review_request.submitter

    summary = _ensure_unicode(review_request.summary)
    subject = 'Review Request %d: %s' % (review_request.display_id, summary)

    reply_message_id = review_request.email_message_id
    extra_recipients = None

    if reply_message_id:
        # Fancy quoted "replies".
        subject = 'Re: %s' % subject
        extra_recipients = review_request.review_participants

    extra_context = {}
    extra_filter_kwargs = {}

    if close_type:
        changedesc = review_request.changedescs.filter(public=True).latest()
        signal = review_request_closed
        extra_filter_kwargs['close_type'] = close_type
    else:
        signal = review_request_published

    limit_recipients_to = None

    if changedesc:
        fields_changed = changedesc.fields_changed
        changed_field_names = set(fields_changed)
        extra_context.update({
            'change_text': changedesc.text,
            'change_rich_text': changedesc.rich_text,
            'changes': fields_changed,
        })

        if (changed_field_names and
            changed_field_names <= {'target_people', 'target_groups'}):
            # If the only changes are to the target reviewers, try to send a
            # much more targeted e-mail. Rather than having it be sent out to
            # everyone, it will only be sent to new reviewers.
            limit_recipients_to = set()

            for model, field in ((User, 'target_people'),
                                 (Group, 'target_groups')):
                if field in changed_field_names:
                    limit_recipients_to.update(
                        model.objects.filter(pk__in=[
                            item[2]
                            for item in fields_changed[field]['added']
                        ]))

    to_field, cc_field = build_recipients(
        user, review_request, extra_recipients,
        limit_recipients_to=limit_recipients_to)

    to_field, cc_field = filter_email_recipients_from_hooks(
        to_field, cc_field, signal,
        review_request=review_request,
        user=user,
        **extra_filter_kwargs)

    return prepare_base_review_request_mail(
        user, review_request, subject, reply_message_id, to_field,
        cc_field, 'notifications/review_request_email', extra_context)


def prepare_user_registered_mail(user):
    """Prepare an e-mail to the administrators notifying of a new user.

    Args:
        user (django.contrib.auth.models.User):
            The user who registered.

    Returns:
        EmailMessage:
        The generated e-mail.
    """
    subject = 'New %s user registration for %s' % (settings.PRODUCT_NAME,
                                                   user.username)

    context = {
        'site_url': _get_server_base_url(),
        'user': user,
        'user_url': build_server_url(reverse('admin:auth_user_change',
                                             args=(user.id,))),
    }

    text_message = render_to_string(
        template_name='notifications/new_user_email.txt',
        context=context)
    html_message = render_to_string(
        template_name='notifications/new_user_email.html',
        context=context)

    return EmailMessage(
        subject=subject.strip(),
        text_body=text_message,
        html_body=html_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        sender=settings.DEFAULT_FROM_EMAIL,
        to=[
            build_email_address(full_name=admin[0], email=admin[1])
            for admin in settings.ADMINS
        ])


def prepare_webapi_token_mail(webapi_token, op):
    """Return an e-mail message notifying a user about a WebAPI token change.

    Args:
        webapi_token (reviewboard.notifications.models.WebAPIToken):
            The token that was created, updated, or deleted.

        op (unicode):
            The operation on the token. This is one of:

            * ``'created'``
            * ``'updated'``
            * ``'deleted'``

    Returns:
        EmailMessage:
        The generated e-mail.
    """
    product_name = settings.PRODUCT_NAME

    if op == 'created':
        subject = 'New %s API token created' % product_name
        template_name = 'notifications/api_token_created'
    elif op == 'updated':
        subject = '%s API token updated' % product_name
        template_name = 'notifications/api_token_updated'
    elif op == 'deleted':
        subject = '%s API token deleted' % product_name
        template_name = 'notifications/api_token_deleted'
    else:
        raise ValueError('Unexpected op "%s" passed to mail_webapi_token.'
                         % op)

    user = webapi_token.user
    user_email = build_email_address_for_user(user)

    context = {
        'api_token': webapi_token,
        'api_tokens_url': AuthenticationPage.get_absolute_url(),
        'partial_token': '%s...' % webapi_token.token[:10],
        'user': user,
        'site_root_url': get_server_url(),
        'PRODUCT_NAME': product_name,
    }

    text_message = render_to_string(
        template_name='%s.txt' % template_name,
        context=context)
    html_message = render_to_string(
        template_name='%s.html' % template_name,
        context=context)

    return EmailMessage(
        subject=subject,
        text_body=text_message,
        html_body=html_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        sender=settings.DEFAULT_FROM_EMAIL,
        to=[user_email])
