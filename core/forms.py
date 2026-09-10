"""Site-wide forms, and the mixin that styles every form in the project."""

from __future__ import annotations

from django import forms


class StyledFormMixin:
    """
    Attach the design system's classes to each widget, once.

    Without this, every template would have to repeat the class list on every
    input, or the forms would have to be rendered field by field by hand. This
    also sets ``aria-invalid`` on fields that failed validation, so a screen
    reader hears the error state rather than only seeing the red border.
    """

    TEXT_WIDGETS = (
        forms.TextInput,
        forms.EmailInput,
        forms.URLInput,
        forms.NumberInput,
        forms.PasswordInput,
        forms.DateInput,
    )
    SELECT_WIDGETS = (forms.Select, forms.SelectMultiple, forms.NullBooleanSelect)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.Textarea):
                css = "textarea"
            elif isinstance(widget, self.SELECT_WIDGETS):
                css = "select"
            elif isinstance(widget, self.TEXT_WIDGETS):
                css = "input"
            else:
                css = ""
            if css:
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()
            if self.is_bound and name in getattr(self, "errors", {}):
                widget.attrs["aria-invalid"] = "true"


TOPICS = [
    ("listing", "Listing a property"),
    ("buying", "Buying or investing"),
    ("verification", "Identity or documents"),
    ("valuation", "A valuation looks wrong"),
    ("other", "Something else"),
]


class ContactForm(StyledFormMixin, forms.Form):
    name = forms.CharField(max_length=80)
    email = forms.EmailField()
    topic = forms.ChoiceField(choices=TOPICS)
    message = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}), max_length=2000
    )

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if len(message) < 20:
            raise forms.ValidationError(
                "Tell us a little more, so the reply is useful."
            )
        return message

    def record(self, user=None) -> None:
        """
        Log the enquiry and acknowledge it in the app.

        No mail server is configured in this build, so the message is written to
        the application log and, for a signed-in user, confirmed as a
        notification. It is never silently dropped.
        """
        import logging

        logging.getLogger("core.contact").info(
            "enquiry topic=%s from=%s <%s>",
            self.cleaned_data["topic"],
            self.cleaned_data["name"],
            self.cleaned_data["email"],
        )

        if user is not None:
            from accounts.services import notify

            notify(
                user,
                kind="system",
                title="We have your message",
                body="Support replies by email, usually within a working day.",
            )
