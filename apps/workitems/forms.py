import uuid
from datetime import datetime, time, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django import forms

from .models import WorkItem

MOSCOW = ZoneInfo('Europe/Moscow')


class ContentForm(forms.Form):
    kind = forms.ChoiceField(label='Вид', choices=WorkItem.Kind.choices)
    title = forms.CharField(label='Название', max_length=255)
    description = forms.CharField(label='Описание', required=False, max_length=10000, widget=forms.Textarea)
    due_date = forms.DateField(label='Срок выполнения', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'), help_text='До конца выбранного дня по московскому времени.')
    event_at = forms.DateTimeField(label='Дата и время события (Москва)', required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    subtasks = forms.CharField(label='Подзадачи: каждая на отдельной строке', required=False, max_length=50000, widget=forms.Textarea)

    def clean(self):
        data = super().clean()
        if data.get('kind') == 'event' and not data.get('event_at'):
            self.add_error('event_at', 'Укажите дату и время события отдельно от срока.')
        if data.get('kind') == 'task' and data.get('event_at'):
            self.add_error('event_at', 'Дата события допустима только для события.')
        lines = [line.strip() for line in data.get('subtasks', '').splitlines() if line.strip()]
        if len(lines) > 50 or any(len(line) > 1000 for line in lines):
            self.add_error('subtasks', 'Не более 50 подзадач, каждая до 1000 символов.')
        data['subtask_lines'] = lines
        return data

    def payload(self):
        data = self.cleaned_data
        return {
            'kind': data['kind'], 'title': data['title'], 'description': data['description'],
            'due_at': datetime.combine(data['due_date'], time.max, MOSCOW).astimezone(dt_timezone.utc).isoformat(),
            'event_at': data['event_at'].astimezone(dt_timezone.utc).isoformat() if data['event_at'] else None,
            'subtasks': data['subtask_lines'],
            **{name: sorted(set(str(value) for value in data[name])) for name in ('users', 'roles', 'groups')},
        }


class IssueForm(ContentForm):
    users = forms.MultipleChoiceField(label='Отдельные сотрудники', required=False, widget=forms.CheckboxSelectMultiple)
    roles = forms.MultipleChoiceField(label='Роли адресатов (ИЛИ)', required=False, widget=forms.CheckboxSelectMultiple)
    groups = forms.MultipleChoiceField(label='Группы адресатов (ИЛИ)', required=False, widget=forms.CheckboxSelectMultiple)
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, directory, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('users', 'roles', 'groups'):
            self.fields[name].choices = [(str(pk), label) for pk, label in directory[name]]


class ConfirmationForm(forms.Form):
    preview = forms.CharField(widget=forms.HiddenInput, max_length=200000)
    request_key = forms.UUIDField(widget=forms.HiddenInput)
