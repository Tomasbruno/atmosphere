from datetime import datetime
import time

from django.core.paginator import Paginator,\
    PageNotAnInteger, EmptyPage
from django.db.models import Q

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from libcloud.common.types import InvalidCredsError

from threepio import logger


from core.models import AtmosphereUser as User
from core.models.provider import AccountProvider
from core.models.instance import convert_esh_instance
from core.models.instance import Instance as CoreInstance
from core.models.size import convert_esh_size
from core.models.volume import convert_esh_volume

from service import task
from service.deploy import build_script
from service.instance import redeploy_init, reboot_instance,\
    launch_instance, resize_instance, confirm_resize,\
    start_instance, resume_instance,\
    stop_instance, suspend_instance,\
    update_instance_metadata

from service.quota import check_over_quota
from service.exceptions import OverAllocationError, OverQuotaError,\
    SizeNotAvailable, HypervisorCapacityError

from api import failure_response, prepare_driver, invalid_creds
from api.permissions import ApiAuthRequired
from api.serializers import InstanceSerializer, PaginatedInstanceSerializer
from api.serializers import InstanceHistorySerializer,\
    PaginatedInstanceHistorySerializer
from api.serializers import VolumeSerializer


class InstanceList(APIView):
    """
    Instances are the objects created when you launch a machine. They are
    represented by a unique ID, randomly generated on launch, important
    attributes of an Instance are:
    Name, Status (building, active, suspended), Size, Machine"""

    permission_classes = (ApiAuthRequired,)
    
    def get(self, request, provider_id, identity_id):
        """
        Returns a list of all instances
        """
        user = request.user
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)

        instance_list_method = esh_driver.list_instances

        if AccountProvider.objects.filter(identity__id=identity_id):
            # Instance list method changes when using the OPENSTACK provider
            instance_list_method = esh_driver.list_all_instances
        try:
            esh_instance_list = instance_list_method()
        except InvalidCredsError:
            return invalid_creds(provider_id, identity_id)

        core_instance_list = [convert_esh_instance(esh_driver,
                                                   inst,
                                                   provider_id,
                                                   identity_id,
                                                   user)
                              for inst in esh_instance_list]

        #TODO: Core/Auth checks for shared instances

        serialized_data = InstanceSerializer(core_instance_list,
                                             context={'user':request.user},
                                             many=True).data
        response = Response(serialized_data)
        response['Cache-Control'] = 'no-cache'
        return response

    def post(self, request, provider_id, identity_id, format=None):
        """
        Instance Class:
        Launches an instance based on the params
        Returns a single instance

        Parameters: machine_alias, size_alias, username

        TODO: Create a 'reverse' using the instance-id to pass
        the URL for the newly created instance
        I.e: url = "/provider/1/instance/1/i-12345678"
        """
        data = request.DATA
        user = request.user
        #Check the data is valid
        missing_keys = valid_post_data(data)
        if missing_keys:
            return keys_not_found(missing_keys)

        #Pass these as args
        size_alias = data.pop('size_alias')
        machine_alias = data.pop('machine_alias')
        hypervisor_name = data.pop('hypervisor',None)
        try:
            core_instance = launch_instance(user, provider_id, identity_id,
                                            size_alias, machine_alias, 
                                            ex_availability_zone=hypervisor_name,
                                            **data)
        except OverQuotaError, oqe:
            return over_quota(oqe)
        except OverAllocationError, oae:
            return over_quota(oae)
        except SizeNotAvailable, snae:
            return size_not_availabe(snae)
        except InvalidCredsError:
            return invalid_creds(provider_id, identity_id)
        except Exception as exc:
            logger.exception("Encountered a generic exception. "
                             "Returning 409-CONFLICT")
            return failure_response(status.HTTP_409_CONFLICT,
                                    exc.message)

        serializer = InstanceSerializer(core_instance,
                                        context={'user':request.user},
                                        data=data)
        #NEVER WRONG
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)


