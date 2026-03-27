import streamlit as st


def inject_ui_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&family=Noto+Sans+JP:wght@400;500;600;700&display=swap');
        html, body, [class*="css"] {
            font-family: 'Noto Sans JP', 'DM Sans', system-ui, -apple-system, sans-serif;
        }
        div[data-testid="stAppViewContainer"] {
            background: linear-gradient(165deg, #fbf9f6 0%, #eef4f0 45%, #e8f0eb 100%);
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f7faf8 0%, #eef5f1 100%);
            border-right: 1px solid rgba(27, 67, 50, 0.08);
        }
        .mm-hero {
            background: #ffffff;
            border-radius: 20px;
            padding: 1.75rem 2rem;
            border: 1px solid rgba(27, 67, 50, 0.06);
            box-shadow: 0 12px 40px rgba(27, 67, 50, 0.06);
            margin-bottom: 1.25rem;
        }
        .mm-muted { color: #5c6f64; font-size: 0.95rem; line-height: 1.65; }
        .mm-pill {
            display: inline-block;
            background: #d8f3dc;
            color: #1b4332;
            font-size: 0.78rem;
            font-weight: 600;
            padding: 0.2rem 0.65rem;
            border-radius: 999px;
            margin-right: 0.35rem;
        }
        h1 { letter-spacing: -0.02em; color: #1b4332 !important; }
        h2, h3 { color: #2d6a4f !important; }
        div[data-testid="stExpander"] {
            background: #fff;
            border-radius: 14px !important;
            border: 1px solid rgba(27, 67, 50, 0.08) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
