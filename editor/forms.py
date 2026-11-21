from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class SignUpForm(UserCreationForm):
    """Registration: user name and password; the e-mail address is optional."""
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get('email') or ''
        if commit:
            user.save()
        return user


class UserProfileForm(forms.ModelForm):
    """Profile editing: user name, e-mail and a display name."""

    class Meta:
        model = User
        fields = ('username', 'email', 'first_name')
        labels = {'first_name': '昵称'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'input')
        self.fields['first_name'].help_text = '进入面试房间时默认显示的名字'
