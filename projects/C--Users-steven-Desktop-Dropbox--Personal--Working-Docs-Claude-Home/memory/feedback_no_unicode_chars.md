---
name: No Unicode special characters in any output
description: Never use em-dashes, the replacement character diamond-question-mark, or any non-ASCII Unicode in code strings, tooltips, comments, or any user-visible text
type: feedback
originSessionId: d30a9621-6564-49e5-a9da-77d30e322f1f
---
Never use em-dashes (---, &mdash;), the Unicode replacement character (the diamond with question mark, U+FFFD), horizontal ellipsis (..., &hellip;), or ANY non-ASCII Unicode characters in:
- JavaScript string literals that become user-visible (tooltips, title attributes, status messages, button text, error messages)
- HTML template strings in viewer.js / viewer.html
- Code comments
- Any output in any session

**Why:** These characters cause the diamond-question-mark replacement character to appear in the browser, breaking the UI. The user explicitly instructed this in session and said to memorize it.

**How to apply:** Replace with ASCII-only equivalents everywhere:
- Em-dash -- or &mdash;  ->  use  --  or  -  or  (space)
- Ellipsis ... or &hellip;  ->  use  ...
- Arrows -> <- ^ v  ->  use  ->  <-  ^  v
- Checkmark OK  ->  use  OK  or  [x]  or  done
- Hourglass [loading] or any emoji  ->  remove or use plain text like [loading]
- Any \uXXXX escape for non-ASCII  ->  use ASCII equivalent

This instruction was given explicitly and must be followed in every future session without exception.
