"""Markdown rendering with LaTeX math, for two surfaces.

- ``ui_md``: emits MathJax delimiters (\\(...\\), \\[...\\]); the browser typesets.
- ``render_email_depth``: emails can't run JS, so each math span is rendered to a
  small PNG via matplotlib mathtext and embedded as an inline cid image. Falls
  back to raw LaTeX text if a span can't be rendered (e.g. unsupported macros).

The ``dollarmath`` plugin parses $...$/$$...$$ first so markdown can't mangle the
math (e.g. turn ``x_i`` into emphasis).
"""

from __future__ import annotations

import io
from html import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from markdown_it import MarkdownIt  # noqa: E402
from mdit_py_plugins.dollarmath import dollarmath_plugin  # noqa: E402


def _base_md() -> MarkdownIt:
    return MarkdownIt("commonmark", {"breaks": True, "html": False}).use(dollarmath_plugin)


# --------------------------------------------------------------------------- #
# UI: MathJax delimiters
# --------------------------------------------------------------------------- #
def _ui_inline(self, tokens, idx, options, env):
    return r"\(" + tokens[idx].content + r"\)"


def _ui_block(self, tokens, idx, options, env):
    return r"\[" + tokens[idx].content + r"\]" + "\n"


ui_md = _base_md()
ui_md.add_render_rule("math_inline", _ui_inline)
ui_md.add_render_rule("math_block", _ui_block)


def render_ui(md_text: str) -> str:
    return ui_md.render(md_text or "")


# --------------------------------------------------------------------------- #
# Email: math -> inline PNG images
# --------------------------------------------------------------------------- #
def latex_to_png(latex: str, fontsize: int = 16) -> bytes:
    fig = plt.figure()
    t = fig.text(0, 0, f"${latex}$", fontsize=fontsize)
    fig.canvas.draw()
    bbox = t.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, transparent=True,
                bbox_inches=bbox, pad_inches=0.02)
    plt.close(fig)
    return buf.getvalue()


def _email_img(latex: str, env: dict, display: bool) -> str:
    imgs = env.setdefault("math_images", [])
    try:
        png = latex_to_png(latex, fontsize=18 if display else 15)
    except Exception:
        plt.close("all")
        return f"<code>{escape(latex)}</code>"  # graceful fallback
    cid = f"math{len(imgs)}"
    imgs.append((cid, png))
    if display:
        return (f'<div style="text-align:center;margin:12px 0">'
                f'<img src="cid:{cid}" style="max-width:92%" alt="{escape(latex)}"/></div>')
    return (f'<img src="cid:{cid}" style="height:1.15em;vertical-align:-0.25em" '
            f'alt="{escape(latex)}"/>')


def _email_inline(self, tokens, idx, options, env):
    return _email_img(tokens[idx].content, env, display=False)


def _email_block(self, tokens, idx, options, env):
    return _email_img(tokens[idx].content, env, display=True)


email_md = _base_md()
email_md.add_render_rule("math_inline", _email_inline)
email_md.add_render_rule("math_block", _email_block)


def render_email_depth(md_text: str) -> tuple[str, list[tuple[str, bytes]]]:
    """Return (html, [(cid, png_bytes), ...]) for the email body."""
    env: dict = {}
    html = email_md.render(md_text or "", env)
    return html, env.get("math_images", [])