class InstanceHistory(APIView):
    """List of instance history for specific instance."""

    permission_classes = (ApiAuthRequired,)
    
    def get(self, request, provider_id=None, identity_id=None):
        """
        Authentication required, Retrieve a list of previously launched instances.
        """
        data = request.DATA
        params = request.QUERY_PARAMS.copy()
        user = User.objects.filter(username=request.user)
        if user and len(user) > 0:
            user = user[0]
        else:
            return failure_response(status.HTTP_401_UNAUTHORIZED,
                                    'User not found')
        page = params.pop('page', None)
        emulate_name = params.pop('username', None)
        try:
            # Support for staff users to emulate a specific user history
            if user.is_staff and emulate_name:
                emualate_name = emulate_name[0]  # Querystring conversion
                user = User.objects.get(username=emulate_name)
            # List of all instances created by user
            history_instance_list = CoreInstance.objects.filter(
                created_by=user).order_by("-start_date")
            #Filter the list based on query strings
            for filter_key, value in params.items():
                if 'start_date' == filter_key:
                    history_instance_list = history_instance_list.filter(
                        start_date__gt=value)
                elif 'end_date' == filter_key:
                    history_instance_list = history_instance_list.filter(
                        Q(end_date=None) |
                        Q(end_date__lt=value))
                elif 'ip_address' == filter_key:
                    history_instance_list = history_instance_list.filter(
                        ip_address__contains=value)
                elif 'alias' == filter_key:
                    history_instance_list = history_instance_list.filter(
                        provider_alias__contains=value)
        except Exception as e:
            return failure_response(
                status.HTTP_400_BAD_REQUEST,
                'Bad query string caused filter validation errors : %s'
                % (e,))
        if page:
            paginator = Paginator(history_instance_list, 5)
            try:
                history_instance_page = paginator.page(page)
            except PageNotAnInteger:
                # If page is not an integer, deliver first page.
                history_instance_page = paginator.page(1)
            except EmptyPage:
                # Page is out of range.
                # deliver last page of results.
                history_instance_page = paginator.page(paginator.num_pages)
            serialized_data = \
                PaginatedInstanceHistorySerializer(
                    history_instance_page).data
        else:
            serialized_data = InstanceHistorySerializer(history_instance_list,
                                                        many=True).data
        response = Response(serialized_data)
        response['Cache-Control'] = 'no-cache'
        return response


