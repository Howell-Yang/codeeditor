import datetime

from django.conf import settings


def site(request):
    """Site identity for the templates (name, footer registrations, ...)."""
    copyright_text = settings.SITE_COPYRIGHT or '© %d %s' % (datetime.date.today().year, settings.SITE_NAME)
    return {
        'SITE': {
            'name': settings.SITE_NAME,
            'tagline': settings.SITE_TAGLINE,
            'url': settings.SITE_URL,
            'copyright': copyright_text,
            'icp_number': settings.SITE_ICP_NUMBER,
            'police_number': settings.SITE_POLICE_NUMBER,
            'police_code': settings.SITE_POLICE_CODE,
            'baidu_verification': settings.BAIDU_SITE_VERIFICATION,
            'baidu_union_script': settings.BAIDU_UNION_SCRIPT,
            'repo_url': settings.SITE_REPO_URL,
            'payment_enabled': settings.PAYMENT_ENABLED,
        },
    }
