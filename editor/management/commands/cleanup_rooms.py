from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from editor.models import RoomState


class Command(BaseCommand):
    help = (
        'Delete interview rooms that nobody has touched for a while. Rooms pinned by a '
        'member (is_vip) are never deleted. Meant to be run from cron.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None,
                            help='idle days before a room is deleted (default: ROOM_RETENTION_DAYS)')
        parser.add_argument('--dry-run', action='store_true', help='only report what would be deleted')

    def handle(self, *args, **options):
        days = options['days'] if options['days'] is not None else settings.ROOM_RETENTION_DAYS
        if days < 1:
            self.stderr.write('Refusing to clean up with less than one day of retention.')
            return
        cutoff = timezone.now() - timedelta(days=days)
        stale = RoomState.objects.filter(is_vip=False, updated_at__lt=cutoff)
        count = stale.count()
        if options['dry_run']:
            self.stdout.write('%d rooms idle for more than %d days would be deleted.' % (count, days))
            return
        stale.delete()
        self.stdout.write(self.style.SUCCESS('Deleted %d rooms idle for more than %d days.' % (count, days)))
