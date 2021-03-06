"""
CAS authentication protocol

Contact:        Steven Gregory <esteve@iplantcollaborative.org>
                J. Matt Peterson <jmatt@iplantcollaborative.org>

"""
from datetime import timedelta
import time

from django.utils import timezone
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from core.models import AtmosphereUser as User

import caslib

from threepio import logger

from atmosphere import settings

from authentication import createAuthToken
from authentication.models import UserProxy

#TODO: Find out the actual proxy ticket expiration time, it varies by server
#May be as short as 5min!
PROXY_TICKET_EXPIRY = timedelta(days=1)


def cas_validateUser(username):
    """
    Because this is a programmatic request
    and CAS requires user input when expired,
    We MUST use CAS Proxy Service,
    and see if we can reauthenticate the user.
    """
    try:
        userProxy = UserProxy.objects.filter(username=username).latest('pk')
        logger.debug("[CAS] Validation Test - %s" % username)
        if userProxy is None:
            logger.debug("User %s does not have a proxy" % username)
            return (False, None)
        proxyTicket = userProxy.proxyTicket
        (validUser, cas_response) = caslib.cas_reauthenticate(username,
                                                              proxyTicket)
        logger.debug("Valid User: %s Proxy response: %s"
                     % (validUser, cas_response))
        return (validUser, cas_response)
    except Exception, e:
        logger.exception('Error validating user %s' % username)
        return (False, None)


def parse_cas_response(cas_response):
    xml_root_dict = cas_response.map
    logger.info(xml_root_dict)
    #A Success responses will return a dict
    #failed responses will be replaced by an empty dict
    xml_response_dict = xml_root_dict.get(cas_response.type, {})
    user = xml_response_dict.get('user', None)
    pgtIOU = xml_response_dict.get('proxyGrantingTicket', None)
    return (user, pgtIOU)


def updateUserProxy(user, pgtIou, max_try=3):
    attempts = 0
    while attempts < max_try:
        try:
            #If PGTIOU exists, a UserProxy object was created
            #match the user to this ticket.
            userProxy = UserProxy.objects.get(proxyIOU=pgtIou)
            userProxy.username = user
            userProxy.expiresOn = timezone.now() + PROXY_TICKET_EXPIRY
            logger.debug("Found a matching proxy IOU for %s"
                         % userProxy.username)
            userProxy.save()
            return True
        except UserProxy.DoesNotExist:
            logger.error("Could not find UserProxy object!"
                         "ProxyIOU & ID was not saved "
                         "at proxy url endpoint.")
            time.sleep(min(2**attempts, 8))
            attempts += 1
    return False


def createSessionToken(request, auth_token):
    request.session['username'] = auth_token.user.username
    request.session['token'] = auth_token.key


"""
CAS is an optional way to login to Atmosphere
This code integrates caslib into the Auth system
"""


def cas_setReturnLocation(sendback):
    """
    Reinitialize cas with the new sendback location
    keeping all other variables the same.
    """
    caslib.cas_setServiceURL(
        settings.SERVER_URL+"/CAS_serviceValidater?sendback="+sendback
    )


def cas_validateTicket(request):
    """
    Method expects 2 GET parameters: 'ticket' & 'sendback'
    After a CAS Login:
    Redirects the request based on the GET param 'ticket'
    Unauthorized Users are redirected to '/' In the event of failure.
    Authorized Users are redirected to the GET param 'sendback'
    """

    redirect_logout_url = settings.REDIRECT_URL+"/login/"
    no_user_url = settings.REDIRECT_URL + "/no_user/"
    logger.debug('GET Variables:%s' % request.GET)
    ticket = request.GET.get('ticket', None)
    sendback = request.GET.get('sendback', None)

    if not ticket:
        logger.info("No Ticket received in GET string "
                    "-- Logout user: %s" % redirect_logout_url)
        return HttpResponseRedirect(redirect_logout_url)

    logger.debug("ServiceValidate endpoint includes a ticket."
                 " Ticket must now be validated with CAS")

    # ReturnLocation set, apply on successful authentication
    cas_setReturnLocation(sendback)
    cas_response = caslib.cas_serviceValidate(ticket)
    if not cas_response.success:
        logger.debug("CAS Server did NOT validate ticket:%s"
                     " and included this response:%s"
                     % (ticket, cas_response))
        return HttpResponseRedirect(redirect_logout_url)
    (user, pgtIou) = parse_cas_response(cas_response)

    if not user:
        logger.debug("User attribute missing from cas response!"
                     "This may require a fix to caslib.py")
        return HttpResponseRedirect(redirect_logout_url)
    if not pgtIou or pgtIou == "":
        logger.error("""Proxy Granting Ticket missing!
        Atmosphere requires CAS proxy as a service to authenticate users.
            Possible Causes:
              * ServerName variable is wrong in /etc/apache2/apache2.conf
              * Proxy URL does not exist
              * Proxy URL is not a valid RSA-2/VeriSigned SSL certificate
              * /etc/host and hostname do not match machine.""")
        return HttpResponseRedirect(redirect_logout_url)

    updated = updateUserProxy(user, pgtIou)
    if not updated:
        return HttpResponseRedirect(redirect_logout_url)
    logger.info("Updated proxy for <%s> -- Auth success!" % user)

    try:
        auth_token = createAuthToken(user)
    except User.DoesNotExist:
        return HttpResponseRedirect(no_user_url)
    if auth_token is None:
        logger.info("Failed to create AuthToken")
        HttpResponseRedirect(redirect_logout_url)
    createSessionToken(request, auth_token)
    return_to = request.GET['sendback']
    logger.info("Session token created, return to: %s" % return_to)
    return HttpResponseRedirect(return_to)


"""
CAS as a proxy service is a useful feature to renew
a users token/authentication without having to explicitly redirect the browser.
These two functions will be called
if caslib has been configured for proxy usage. (See #settings.py)
"""


def cas_storeProxyIOU_ID(request):
    """
    Any request to the proxy url will contain the PROXY-TICKET IOU and ID
    IOU and ID are mapped to a DB so they can be used later
    """
    if "pgtIou" in request.GET and "pgtId" in request.GET:
        iou_token = request.GET["pgtIou"]
        proxy_ticket = request.GET["pgtId"]
        logger.debug("PROXY HIT 2 - CAS server sends two IDs: "
                     "1.ProxyIOU (%s) 2. ProxyGrantingTicket (%s)"
                     % (iou_token, proxy_ticket))
        proxy = UserProxy(
            proxyIOU=iou_token,
            proxyTicket=proxy_ticket
        )
        proxy.save()
        logger.debug("Proxy ID has been saved, match ProxyIOU(%s) "
                     "from the proxyIOU returned in service validate."
                     % (proxy.proxyIOU,))
    else:
        logger.debug("Proxy HIT 1 - CAS server tests that this link is HTTPS")

    return HttpResponse("Received proxy request. Thank you.")


def cas_proxyCallback(request):
    """
    This is a placeholder for a proxyCallback service
    needed for CAS authentication
    """
    logger.debug("Incoming request to CASPROXY (Proxy Callback):")
    return HttpResponse("I am at a RSA-2 or VeriSigned SSL Cert. website.")


def cas_formatAttrs(cas_response):
    """
    Formats attrs into a unified dict to ease in user creation
    """
    try:
        cas_response_obj = cas_response.map[cas_response.type]
        logger.debug(cas_response_obj)
        cas_attrs = cas_response_obj['attributes']
        return cas_attrs
    except KeyError, nokey:
        logger.debug("Error retrieving attributes")
        logger.exception(nokey)
        return None
