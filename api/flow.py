"""
Atmosphere api flow.
"""
from rest_framework.views import APIView
from rest_framework.response import Response


from core.models.size import convert_esh_size

from api.serializers import ProviderSizeSerializer

from api import prepare_driver, invalid_creds
from api.permissions import InMaintenance, ApiAuthRequired


class FlowList(APIView):
    """
    List all active flows for a provider and identity.
    """
    permission_classes = (ApiAuthRequired,)

    def get(self, request, provider_id, identity_id):
        """
        """
        user = request.user
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)
        serialized_data = []
        response = Response(serialized_data)
        return response


class Flow(APIView):
    """
    View details on a flow.
    """
    permission_classes = (ApiAuthRequired,)

    def get(self, request, provider_id, identity_id, flow_id):
        """
        """
        user = request.user
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)
        esh_size = []
        serialized_data = []
        response = Response(serialized_data)
        return response
