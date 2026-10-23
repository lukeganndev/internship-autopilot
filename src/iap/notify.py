"""Windows toast notifications. Falls back to printing anywhere else."""


def toast(title: str, body: str, url: str | None = None) -> None:
    try:
        from windows_toasts import Toast, WindowsToaster

        WindowsToaster("Internship Autopilot").show_toast(Toast([title, body], launch_action=url))
    except Exception:  # not Windows, or notifications disabled: never break a sourcing run over a toast
        print(f"[notify] {title}: {body} {url or ''}")
