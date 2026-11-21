import json
import random
import time
import uuid

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models import F
from django.db.utils import IntegrityError
from django.utils import timezone


# ==================== Interview rooms ====================

class RoomState(models.Model):
    """Persisted content of an interview room.

    The live state (revision history, cursors, participants) is kept in memory
    by ``editor.rooms``; this row is what survives after everybody left.
    """
    room_name = models.CharField(max_length=255, unique=True)
    code = models.TextField(default='')
    language = models.CharField(max_length=50, default='python')
    font_size = models.CharField(max_length=10, default='14px')
    input_text = models.TextField(default='')
    problem_text = models.TextField(default='')
    output_text = models.TextField(default='')
    # Pinned by a member: kept forever, never removed by ``cleanup_rooms``.
    is_vip = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pinned_rooms',
    )
    # JSON with small shared extras (interview timer, ...).
    meta = models.TextField(default='', blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        verbose_name = '面试房间'
        verbose_name_plural = '面试房间'

    def __str__(self):
        return self.room_name

    def get_meta(self):
        if not self.meta:
            return {}
        try:
            data = json.loads(self.meta)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


# ==================== Membership & orders ====================

class Order(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    payment_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True
    )
    purchase_type = models.CharField(
        max_length=10,
        choices=(('count', '按次'), ('yearly', '按年'))
    )
    quantity = models.PositiveIntegerField(null=True, blank=True)
    duration_years = models.PositiveIntegerField(null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=10,
        choices=(
            ('pending', '待支付'),
            ('paid', '已支付'),
            ('failed', '支付失败')
        ),
        default='pending'
    )
    # Account balance recorded by the server when the order was created. The
    # order counts as paid once the balance has grown by ``amount``.
    balance_before = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '订单'
        verbose_name_plural = '订单'

    def __str__(self):
        return "%s - %s" % (self.user, self.payment_id)

    @classmethod
    def generate_payment_id(cls):
        """Unique order number: timestamp + UUID fragment + random digits."""
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        uuid_part = uuid.uuid4().hex[:8].upper()
        random_part = "%04d" % random.randint(1000, 9999)
        return "%s_%s_%s" % (timestamp, uuid_part, random_part)

    @classmethod
    def create_order_with_retry(cls, user, max_retries=3, **kwargs):
        """Create an order, retrying on the (very unlikely) id collision."""
        last_error = None
        for attempt in range(max_retries):
            try:
                with transaction.atomic():
                    return cls.objects.create(
                        user=user,
                        payment_id=cls.generate_payment_id(),
                        **kwargs
                    )
            except IntegrityError as exc:
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
        raise RuntimeError("Failed to create order after %d attempts: %s" % (max_retries, last_error))


class Membership(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)
    # Credits for pinning rooms; new accounts get five.
    remaining_rooms = models.IntegerField(default=5)

    class Meta:
        verbose_name = '会员'
        verbose_name_plural = '会员'

    def __str__(self):
        return "%s (%s)" % (self.user, self.get_status())

    def has_time_plan(self):
        return bool(self.end_date and self.end_date > timezone.now())

    def is_active(self):
        return self.has_time_plan() or self.remaining_rooms > 0
    is_active.boolean = True
    is_active.short_description = '有效'

    def get_status(self):
        if self.has_time_plan():
            return timezone.localtime(self.end_date).strftime('%Y-%m-%d') + "到期"
        if self.remaining_rooms > 0:
            return "{}个房间".format(self.remaining_rooms)
        return "已过期"

    def consume_room(self):
        """Spend what pinning one room costs. Returns False when nothing is left.

        A time based plan pins for free; otherwise one room credit is used. The
        decrement is done in the database so that two requests cannot spend the
        same credit.
        """
        if self.has_time_plan():
            return True
        updated = Membership.objects.filter(
            pk=self.pk, remaining_rooms__gt=0
        ).update(remaining_rooms=F('remaining_rooms') - 1)
        if updated:
            self.refresh_from_db(fields=['remaining_rooms'])
            return True
        return False


# ==================== Problem bank ====================

class ProblemCategory(models.Model):
    """题目分类"""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='children'
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = '题目分类'
        verbose_name_plural = '题目分类'

    def __str__(self):
        return self.name


class Problem(models.Model):
    """编程题目"""
    DIFFICULTY_CHOICES = [
        ('easy', '简单'),
        ('medium', '中等'),
        ('hard', '困难'),
    ]

    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('published', '已发布'),
        ('archived', '已归档'),
    ]

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(help_text="题目描述，支持 Markdown 格式")
    input_format = models.TextField(blank=True, help_text="输入格式说明")
    output_format = models.TextField(blank=True, help_text="输出格式说明")
    constraints = models.TextField(blank=True, help_text="数据约束说明")
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default='easy')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    category = models.ForeignKey(
        ProblemCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='problems'
    )
    tags = models.CharField(max_length=500, blank=True, help_text="标签，用逗号分隔")
    hint = models.TextField(blank=True, help_text="解题提示")
    solution = models.TextField(blank=True, help_text="参考解答（仅管理员可见）")
    template_code = models.TextField(blank=True, help_text="Python 代码模板")
    starter_code = models.TextField(
        blank=True,
        help_text='各语言代码模板的 JSON，例如 {"python": "...", "cpp": "..."}'
    )
    default_language = models.CharField(max_length=50, default='python')
    time_limit = models.PositiveIntegerField(default=1000, help_text="时间限制 (ms)，以 C/C++ 为基准")
    memory_limit = models.PositiveIntegerField(default=256, help_text="内存限制 (MB)")
    order = models.PositiveIntegerField(default=0, db_index=True, help_text="列表中的排序，小的在前")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_count = models.PositiveIntegerField(default=0)
    submission_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_problems'
    )

    class Meta:
        ordering = ['order', 'id']
        verbose_name = '编程题目'
        verbose_name_plural = '编程题目'

    def __str__(self):
        return "[%s] %s" % (self.get_difficulty_display(), self.title)

    def tag_list(self):
        raw = self.tags.replace('，', ',')
        return [tag.strip() for tag in raw.split(',') if tag.strip()]

    def get_starter_code(self):
        """Per-language starter code: ``starter_code`` JSON plus the legacy field."""
        result = {}
        if self.starter_code:
            try:
                data = json.loads(self.starter_code)
            except ValueError:
                data = {}
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, str):
                        result[str(key)] = value
        if self.template_code and 'python' not in result:
            result['python'] = self.template_code
        return result

    def acceptance_rate(self):
        if not self.submission_count:
            return None
        return 100.0 * self.accepted_count / self.submission_count