class InstanceAction(APIView):
    """
    This endpoint will allow you to run a specific action on an instance.
    The GET method will retrieve all available actions and any parameters that are required.
    The POST method expects DATA: {"action":...}
                            Returns: 200, data: {'result':'success',...}
                                     On Error, a more specfific message applies.
    Data variables:
     ___
    * action - The action you wish to take on your instance
    * action_params - any parameters required (as detailed on the api) to run the requested action.

    Instances are the objects created when you launch a machine. They are
    represented by a unique ID, randomly generated on launch, important
    attributes of an Instance are:
    Name, Status (building, active, suspended), Size, Machine"""

    permission_classes = (ApiAuthRequired,)
    
    def get(self, request, provider_id, identity_id, instance_id):
        """Authentication Required, List all available instance actions ,including necessary parameters.
        """
        api_response = [
                {"action":"attach_volume",
                 "action_params":{
                     "volume_id":"required",
                     "device":"optional",
                     "mount_location":"optional"},
                 "description":"Attaches the volume <id> to instance"},
                {"action":"detach_volume",
                 "action_params":{"volume_id":"required"},
                 "description":"Detaches the volume <id> to instance"},
                {"action":"resize",
                 "action_params":{"size":"required"},
                 "description":"Resize instance to size <id>"},
                {"action":"confirm_resize",
                 "description":"Confirm the instance works after resize."},
                {"action":"revert_resize",
                 "description":"Revert the instance if resize fails."},
                {"action":"suspend",
                 "description":"Suspend the instance."},
                {"action":"resume",
                 "description":"Resume the instance."},
                {"action":"start",
                 "description":"Start the instance."},
                {"action":"stop",
                 "description":"Stop the instance."},
                {"action":"reboot",
                 "action_params":{"reboot_type":"optional"},
                 "description":"Stop the instance."},
                {"action":"console",
                 "description":"Get noVNC Console."}]
        response = Response(api_response, status=status.HTTP_200_OK)
        return response

    def post(self, request, provider_id, identity_id, instance_id):
        """Authentication Required, Attempt a specific instance action, including necessary parameters.
        """
        #Service-specific call to action
        action_params = request.DATA
        if not action_params.get('action', None):
            return failure_response(
                status.HTTP_400_BAD_REQUEST,
                'POST request to /action require a BODY with \'action\'.')
        result_obj = None
        user = request.user
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)
        instance_list_method = esh_driver.list_instances
        if AccountProvider.objects.filter(identity__id=identity_id):
            # Instance list method changes when using the OPENSTACK provider
            instance_list_method = esh_driver.list_all_instances
        try:
            esh_instance_list = instance_list_method()
        except InvalidCredsError:
            return invalid_creds(provider_id, identity_id)

        esh_instance = esh_driver.get_instance(instance_id)
        if not esh_instance:
            return failure_response(
                status.HTTP_400_BAD_REQUEST,
                'Instance %s no longer exists' % (instance_id,))
        action = action_params['action']
        try:
            if 'volume' in action:
                volume_id = action_params.get('volume_id')
                if 'attach_volume' == action:
                    mount_location = action_params.get('mount_location', None)
                    if mount_location == 'null' or mount_location == 'None':
                        mount_location = None
                    device = action_params.get('device', None)
                    if device == 'null' or device == 'None':
                        device = None
                    task.attach_volume_task(esh_driver, esh_instance.alias,
                                            volume_id, device, mount_location)
                elif 'detach_volume' == action:
                    (result, error_msg) = task.detach_volume_task(
                        esh_driver,
                        esh_instance.alias,
                        volume_id)
                    if not result and error_msg:
                        #Return reason for failed detachment
                        return failure_response(
                            status.HTTP_400_BAD_REQUEST,
                            error_msg)
                #Task complete, convert the volume and return the object
                esh_volume = esh_driver.get_volume(volume_id)
                core_volume = convert_esh_volume(esh_volume,
                                                 provider_id,
                                                 identity_id,
                                                 user)
                result_obj = VolumeSerializer(core_volume,
                                              context={'user':request.user}
                                              ).data
            elif 'resize' == action:
                size_alias = action_params.get('size', '')
                if type(size_alias) == int:
                    size_alias = str(size_alias)
                resize_instance(esh_driver, esh_instance, size_alias,
                               provider_id, identity_id, user)
            elif 'confirm_resize' == action:
                confirm_resize(esh_driver, esh_instance,
                               provider_id, identity_id, user)
            elif 'revert_resize' == action:
                esh_driver.revert_resize_instance(esh_instance)
            elif 'redeploy' == action:
                redeploy_init(esh_driver, esh_instance, countdown=None)
            elif 'resume' == action:
                resume_instance(esh_driver, esh_instance,
                                provider_id, identity_id, user)
            elif 'suspend' == action:
                suspend_instance(esh_driver, esh_instance,
                                 provider_id, identity_id, user)
            elif 'start' == action:
                start_instance(esh_driver, esh_instance,
                               provider_id, identity_id, user)
            elif 'stop' == action:
                stop_instance(esh_driver, esh_instance,
                              provider_id, identity_id, user)
            elif 'reset_network' == action:
                esh_driver.reset_network(esh_instance)
            elif 'console' == action:
                result_obj = esh_driver._connection.ex_vnc_console(esh_instance)
            elif 'reboot' == action:
                reboot_type = action_params.get('reboot_type', 'SOFT')
                reboot_instance(esh_driver, esh_instance, reboot_type)
            elif 'rebuild' == action:
                machine_alias = action_params.get('machine_alias', '')
                machine = esh_driver.get_machine(machine_alias)
                esh_driver.rebuild_instance(esh_instance, machine)
            else:
                return failure_response(
                    status.HTTP_400_BAD_REQUEST,
                    'Unable to to perform action %s.' % (action))
            #ASSERT: The action was executed successfully
            api_response = {
                'result': 'success',
                'message': 'The requested action <%s> was run successfully'
                % action_params['action'],
                'object': result_obj,
            }
            response = Response(api_response, status=status.HTTP_200_OK)
            return response
        ### Exception handling below..
        except HypervisorCapacityError, hce:
            return over_capacity(hce)
        except OverQuotaError, oqe:
            return over_quota(oqe)
        except OverAllocationError, oae:
            return over_quota(oae)
        except SizeNotAvailable, snae:
            return size_not_availabe(snae)
        except InvalidCredsError:
            return invalid_creds(provider_id, identity_id)
        except NotImplemented, ne:
            return failure_response(
                status.HTTP_404_NOT_FOUND,
                "The requested action %s is not available on this provider"
                % action_params['action'])


