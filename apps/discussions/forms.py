import uuid
from django import forms


class SendForm(forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    text = forms.CharField(label='Сообщение', max_length=4000, widget=forms.Textarea(attrs={'rows': 4}), help_text='Обычный текст, до 4000 символов. Отправленное сообщение нельзя редактировать.')


class HideForm(forms.Form):
    reason = forms.CharField(label='Причина скрытия', max_length=500)


class CloseForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