class TestCase(models.Model):
    """测试用例"""
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        related_name='test_cases'
    )
    input_data = models.TextField(blank=True, help_text="输入数据")
    expected_output = models.TextField(blank=True, help_text="期望输出")
    is_sample = models.BooleanField(default=False, help_text="是否为示例（在题面中展示）")
    is_hidden = models.BooleanField(default=True, help_text="是否隐藏输入输出（判题结果只显示通过与否）")
    points = models.PositiveIntegerField(default=1, help_text="该测试用例的分数")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = '测试用例'
        verbose_name_plural = '测试用例'

    def __str__(self):
        return "TestCase #%s for %s" % (self.pk, self.problem.title)


class Submission(models.Model):
    """代码提交记录"""
    STATUS_CHOICES = [
        ('pending', '等待判题'),
        ('judging', '判题中'),
        ('accepted', '通过'),
        ('wrong_answer', '答案错误'),
        ('time_limit_exceeded', '超时'),
        ('memory_limit_exceeded', '超内存'),
        ('output_limit_exceeded', '输出超限'),
        ('runtime_error', '运行错误'),
        ('compilation_error', '编译错误'),
        ('system_error', '系统错误'),
    ]
    FINAL_STATUSES = (
        'accepted', 'wrong_answer', 'time_limit_exceeded', 'memory_limit_exceeded',
        'output_limit_exceeded', 'runtime_error', 'compilation_error', 'system_error',
    )

    LANGUAGE_CHOICES = [
        ('python', 'Python 3'),
        ('c', 'C'),
        ('cpp', 'C++'),
        ('javascript', 'JavaScript'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='submissions'
    )
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        related_name='submissions'
    )
    code = models.TextField()
    language = models.CharField(max_length=50, choices=LANGUAGE_CHOICES, default='python')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending', db_index=True)
    execution_time = models.PositiveIntegerField(null=True, blank=True, help_text="最慢用例的 CPU 时间 (ms)")
    memory_used = models.PositiveIntegerField(null=True, blank=True, help_text="内存峰值 (KB)")
    score = models.PositiveIntegerField(default=0)
    total_score = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    test_results = models.TextField(blank=True, help_text="各测试用例结果的 JSON")
    submitted_at = models.DateTimeField(auto_now_add=True)
    judged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = '代码提交'
        verbose_name_plural = '代码提交'
        indexes = [
            models.Index(fields=['user', 'problem'], name='submission_user_problem'),
        ]

    def __str__(self):
        return "Submission #%s by %s - %s" % (
            self.pk,
            self.user.username if self.user else 'Anonymous',
            self.get_status_display(),
        )

    @property
    def is_final(self):
        return self.status in self.FINAL_STATUSES

    def get_test_results(self):
        if not self.test_results:
            return []
        try:
            data = json.loads(self.test_results)
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def memory_mb(self):
        if self.memory_used is None:
            return None
        return self.memory_used / 1024.0


class ProblemBookmark(models.Model):
    """题目收藏"""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'problem']
        ordering = ['-created_at']
        verbose_name = '题目收藏'
        verbose_name_plural = '题目收藏'

    def __str__(self):
        return "%s bookmarked %s" % (self.user.username, self.problem.title)


class CustomProblem(models.Model):
    """用户自己的题库：面试官把房间里的题目和参考代码存下来，下次直接调用。"""
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='custom_problems'
    )
    name = models.CharField(max_length=255, help_text="题目名称")
    title = models.CharField(max_length=255, blank=True, help_text="题目标题")
    description = models.TextField(blank=True, help_text="题目描述 (Markdown)")
    input_format = models.TextField(blank=True, help_text="输入格式")
    output_format = models.TextField(blank=True, help_text="输出格式")
    constraints = models.TextField(blank=True, help_text="数据约束")
    code = models.TextField(default='', blank=True, help_text="起始代码/参考解答")
    language = models.CharField(max_length=50, default='python')
    input_text = models.TextField(blank=True, help_text="示例输入")
    output_text = models.TextField(blank=True, help_text="示例输出")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-created_at']
        verbose_name = '自定义题目'
        verbose_name_plural = '自定义题目'
        unique_together = ['user', 'name']

    def __str__(self):
        return "%s's problem: %s" % (self.user.username, self.name)
