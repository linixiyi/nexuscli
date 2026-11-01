---
name: web-access
description: |
  Use this skill for live web research, webpage fetching, login-state browsing, dynamic pages, social sites, and tasks that need current internet evidence.
abstract: Live web research and page fetching: web_search for discovery, web_fetch for static pages, and Chrome DevTools MCP when login state or JS rendering is needed.
overview: |
  Start from the exact user goal and identify what must be current. Use web_search for discovery and web_fetch for public static pages; switch to Chrome DevTools MCP when a page needs login state, JavaScript rendering, or browser inspection. Keep sources and dates visible when recency matters, and never guess from an empty SPA render — switch to the browser instead.
version: "1.0.0"
author: NexusCLI
tags: [web, browser, research]
---

# Web Access

Use network tools deliberately:

- Start with the exact user goal and identify what must be current.
- Use `web_search` for discovery and `web_fetch` for public static pages.
- Prefer Chrome DevTools MCP when a page needs login state, JavaScript rendering, or browser inspection.
- Keep sources and dates visible in the answer when recency matters.
- If a fetched page is empty because of SPA or anti-bot behavior, switch to browser/MCP instead of guessing.
