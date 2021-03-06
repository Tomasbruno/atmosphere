"""
Atmosphere api email
"""
from django.utils.timezone import datetime

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from libcloud.common.types import InvalidCredsError

from threepio import logger

from api.permissions import InMaintenance, ApiAuthRequired
from authentication.protocol.ldap import lookupEmail

from core.models.provider import AccountProvider
from core.models.volume import convert_esh_volume

from service.volume import create_volume
from service.exceptions import OverQuotaError

from api import prepare_driver, failure_response, invalid_creds

from web.emails import feedback_email, quota_request_email, support_email

class Feedback(APIView):
    """
    Post feedback via RESTful API
    """
    permission_classes = (ApiAuthRequired,)
    def post(self, request):
        """
        Creates a new feedback email and sends it to admins
        """
        data = request.DATA
        required = ['message',]
        missing_keys = valid_post_data(data, required)
        if missing_keys:
            return keys_not_found(missing_keys)
        #Pass arguments
        user = request.user
        message = data['message']
        user_email = lookupEmail(user.username)
        result = feedback_email(request, user.username, user_email, message)
        return Response(result, status=status.HTTP_201_CREATED)


class QuotaEmail(APIView):
    """
    Post Quota Email via RESTful API
    """
    permission_classes = (ApiAuthRequired,)
    def post(self, request):
        """
        Creates a new Quota Request email and sends it to admins
        """
        data = request.DATA
        required = ['quota', 'reason']
        missing_keys = valid_post_data(data, required)
        if missing_keys:
            return keys_not_found(missing_keys)
        #Pass arguments
        username = request.user.username
        quota = data['quota']
        reason = data['reason']
        result = quota_request_email(request, username, quota, reason)
        return Response(result, status=status.HTTP_201_CREATED)


class SupportEmail(APIView):
    """
    """
    permission_classes = (ApiAuthRequired,)

    def post(self, request):
        """
        Creates a new support email and sends it to admins


        Post Support Email via RESTful API
        """
        data = request.DATA
        required = ['message','subject']
        missing_keys = valid_post_data(data, required)
        if missing_keys:
            return keys_not_found(missing_keys)
        #Pass arguments
        subject = data['subject']
        message = data['message']
        result = support_email(request, subject, message)
        return Response(result, status=status.HTTP_201_CREATED)


def valid_post_data(data, required_keys):
    """
    Return any missing required post key names.
    """
    return [key for key in required
            #Key must exist and have a non-empty value.
            if not ( key in data and len(data[key]) > 0)]


def keys_not_found(missing_keys):
    return failure_response(
        status.HTTP_400_BAD_REQUEST,
        'Missing required POST data variables : %s' % missing_keys)
