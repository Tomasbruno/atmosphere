import os

from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.conf.urls import patterns, url, include


from rest_framework.urlpatterns import format_suffix_patterns

from api.accounts import Account
from api.application import ApplicationSearch, ApplicationList, Application
from api.email import Feedback, QuotaEmail, SupportEmail
from api.flow import Flow
from api.group import GroupList, Group
from api.identity_membership import IdentityMembershipList, IdentityMembership
from api.identity import IdentityList, Identity, IdentityDetailList
from api.instance import InstanceList, Instance,\
    InstanceAction, InstanceHistory
from api.machine import MachineList, Machine, MachineHistory,\
    MachineSearch, MachineVote
from api.machine_request import MachineRequestList, MachineRequest,\
    MachineRequestStaffList, MachineRequestStaff
from api.machine_export import MachineExportList, MachineExport
from api.maintenance import MaintenanceRecordList, MaintenanceRecord
from api.meta import Meta, MetaAction
from api.notification import NotificationList
from api.occupancy import Occupancy, Hypervisor
from api.project import ProjectList, ProjectDetail
from api.profile import Profile
from api.provider import ProviderList, Provider
from api.size import SizeList, Size
from api.hypervisor import HypervisorList, HypervisorDetail
from api.step import StepList, Step
from api.tag import TagList, Tag
from api.user import UserManagement, User
from api.version import Version
from api.volume import VolumeList, Volume

from authentication.decorators import atmo_valid_token_required

resources_path = os.path.join(os.path.dirname(__file__), 'resources')
mobile = os.path.join(os.path.dirname(__file__), 'mobile')
cloud2 = os.path.join(os.path.dirname(__file__), 'cf2')
user_match = "[A-Za-z0-9]+(?:[ _-][A-Za-z0-9]+)*)"

admin.autodiscover()
urlpatterns = patterns(
    '',

    # "The Front Door"
    url(r'^$', 'web.views.redirectApp'),

    # ADMIN Section:
    # Emulation controls for admin users
    url(r'^admin/emulate/$', 'web.views.emulate_request'),
    url(r'^admin/emulate/(?P<username>(%s)/$' % user_match, 'web.views.emulate_request'),
    # DB Admin Panel for admin users
    url(r'^admin/', include(admin.site.urls)),

    #v2 api auth by token
    url(r'^auth/$', 'authentication.views.token_auth', name='token-auth'),

    #File Retrieval:
    # Systemwide
    #TODO: Remove when using Troposphere
    url(r'^resources/(?P<path>.*)$', 'django.views.static.serve',
        {'document_root': resources_path}),

    # GLOBAL Authentication Section:
    #   Login/Logout
    url(r'^login/$', 'web.views.login'),
    url(r'^logout/$', 'web.views.logout'),
    # CAS Authentication Section:
    #    CAS Validation:
    #    Service URL validates the ticket returned after CAS login
    url(r'^CAS_serviceValidater',
        'authentication.protocol.cas.cas_validateTicket'),
    #    A valid callback URL for maintaining proxy requests
    #    This URL retrieves Proxy IOU combination
    url(r'^CAS_proxyCallback',
        'authentication.protocol.cas.cas_proxyCallback'),
    #    This URL retrieves maps Proxy IOU & ID
    url(r'^CAS_proxyUrl',
        'authentication.protocol.cas.cas_storeProxyIOU_ID'),
    #    This URL will include 'redirect' as a parameter for CAS logins
    url(r'^CASlogin/(?P<redirect>.*)$', 'authentication.cas_loginRedirect'),

    # The Front-Facing Web Application
    url(r'^application/$', 'web.views.app'),

    # Experimental UI
    # TODO: Rename to application when it launches
    # url(r'^beta/', 'web.views.app_beta'), # remove for production.
    #Partials
    url(r'^partials/(?P<path>.*)$', 'web.views.partial'),

    #Error Redirection
    url(r'^no_user/$', 'web.views.no_user_redirect'),
    #API Layer
    url(r'^api/v1/',
         include("api.urls")),

    #
    url(r'^api-docs/', include('rest_framework_swagger.urls')), 

    ### DJANGORESTFRAMEWORK ###
    url(r'^api-auth/',
        include('rest_framework.urls', namespace='rest_framework'))
)

urlpatterns += staticfiles_urlpatterns()
