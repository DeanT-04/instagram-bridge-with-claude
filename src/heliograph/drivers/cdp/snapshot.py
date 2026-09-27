"""Compact DOM summary for the CDP driver: visible text, links, buttons and inputs.

Designed to be small enough to hand to Claude (kilobytes, not megabytes).
"""

from __future__ import annotations

__all__ = ["SNAPSHOT_JS"]

SNAPSHOT_JS = """([maxText, maxNodes]) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
  const nodes = [];
  const seen = new Set();
  const push = (node) => {
    const key = node.role + '|' + node.name + '|' + (node.href || '');
    if (!node.name && !node.href) return;
    if (seen.has(key) || nodes.length >= maxNodes) return;
    seen.add(key); nodes.push(node);
  };
  for (const el of document.querySelectorAll(
      'a[href], button, [role=button], [role=link], [role=tab], input, textarea, [role=textbox]')) {
    if (!visible(el)) continue;
    const role = el.getAttribute('role') || (el.tagName === 'A' ? 'link'
      : el.tagName === 'BUTTON' ? 'button' : 'textbox');
    const name = clean(el.getAttribute('aria-label') || el.innerText || el.getAttribute('title')
      || el.getAttribute('placeholder') || (el.querySelector('img[alt]') || {}).alt);
    const node = {role, name};
    if (el.tagName === 'A') {
      try { node.href = new URL(el.getAttribute('href'), location.href).pathname; } catch (e) {}
    }
    if (el.getAttribute('aria-pressed')) node.pressed = el.getAttribute('aria-pressed');
    push(node);
  }
  const main = document.querySelector('main') || document.body;
  const text = main ? main.innerText : '';
  return {url: location.href, title: document.title,
          text: (text || '').replace(/\\n{2,}/g, '\\n').slice(0, maxText),
          nodes, truncated: nodes.length >= maxNodes};
}"""
