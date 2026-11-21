from django.urls import path, re_path

from .views import auth, membership, pages, problems

ROOM = r'(?P<room_name>[A-Za-z0-9_-]{3,64})'

urlpatterns = [
    path('', pages.home, name='home'),
    path('new/', pages.new_room, name='new_room'),
    path('main/', pages.legacy_main),
    path('robots.txt', pages.robots_txt),
    path('healthz/', pages.healthz),

    # Accounts
    path('signup/', auth.signup, name='signup'),
    path('login/', auth.login_view, name='login'),
    path('logout/', auth.logout_view, name='logout'),
    path('get-user-info/', auth.get_user_info, name='get_user_info'),
    path('profile/', auth.user_profile, name='user_profile'),

    # Membership
    path('membership/', membership.membership_page, name='membership'),
    path('get-payment-qr/', membership.get_payment_qr, name='get_payment_qr'),
    path('check-payment-status/', membership.check_payment_status, name='check_payment_status'),
    path('purchase-membership/', membership.retired_purchase_endpoint),
    path('purchase-by-count/', membership.retired_purchase_endpoint),

    # Problem bank
    path('problems/', problems.problem_list, name='problem_list'),
    path('problem/<slug:slug>/', problems.problem_detail, name='problem_detail'),
    path('problem/<slug:slug>/run/', problems.run_problem, name='run_problem'),
    path('problem/<slug:slug>/submit/', problems.submit_code, name='submit_code'),
    path('problem/<slug:slug>/bookmark/', problems.toggle_bookmark, name='toggle_bookmark'),
    path('submission/<int:submission_id>/', problems.submission_result, name='submission_result'),
    path('my-submissions/', problems.my_submissions, name='my_submissions'),
    path('my-bookmarks/', problems.my_bookmarks, name='my_bookmarks'),

    # Personal problem bank
    path('custom-problems/', problems.get_custom_problems, name='get_custom_problems'),
    path('custom-problems/save/', problems.save_custom_problem, name='save_custom_problem'),
    path('custom-problems/<int:problem_id>/load/', problems.load_custom_problem, name='load_custom_problem'),
    path('custom-problems/<int:problem_id>/delete/', problems.delete_custom_problem,
         name='delete_custom_problem'),

    # JSON used by the pages
    path('api/problems/', problems.api_problem_list, name='api_problem_list'),
    path('api/problems/<slug:slug>/', problems.api_problem_detail, name='api_problem_detail'),
    path('api/jobs/<str:job_id>/', pages.job_result, name='job_result'),
    re_path(r'^api/rooms/' + ROOM + r'/pin/$', pages.pin_room, name='pin_room'),

    # Endpoints of the pre-upgrade pages
    path('execute/', pages.legacy_execute),
    re_path(r'^' + ROOM + r'/execute/$', pages.legacy_execute),

    # Interview rooms live at the top level: /<room>/  (keep this last)
    re_path(r'^' + ROOM + r'/$', pages.room, name='room'),
]
