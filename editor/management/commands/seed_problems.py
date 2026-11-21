import json

from django.core.management.base import BaseCommand
from django.db import transaction

from editor import problem_seeds
from editor.models import Problem, ProblemCategory, TestCase


class Command(BaseCommand):
    help = (
        'Load the built-in problems into the problem bank. Existing problems are left '
        'alone unless --update is given.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--update', action='store_true',
            help='also overwrite the statement and the test cases of built-in problems that already exist')
        parser.add_argument(
            '--only', nargs='*', metavar='SLUG', help='only these problems')

    def handle(self, *args, **options):
        categories = {}
        for order, (slug, name, description) in enumerate(problem_seeds.CATEGORIES, 1):
            category, _ = ProblemCategory.objects.update_or_create(
                slug=slug, defaults={'name': name, 'description': description, 'order': order})
            categories[slug] = category

        created = updated = skipped = 0
        for position, spec in enumerate(problem_seeds.PROBLEMS, 1):
            if options['only'] and spec['slug'] not in options['only']:
                continue
            existing = Problem.objects.filter(slug=spec['slug']).first()
            if existing is not None and not options['update']:
                skipped += 1
                continue
            cases = problem_seeds.build_cases(spec)
            fields = {
                'title': spec['title'],
                'description': spec['description'],
                'input_format': spec['input_format'],
                'output_format': spec['output_format'],
                'constraints': spec['constraints'],
                'hint': spec.get('hint', ''),
                'difficulty': spec['difficulty'],
                'category': categories[spec['category']],
                'tags': spec['tags'],
                'solution': spec['solution'],
                'starter_code': json.dumps(problem_seeds.STARTER_CODE, ensure_ascii=False),
                'default_language': 'python',
                'time_limit': spec.get('time_limit', 1000),
                'memory_limit': spec.get('memory_limit', 256),
                'order': position * 10,
                'status': 'published',
            }
            with transaction.atomic():
                if existing is None:
                    problem = Problem.objects.create(slug=spec['slug'], **fields)
                    created += 1
                else:
                    for name, value in fields.items():
                        setattr(existing, name, value)
                    existing.save()
                    existing.test_cases.all().delete()
                    problem = existing
                    updated += 1
                TestCase.objects.bulk_create([
                    TestCase(
                        problem=problem, input_data=text, expected_output=expected,
                        is_sample=is_sample, is_hidden=not is_sample, points=1, order=index)
                    for index, (text, expected, is_sample) in enumerate(cases, 1)
                ])
            self.stdout.write('  %s %s (%d test cases)' % (
                '+' if existing is None else '~', spec['slug'], len(cases)))

        self.stdout.write(self.style.SUCCESS(
            'Problems: %d created, %d updated, %d already there.' % (created, updated, skipped)))
