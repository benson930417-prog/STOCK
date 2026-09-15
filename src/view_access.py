"""Existing signed view-session contract, independent of retired accounting."""
import hashlib
import hmac
import time

AUTH_TOKEN_TTL_SECONDS = 24 * 60 * 60


def valid_session(token: str, password: str, *, now: int | None = None) -> bool:
    if not password or not isinstance(token, str):
        return False
    try:
        timestamp, signature = token.split('-', 1)
        stamp = int(timestamp)
        current = int(time.time()) if now is None else now
        if stamp > current + 60 or current - stamp > AUTH_TOKEN_TTL_SECONDS:
            return False
        expected = hmac.new(password.encode(), f'stock:{stamp}'.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(signature, expected)
    except (TypeError, ValueError):
        return False


def require_view_access() -> None:
    import streamlit as st
    try:
        password = str(st.secrets.get('VIEW_PASSWORD', '')).strip()
    except FileNotFoundError:
        password = ''
    if not password:
        return
    if valid_session(st.query_params.get('session', ''), password):
        return
    with st.form('view_access'):
        entered = st.text_input('查看密碼', type='password')
        submit = st.form_submit_button('開啟公開資料')
    if submit:
        if hmac.compare_digest(entered.encode(), password.encode()):
            stamp = int(time.time())
            signature = hmac.new(password.encode(), f'stock:{stamp}'.encode(), hashlib.sha256).hexdigest()[:32]
            st.query_params['session'] = f'{stamp}-{signature}'
            st.query_params.pop('pwd', None)
            st.rerun()
        st.error('密碼錯誤')
    st.stop()
