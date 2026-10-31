def normalize_email(email: str) -> str:
    """Email для хранения и поиска: без пробелов по краям, в нижнем регистре."""
    return email.strip().lower()