class Instance(APIView):
    """
    Instances are the objects created when you launch a machine. They are
    represented by a unique ID, randomly generated on launch, important
    attributes of an Instance are:
    Name, Status (building, active, suspended), Size, Machine"""
    #renderer_classes = (JSONRenderer, JSONPRenderer)

    permission_classes = (ApiAuthRequired,)
    
    def get(self, request, provider_id, identity_id, instance_id):
        """
        Authentication Required, get instance details.
        """
        user = request.user
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)
        esh_instance = esh_driver.get_instance(instance_id)
        if not esh_instance:
            return instance_not_found(instance_id)
        core_instance = convert_esh_instance(esh_driver, esh_instance,
                                             provider_id, identity_id, user)
        serialized_data = InstanceSerializer(core_instance,
                                             context={'user':request.user}).data
        response = Response(serialized_data)
        response['Cache-Control'] = 'no-cache'
        return response

    def patch(self, request, provider_id, identity_id, instance_id):
        """Authentication Required, update metadata about the instance"""
        user = request.user
        data = request.DATA
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)
        esh_instance = esh_driver.get_instance(instance_id)
        if not esh_instance:
            return instance_not_found(instance_id)
        #Gather the DB related item and update
        core_instance = convert_esh_instance(esh_driver, esh_instance,
                                             provider_id, identity_id, user)
        serializer = InstanceSerializer(core_instance, data=data,
                                        context={'user':request.user}, partial=True)
        if serializer.is_valid():
            logger.info('metadata = %s' % data)
            update_instance_metadata(esh_driver, esh_instance, data,
                    replace=False)
            serializer.save()
            response = Response(serializer.data)
            logger.info('data = %s' % serializer.data)
            response['Cache-Control'] = 'no-cache'
            return response
        else:
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST)

    def put(self, request, provider_id, identity_id, instance_id):
        """Authentication Required, update metadata about the instance"""
        user = request.user
        data = request.DATA
        #Ensure item exists on the server first
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)
        esh_instance = esh_driver.get_instance(instance_id)
        if not esh_instance:
            return instance_not_found(instance_id)
        #Gather the DB related item and update
        core_instance = convert_esh_instance(esh_driver, esh_instance,
                                             provider_id, identity_id, user)
        serializer = InstanceSerializer(core_instance, data=data,
                                        context={'user':request.user})
        if serializer.is_valid():
            logger.info('metadata = %s' % data)
            update_instance_metadata(esh_driver, esh_instance, data)
            serializer.save()
            response = Response(serializer.data)
            logger.info('data = %s' % serializer.data)
            response['Cache-Control'] = 'no-cache'
            return response
        else:
            return Response(serializer.errors, status=status.HTTP_400)

    def delete(self, request, provider_id, identity_id, instance_id):
        """Authentication Required, TERMINATE the instance.

        Be careful, there is no going back once you've deleted an instance.
        """
        user = request.user
        esh_driver = prepare_driver(request, provider_id, identity_id)
        if not esh_driver:
            return invalid_creds(provider_id, identity_id)
        try:
            esh_instance = esh_driver.get_instance(instance_id)
            if not esh_instance:
                return instance_not_found(instance_id)
            task.destroy_instance_task(esh_instance, identity_id)
            existing_instance = esh_driver.get_instance(instance_id)
            if existing_instance:
                #Instance will be deleted soon...
                esh_instance = existing_instance
                if esh_instance.extra\
                   and 'task' not in esh_instance.extra:
                    esh_instance.extra['task'] = 'queueing delete'
            core_instance = convert_esh_instance(esh_driver, esh_instance,
                                                 provider_id, identity_id,
                                                 user)
            if core_instance:
                core_instance.end_date_all()
            serialized_data = InstanceSerializer(core_instance,
                                                 context={'user':request.user}).data
            response = Response(serialized_data, status=status.HTTP_200_OK)
            response['Cache-Control'] = 'no-cache'
            return response
        except InvalidCredsError:
            return invalid_creds(provider_id, identity_id)


def valid_post_data(data):
    """
    Return any missing required post key names.
    """
    required = ['machine_alias', 'size_alias', 'name']
    #Return any keys that don't match criteria
    return [key for key in required
            #Key must exist and have a non-empty value.
            if not ( key in data and len(data[key]) > 0)]


def keys_not_found(missing_keys):
    return failure_response(
        status.HTTP_400_BAD_REQUEST,
        'Missing data for variable(s): %s' % missing_keys)


def instance_not_found(instance_id):
    return failure_response(
        status.HTTP_404_NOT_FOUND,
        'Instance %s does not exist' % instance_id)


def size_not_availabe(sna_exception):
    return failure_response(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        sna_exception.message)


def over_capacity(capacity_exception):
    return failure_response(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        capacity_exception.message)


def over_quota(quota_exception):
    return failure_response(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        quota_exception.message)


def over_allocation(allocation_exception):
    return failure_response(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        allocation_exception.message)
