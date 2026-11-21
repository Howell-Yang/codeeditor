from django.contrib import admin

from .models import (
    CustomProblem,
    Membership,
    Order,
    Problem,
    ProblemBookmark,
    ProblemCategory,
    RoomState,
    Submission,
    TestCase,
)


@admin.register(ProblemCategory)
class ProblemCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'order', 'created_at']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['order', 'name']


class TestCaseInline(admin.TabularInline):
    model = TestCase
    extra = 1
    fields = ['input_data', 'expected_output', 'is_sample', 'is_hidden', 'points', 'order']
    ordering = ['order']


@admin.register(Problem)
class ProblemAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'difficulty', 'status', 'category', 'order',
        'accepted_count', 'submission_count', 'created_at'
    ]
    list_filter = ['difficulty', 'status', 'category']
    list_editable = ['status', 'order']
    search_fields = ['title', 'description', 'tags']
    prepopulated_fields = {'slug': ('title',)}
    ordering = ['order', 'id']
    inlines = [TestCaseInline]
    readonly_fields = ['accepted_count', 'submission_count']
    fieldsets = (
        ('基本信息', {
            'fields': ('title', 'slug', 'description', 'category', 'tags', 'order')
        }),
        ('题目详情', {
            'fields': ('input_format', 'output_format', 'constraints', 'hint')
        }),
        ('判题设置', {
            'fields': ('difficulty', 'status', 'default_language', 'time_limit', 'memory_limit')
        }),
        ('代码模板', {
            'fields': ('template_code', 'starter_code'),
            'classes': ('collapse',)
        }),
        ('参考解答', {
            'fields': ('solution',),
            'classes': ('collapse',)
        }),
        ('统计信息', {
            'fields': ('accepted_count', 'submission_count', 'created_by'),
        }),
    )


@admin.register(TestCase)
class TestCaseAdmin(admin.ModelAdmin):
    list_display = ['id', 'problem', 'is_sample', 'is_hidden', 'points', 'order']
    list_filter = ['is_sample', 'is_hidden', 'problem']
    search_fields = ['problem__title', 'input_data']
    ordering = ['problem', 'order']


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'problem', 'language', 'status',
        'execution_time', 'memory_used', 'score', 'submitted_at'
    ]
    list_filter = ['status', 'language', 'submitted_at']
    search_fields = ['user__username', 'problem__title']
    readonly_fields = ['execution_time', 'memory_used', 'score', 'total_score', 'test_results',
                       'submitted_at', 'judged_at']
    raw_id_fields = ['user', 'problem']
    ordering = ['-submitted_at']
    date_hierarchy = 'submitted_at'


@admin.register(ProblemBookmark)
class ProblemBookmarkAdmin(admin.ModelAdmin):
    list_display = ['user', 'problem', 'created_at']
    search_fields = ['user__username', 'problem__title']
    raw_id_fields = ['user', 'problem']
    ordering = ['-created_at']


@admin.register(CustomProblem)
class CustomProblemAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'language', 'updated_at']
    search_fields = ['name', 'title', 'user__username']
    raw_id_fields = ['user']


@admin.register(RoomState)
class RoomStateAdmin(admin.ModelAdmin):
    list_display = ['room_name', 'language', 'is_vip', 'owner', 'updated_at']
    search_fields = ['room_name']
    list_filter = ['language', 'is_vip']
    raw_id_fields = ['owner']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-updated_at']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'payment_id', 'user', 'purchase_type', 'amount',
        'status', 'created_at', 'paid_at'
    ]
    list_filter = ['status', 'purchase_type', 'created_at']
    search_fields = ['payment_id', 'user__username']
    readonly_fields = ['payment_id', 'created_at', 'paid_at', 'balance_before']
    raw_id_fields = ['user']
    ordering = ['-created_at']


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ['user', 'start_date', 'end_date', 'remaining_rooms', 'is_active']
    list_filter = ['end_date']
    search_fields = ['user__username']
    readonly_fields = ['start_date']
    raw_id_fields = ['user']
