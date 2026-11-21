"""Problem bank: list, detail, run, submit, bookmarks, personal problem bank."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .. import execution, judge, ratelimit, sandbox
from ..models import (
    CustomProblem,
    Problem,
    ProblemBookmark,
    ProblemCategory,
    Submission,
)
from .common import (
    BadRequest,
    api_login_required,
    api_view,
    json_body,
    json_error,
    limited,
    text_field,
)
from .pages import job_owner

PAGE_SIZE = 20
MAX_CUSTOM_PROBLEMS = 200


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _user_progress(user):
    """(ids of solved problems, ids of attempted problems) for ``user``."""
    if not user.is_authenticated:
        return set(), set()
    rows = Submission.objects.filter(user=user).values_list('problem_id', 'status')
    solved, attempted = set(), set()
    for problem_id, status in rows:
        attempted.add(problem_id)
        if status == 'accepted':
            solved.add(problem_id)
    return solved, attempted


def problem_markdown(problem, samples):
    """The statement as one Markdown document (what a room's problem panel shows)."""
    parts = ['# %s' % problem.title, '', problem.description.strip()]
    for heading, body in (('输入格式', problem.input_format), ('输出格式', problem.output_format),
                          ('数据范围', problem.constraints)):
        if body.strip():
            parts += ['', '## %s' % heading, '', body.strip()]
    for index, case in enumerate(samples, 1):
        parts += ['', '## 示例 %d' % index, '', '输入：', '', '```', case.input_data.rstrip('\n'), '```',
                  '', '输出：', '', '```', case.expected_output.rstrip('\n'), '```']
    return '\n'.join(parts) + '\n'


def _submission_payload(submission):
    return {
        'success': True,
        'submission_id': submission.pk,
        'status': submission.status,
        'status_display': submission.get_status_display(),
        'done': submission.is_final,
        'score': submission.score,
        'total_score': submission.total_score,
        'execution_time': submission.execution_time,
        'memory_used': submission.memory_used,
        'test_results': submission.get_test_results(),
        'error_message': submission.error_message,
        'language': submission.language,
    }


def _program_from(data):
    language = sandbox.get_language(text_field(data, 'language', 40, 'python'))
    if language is None or not sandbox.find_binary(language):
        raise BadRequest('服务器不支持这种语言')
    code = text_field(data, 'code', execution.MAX_CODE_CHARS)
    if not code.strip():
        raise BadRequest('代码不能为空')
    return language, code


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@never_cache
@ensure_csrf_cookie
def problem_list(request):
    search = request.GET.get('search', '').strip()
    difficulty = request.GET.get('difficulty', '')
    category_id = request.GET.get('category', '')
    status = request.GET.get('status', '')
    tag = request.GET.get('tag', '').strip() or request.GET.get('tags', '').strip()

    problems = Problem.objects.filter(status='published').select_related('category')
    if search:
        problems = problems.filter(
            Q(title__icontains=search) | Q(description__icontains=search) | Q(tags__icontains=search))
    if difficulty in ('easy', 'medium', 'hard'):
        problems = problems.filter(difficulty=difficulty)
    if category_id.isdigit():
        problems = problems.filter(category_id=int(category_id))
    if tag:
        problems = problems.filter(tags__icontains=tag)

    solved, attempted = _user_progress(request.user)
    if request.user.is_authenticated:
        if status == 'solved':
            problems = problems.filter(id__in=solved)
        elif status == 'attempted':
            problems = problems.filter(id__in=attempted - solved)
        elif status == 'todo':
            problems = problems.exclude(id__in=attempted)

    page = Paginator(problems, PAGE_SIZE).get_page(request.GET.get('page', 1))
    for problem in page.object_list:
        problem.user_status = (
            'solved' if problem.id in solved else ('attempted' if problem.id in attempted else ''))

    published = Problem.objects.filter(status='published')
    return render(request, 'editor/problem_list.html', {
        'page_obj': page,
        'categories': ProblemCategory.objects.all(),
        'filters': {
            'search': search, 'difficulty': difficulty, 'category': category_id,
            'status': status, 'tag': tag,
        },
        'has_filters': bool(search or difficulty or category_id or status or tag),
        'totals': {
            'all': published.count(),
            'solved': len(solved),
            'easy': published.filter(difficulty='easy').count(),
            'medium': published.filter(difficulty='medium').count(),
            'hard': published.filter(difficulty='hard').count(),
        },
    })


@never_cache
@ensure_csrf_cookie
def problem_detail(request, slug):
    problem = get_object_or_404(Problem.objects.select_related('category'), slug=slug, status='published')
    samples = list(problem.test_cases.filter(is_sample=True))
    user_submissions = []
    user_status = ''
    is_bookmarked = False
    if request.user.is_authenticated:
        user_submissions = list(
            Submission.objects.filter(user=request.user, problem=problem)[:10])
        if any(submission.status == 'accepted' for submission in user_submissions) or \
                Submission.objects.filter(user=request.user, problem=problem, status='accepted').exists():
            user_status = 'solved'
        elif user_submissions:
            user_status = 'attempted'
        is_bookmarked = ProblemBookmark.objects.filter(user=request.user, problem=problem).exists()

    published = Problem.objects.filter(status='published')
    previous_problem = published.filter(
        Q(order__lt=problem.order) | Q(order=problem.order, id__lt=problem.id)).order_by('-order', '-id').first()
    next_problem = published.filter(
        Q(order__gt=problem.order) | Q(order=problem.order, id__gt=problem.id)).order_by('order', 'id').first()

    languages = sandbox.language_catalog()
    return render(request, 'editor/problem_detail.html', {
        'problem': problem,
        'samples': samples,
        'user_submissions': user_submissions,
        'user_status': user_status,
        'is_bookmarked': is_bookmarked,
        'previous_problem': previous_problem,
        'next_problem': next_problem,
        'languages': languages,
        'problem_config': {
            'slug': problem.slug,
            'title': problem.title,
            'languages': languages,
            'defaultLanguage': sandbox.normalize_language(problem.default_language),
            'starterCode': problem.get_starter_code(),
            'samples': [{'input': case.input_data, 'output': case.expected_output} for case in samples],
            'timeLimit': problem.time_limit,
            'memoryLimit': problem.memory_limit,
            'authenticated': request.user.is_authenticated,
            'bookmarked': is_bookmarked,
        },
    })


@never_cache
@login_required
def my_submissions(request):
    status = request.GET.get('status', '')
    problem_slug = request.GET.get('problem', '').strip()
    submissions = Submission.objects.filter(user=request.user).select_related('problem')
    if status in dict(Submission.STATUS_CHOICES):
        submissions = submissions.filter(status=status)
    if problem_slug:
        submissions = submissions.filter(problem__slug=problem_slug)
    page = Paginator(submissions, PAGE_SIZE).get_page(request.GET.get('page', 1))
    return render(request, 'editor/my_submissions.html', {
        'page_obj': page,
        'status': status,
        'problem_slug': problem_slug,
        'status_choices': [choice for choice in Submission.STATUS_CHOICES
                           if choice[0] in Submission.FINAL_STATUSES],
    })


@never_cache
@login_required
def my_bookmarks(request):
    bookmarks = list(ProblemBookmark.objects.filter(user=request.user).select_related('problem', 'problem__category'))
    solved, attempted = _user_progress(request.user)
    for bookmark in bookmarks:
        bookmark.user_status = (
            'solved' if bookmark.problem_id in solved
            else ('attempted' if bookmark.problem_id in attempted else ''))
    return render(request, 'editor/bookmarks.html', {'bookmarks': bookmarks})


# --------------------------------------------------------------------------
# Running and judging
# --------------------------------------------------------------------------

@require_POST
@api_view
def run_problem(request, slug):
    """Run the code on custom input (no verdict). Returns a job id to poll."""
    problem = get_object_or_404(Problem, slug=slug, status='published')
    if not ratelimit.allow('run_per_ip', ratelimit.client_ip(request)):
        return json_error('运行得太频繁了，请稍后再试', 429)
    data = json_body(request)
    language, code = _program_from(data)
    stdin_text = text_field(data, 'input', execution.MAX_STDIN_CHARS)
    limits = judge.limits_for(problem, language)
    try:
        job_id = execution.start_job(
            job_owner(request), execution.run_program, language.key, code, stdin_text, limits)
    except execution.QueueFull:
        return json_error('服务器正忙，请稍后再试', 503)
    return JsonResponse({'success': True, 'job_id': job_id})


@require_POST
@api_view
@api_login_required
@limited('submit_per_user', lambda request: request.user.pk)
def submit_code(request, slug):
    problem = get_object_or_404(Problem, slug=slug, status='published')
    language, code = _program_from(json_body(request))
    submission = Submission.objects.create(
        user=request.user, problem=problem, code=code, language=language.key, status='pending')
    judge.enqueue(submission)
    submission.refresh_from_db()
    return JsonResponse(_submission_payload(submission))


@never_cache
@require_GET
@api_login_required
def submission_result(request, submission_id):
    submission = get_object_or_404(Submission, pk=submission_id)
    if submission.user_id != request.user.pk and not request.user.is_staff:
        return json_error('没有权限查看这次提交', 403)
    submission = judge.recover_if_stale(submission)
    payload = _submission_payload(submission)
    if request.GET.get('code'):
        payload['code'] = submission.code
    return JsonResponse(payload)


@require_POST
@api_login_required
def toggle_bookmark(request, slug):
    problem = get_object_or_404(Problem, slug=slug, status='published')
    bookmark, created = ProblemBookmark.objects.get_or_create(user=request.user, problem=problem)
    if not created:
        bookmark.delete()
    return JsonResponse({'success': True, 'action': 'bookmarked' if created else 'unbookmarked',
                         'bookmarked': created})


# --------------------------------------------------------------------------
# JSON for the room's "pick a problem" dialog
# --------------------------------------------------------------------------

@never_cache
@require_GET
def api_problem_list(request):
    search = request.GET.get('q', '').strip()
    problems = Problem.objects.filter(status='published').select_related('category')
    if search:
        problems = problems.filter(Q(title__icontains=search) | Q(tags__icontains=search))
    difficulty = request.GET.get('difficulty', '')
    if difficulty in ('easy', 'medium', 'hard'):
        problems = problems.filter(difficulty=difficulty)
    items = [{
        'slug': problem.slug,
        'title': problem.title,
        'difficulty': problem.difficulty,
        'difficulty_display': problem.get_difficulty_display(),
        'category': problem.category.name if problem.category else '',
        'tags': problem.tag_list(),
    } for problem in problems[:100]]
    return JsonResponse({'success': True, 'problems': items})


@never_cache
@require_GET
def api_problem_detail(request, slug):
    problem = get_object_or_404(Problem, slug=slug, status='published')
    samples = list(problem.test_cases.filter(is_sample=True))
    return JsonResponse({'success': True, 'problem': {
        'slug': problem.slug,
        'title': problem.title,
        'difficulty': problem.difficulty,
        'markdown': problem_markdown(problem, samples),
        'starter_code': problem.get_starter_code(),
        'sample_input': samples[0].input_data if samples else '',
    }})


# --------------------------------------------------------------------------
# Personal problem bank (interviewers keep their own questions)
# --------------------------------------------------------------------------

def _custom_payload(problem, full=False):
    data = {
        'id': problem.pk,
        'name': problem.name,
        'title': problem.title,
        'language': problem.language,
        'updated_at': problem.updated_at.strftime('%Y-%m-%d %H:%M'),
    }
    if full:
        data.update({
            'description': problem.description,
            'input_format': problem.input_format,
            'output_format': problem.output_format,
            'constraints': problem.constraints,
            'code': problem.code,
            'input_text': problem.input_text,
            'output_text': problem.output_text,
        })
    return data


@require_POST
@api_view
@api_login_required
def save_custom_problem(request):
    data = json_body(request)
    name = text_field(data, 'name', 255).strip()
    if not name:
        return json_error('题目名称不能为空')
    language = sandbox.normalize_language(text_field(data, 'language', 40, 'python')) or 'python'
    values = {
        'title': text_field(data, 'title', 255).strip(),
        'description': text_field(data, 'description', 100000),
        'input_format': text_field(data, 'input_format', 20000),
        'output_format': text_field(data, 'output_format', 20000),
        'constraints': text_field(data, 'constraints', 20000),
        'code': text_field(data, 'code', execution.MAX_CODE_CHARS),
        'language': language,
        'input_text': text_field(data, 'input_text', execution.MAX_STDIN_CHARS),
        'output_text': text_field(data, 'output_text', execution.MAX_STDIN_CHARS),
    }
    existing = CustomProblem.objects.filter(user=request.user, name=name).first()
    if existing is None and CustomProblem.objects.filter(user=request.user).count() >= MAX_CUSTOM_PROBLEMS:
        return json_error('个人题库最多保存 %d 道题' % MAX_CUSTOM_PROBLEMS)
    problem, created = CustomProblem.objects.update_or_create(
        user=request.user, name=name, defaults=values)
    return JsonResponse({'success': True, 'id': problem.pk, 'created': created,
                         'message': '题目已保存' if created else '题目已更新'})


@never_cache
@require_GET
@api_login_required
def get_custom_problems(request):
    problems = CustomProblem.objects.filter(user=request.user)
    return JsonResponse({'success': True, 'problems': [_custom_payload(problem) for problem in problems]})


@never_cache
@require_GET
@api_login_required
def load_custom_problem(request, problem_id):
    problem = get_object_or_404(CustomProblem, pk=problem_id, user=request.user)
    return JsonResponse({'success': True, 'problem': _custom_payload(problem, full=True)})


@require_POST
@api_login_required
def delete_custom_problem(request, problem_id):
    problem = get_object_or_404(CustomProblem, pk=problem_id, user=request.user)
    problem.delete()
    return JsonResponse({'success': True, 'message': '题目已删除'})
